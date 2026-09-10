"""Edit.exe's chroma cleanup, ZcTaskSIMDMarble, reproduced bit for bit.

Decoded from the engine on 2026-09-11 (sony_repro/notes/measured-chroma-gap.md
2.27, reference modules in sony_repro/tools/marble_ref/, each verified
100.0000% against the engine's own buffers captured at export). The stage runs
last in the engine's chain, on the 14-bit display RGB that YCC2RGB produced,
and does the following:

1. ``gamut_fwd``: sRGB EOTF (LUT) -> 3x3 /8192 matrix -> clamp 14 bit ->
   x^(256/563) (LUT). A working space the stage converts back out of at the end.
2. RGB -> 16-bit YCC (float32, truncating): Y carries a +4096 offset, the two
   colour differences sit on 32768.
3. The chroma planes are decimated 2:1 horizontally (pair mean, truncating) and
   everything goes through a 4x4 box to 1/4 resolution.
4. ``cnr2``: a 5x5, stride-2 window mean that only admits neighbours whose Y and
   chroma are within thresholds derived from the centre's Y level and chroma
   magnitude (an edge-preserving mean, the same shape as Clarity's first pass).
5. ``cnr3``: [1 2 1;2 4 2;1 2 1]/16 blended half with the centre.
6. ``cnr4``: bilinear upsample back to the half-res chroma grid (weights sum 128,
   centred phases), with a protect term that hands strongly red pixels their
   original chroma back.
7. Nearest 2x horizontal expansion, blend with the original chroma by ``amount``
   (ISO and slider dependent, 0.5 at ISO 100 rising to 1.0 at ISO 1600), then the
   Q15 inverse YCC matrix and the inverse gamut conversion.

Everything below keeps the engine's integer and float32 semantics so the
reference tile in the test fixtures reproduces exactly. The whole frame is
processed at once; the engine works on 1080-px tiles whose 24-px margins are
discarded, and the filter's support (16 px at full res for the mean, plus the
blur and the upsampling) fits inside that margin, so whole-frame processing is
the tile result without the seams.
"""

from __future__ import annotations

import numpy as np

_f32 = np.float32

# --- gamut conversion (PIPELINE 7.9; FUN_140193530 tables reproduced in float32) ---

_GAMMA_B = _f32(2.19921875)  # 563/256 -- not 2.2; float32 floor, normaliser 16384.

M_FWD = np.array([[0x13B2, 0x0CA2, -0x0054], [0x02EC, 0x1ABD, 0x0257], [0x0073, 0x0253, 0x1D39]], np.int64)
M_INV = np.array([[0x37E2, -0x1AA4, 0x02C3], [-0x0613, 0x2976, -0x0363], [-0x0060, -0x02E3, 0x2345]], np.int64)


def _gen_lut(fn, n):
    u = np.arange(n, dtype=_f32) / _f32(16384.0)
    return np.floor(_f32(_f32(16384.0) * fn(u))).astype(np.int64)


def _srgb_eotf(u):
    return np.where(u <= _f32(0.04045), u / _f32(12.92), np.power((u + _f32(0.055)) / _f32(1.055), _f32(2.4), dtype=_f32))


def _srgb_oetf(u):
    return np.where(u <= _f32(0.0031308), u * _f32(12.92), _f32(1.055) * np.power(u, _f32(1.0 / 2.4), dtype=_f32) - _f32(0.055))


class GamutLuts:
    """The four runtime tables; decode tables are read as int16 over 65536 entries."""

    def __init__(self) -> None:
        with np.errstate(invalid="ignore", over="ignore"):
            self.dec_srgb = _gen_lut(_srgb_eotf, 0x10000)
            self.dec_pow = _gen_lut(lambda u: np.power(u, _GAMMA_B, dtype=_f32), 0x10000)
        self.enc_pow = _gen_lut(lambda u: np.power(u, _f32(1.0) / _GAMMA_B, dtype=_f32), 0x4000)
        self.enc_srgb = _gen_lut(_srgb_oetf, 0x4000)
        # The engine stores int16: values above 0x4000 wrap. Inputs are 14-bit, so
        # only [0, 0x4000) is ever read; keep the wrap for parity anyway.
        for name in ("dec_srgb", "dec_pow"):
            t = getattr(self, name)
            t = ((t + 32768) % 65536) - 32768
            setattr(self, name, t)


_LUTS: GamutLuts | None = None


def gamut_luts() -> GamutLuts:
    global _LUTS
    if _LUTS is None:
        _LUTS = GamutLuts()
    return _LUTS


def _gamut(r, g, b, dec, enc, m):
    a = dec[r.astype(np.int64)]
    bb = dec[g.astype(np.int64)]
    c = dec[b.astype(np.int64)]
    out = []
    for row in m:
        v = (row[0] * a + row[1] * bb + row[2] * c) >> 13
        out.append(enc[np.clip(v, 0, 0x3FFF)].astype(np.uint16))
    return out


def gamut_fwd(r, g, b, luts: GamutLuts | None = None):
    luts = luts or gamut_luts()
    return _gamut(r, g, b, luts.dec_srgb, luts.enc_pow, M_FWD)


def gamut_inv(r, g, b, luts: GamutLuts | None = None):
    luts = luts or gamut_luts()
    return _gamut(r, g, b, luts.dec_pow, luts.enc_srgb, M_INV)


# --- YCC (0x14038aa40 / 0x14038b010) ---


def rgb_to_ycc(r, g, b):
    R, G, B = (np.asarray(p, _f32) for p in (r, g, b))

    def one(kr, kg, kb, off):
        t = (G * _f32(kg) + R * _f32(kr)) + B * _f32(kb)
        return np.clip(np.trunc(t / _f32(2048.0)) + _f32(off), 0, 65535).astype(np.uint16)

    return one(2884, 3523, 625, 4096), one(-1707, -2404, 4113, 32768), one(6860, -6848, -9, 32768)


def ycc_to_rgb(y, c1, c2):
    """The AVX path of the blend function: float32 throughout, trunc, clamp 14 bit."""
    Yf = np.asarray(y, _f32) - _f32(4096.0)
    C1 = np.asarray(c1, _f32) - _f32(32768.0)
    C2 = np.asarray(c2, _f32) - _f32(32768.0)

    def one(k1, k0, k2):
        t = (C1 * _f32(k1) + Yf * _f32(k0)) + C2 * _f32(k2)
        return np.clip(np.trunc(t / _f32(32768.0)), 0, 16383).astype(np.uint16)

    return one(-1438, 9542, 5414), one(-1459, 9547, -4376), one(14864, 9538, -310)


# --- the small-scale filters ---

# tab[k] = round(32768 / k), k = 1..25 (Edit.exe .data 0x1405594d0); tab[0] = 32768.
_RECIP25 = np.array([32768] + [int(np.floor(32768.0 / k + 0.5)) for k in range(1, 26)], np.uint32)
_RECIP256 = [0, 32768] + [int(np.floor(32768.0 / n + 0.5)) for n in range(2, 256)]


def _mul_rs8(a, k):
    return (a.astype(np.int64) * int(k) + 128) >> 8


def cnr2_threshold_mean(y, c1, c2, p):
    """The 5x5 stride-2 admitted-neighbour mean on the padded 1/4-res planes.

    ``y``, ``c1``, ``c2`` are int arrays already padded by >= 4 with replicated
    edges; returns the two chroma outputs over the same shape (edges of width 4
    are left equal to the input).
    """
    Y = y.astype(np.int64)
    C1 = c1.astype(np.int64)
    C2 = c2.astype(np.int64)
    t0 = np.clip(_mul_rs8(Y, p["p34"]) + p["p38"], 0, 65535)
    a1 = np.clip(_mul_rs8(C1 - 32768, p["p3c"]) + p["p40"], 0, 65535)
    a2 = np.clip(_mul_rs8(C2 - 32768, p["p44"]) + p["p48"], 0, 65535)
    thr1 = np.clip(_mul_rs8(np.clip((t0 * a1 + 128) >> 8, 0, 65535), p["p4c"]), 256, 32768)
    thr2 = np.clip(_mul_rs8(np.clip((t0 * a2 + 128) >> 8, 0, 65535), p["p50"]), 256, 32768)
    ythr = _f32(p["p30"])
    Yf, C1f, C2f = Y.astype(_f32), C1.astype(_f32), C2.astype(_f32)
    thr1f, thr2f = thr1.astype(_f32), thr2.astype(_f32)
    h, w = Y.shape
    s1 = np.zeros((h - 8, w - 8), _f32)
    s2 = np.zeros((h - 8, w - 8), _f32)
    n = np.zeros((h - 8, w - 8), _f32)
    ctr = (slice(4, h - 4), slice(4, w - 4))
    yc, c1c, c2c = Yf[ctr], C1f[ctr], C2f[ctr]
    t1c, t2c = thr1f[ctr], thr2f[ctr]
    for dy in (-4, -2, 0, 2, 4):
        for dx in (-4, -2, 0, 2, 4):
            sl = (slice(4 + dy, h - 4 + dy), slice(4 + dx, w - 4 + dx))
            yn, c1n, c2n = Yf[sl], C1f[sl], C2f[sl]
            ok = (np.abs(yc - yn) <= ythr) & (np.abs(c1c - c1n) <= t1c) & (np.abs(c2c - c2n) <= t2c) \
                & (yn != 0) & (c1n != 0) & (c2n != 0)
            s1 += np.where(ok, c1n, _f32(0))
            s2 += np.where(ok, c2n, _f32(0))
            n += ok.astype(_f32)
    k = n.astype(np.int64)
    k = np.where((k >= 1) & (k <= 25), k, 0)
    r = _RECIP25[k].astype(np.uint64)
    o1 = np.minimum((s1.astype(np.uint64) * r + 0x4000) >> 15, 65535)
    o2 = np.minimum((s2.astype(np.uint64) * r + 0x4000) >> 15, 65535)
    out1, out2 = c1.copy(), c2.copy()
    out1[ctr] = o1.astype(np.uint16)
    out2[ctr] = o2.astype(np.uint16)
    return out1, out2


def cnr3_blur(c, p_centre):
    """[1 2 1;2 4 2;1 2 1]/16 blended with the centre by p/256 (+2048 >> 12)."""
    x = c.astype(np.uint64)
    s = np.zeros_like(x)
    k = ((1, 2, 1), (2, 4, 2), (1, 2, 1))
    h, w = x.shape
    for dy in range(3):
        for dx in range(3):
            s[1:h - 1, 1:w - 1] += k[dy][dx] * x[dy:h - 2 + dy, dx:w - 2 + dx]
    out = c.copy()
    v = (16 * x[1:h - 1, 1:w - 1] * p_centre + s[1:h - 1, 1:w - 1] * (256 - p_centre) + 2048) >> 12
    out[1:h - 1, 1:w - 1] = (v & 0xFFFF).astype(np.uint16)
    return out


# Bilinear weights for factor 4 (Edit.exe .data 0x140559640..): row phases
# dy = 1/8, 3/8, 5/8, 7/8 and column phases dx = 1/4, 3/4; sum 128; +64 >> 7.
_W_A0 = np.array([84, 28, 60, 20, 36, 12, 12, 4], np.int64)
_W_A1 = np.array([28, 84, 20, 60, 12, 36, 4, 12], np.int64)
_W_B1 = np.array([4, 12, 12, 36, 20, 60, 28, 84], np.int64)
_W_B0 = np.array([12, 4, 36, 12, 60, 20, 84, 28], np.int64)


def cnr4_upsample(src, hs, ws):
    """1/4-res (hs+2, ws+2) plane with one replicated pad row/col at the far side
    -> half-res (4*hs, 2*ws) with centred bilinear phases."""
    S = src.astype(np.int64)
    out = np.zeros((4 * hs, 2 * ws), np.int64)
    A = S[:hs, :ws]
    A1 = S[:hs, 1:ws + 1]
    B = S[1:hs + 1, :ws]
    B1 = S[1:hs + 1, 1:ws + 1]
    for r in range(4):
        for c in range(2):
            k = 2 * r + c
            v = (_W_A0[k] * A + _W_A1[k] * A1 + _W_B1[k] * B1 + _W_B0[k] * B + 64) >> 7
            # engine writes rows 4j+2+r and columns 2i+1+c: the grid is offset by
            # (+2, +1); rows 0..1 and column 0 come from the previous cell.
            dst = out[2 + r::4, 1 + c::2]
            nr, nc = min(dst.shape[0], hs), min(dst.shape[1], ws)
            dst[:nr, :nc] = v[:nr, :nc]
    # rows 0,1 and column 0 are undefined in the engine (unwritten); replicate.
    out[0:2, :] = out[2:3, :]
    out[:, 0] = out[:, 1]
    return np.clip(out, 0, 65535)


def cnr4_protect(u1, u2, tmp, p):
    """b2 = U1; b1 = tmp blended over U2 where U1 is desaturated and U2 strongly positive."""
    r1 = _RECIP256[p["hi1"] - p["lo1"]] if p["hi1"] >= p["lo1"] else -_RECIP256[p["lo1"] - p["hi1"]]
    r2 = _RECIP256[p["hi2"] - p["lo2"]] if p["hi2"] >= p["lo2"] else -_RECIP256[p["lo2"] - p["hi2"]]
    U1 = u1.astype(np.int64)
    U2 = u2.astype(np.int64)
    s1 = (1 << 23) - np.clip((np.abs(U1 - 32768) - p["lo1"] * 256) * r1, 0, 1 << 23)
    c2 = np.clip(((U2 - 32768) - p["lo2"] * 256) * r2, 0, 1 << 23)
    k = (np.minimum(s1, c2) + 32768) >> 16
    alpha = k * p["strength"]
    b1 = np.minimum((tmp.astype(np.int64) * alpha + (32768 - alpha) * U2 + 16384) >> 15, 65535)
    return np.clip(U1, 0, 65535).astype(np.uint16), b1.astype(np.uint16)


# --- calibration (ILCE-7CM2, calib+0x10a0..0x10c4; identical on every body dumped) ---

CALIB_7CM2 = {
    "p30": 8192, "p34": -4, "p38": 982, "p3c": -1, "p40": 158, "p44": -1, "p48": 187,
    "base_4c": 652, "base_50": 652, "base_54": 128, "base_58": 128,
    "lo1": 30, "hi1": 40, "lo2": 30, "hi2": 40, "strength": 205,
}


def slider_params(calib: dict, chroma_slider: int = 5) -> dict:
    """Edit's manual '色彩降噪' slider (0..10, 5 = auto) -> the ctx thresholds.

    opts[0x224] = 10*(slider-5); esi = trunc(v/20)+5, ebx = -trunc(v/20). Above 5
    the threshold gain grows 8/5 per step (the processor's vt[0xe8] path), below
    it the gain shrinks linearly and the blur mixes less of the centre.
    """
    v = 10 * (int(chroma_slider) - 5)
    q = int(v / 20)  # trunc toward zero, as the imul/sar idiom does
    esi, ebx = q + 5, -q
    p = {k: calib[k] for k in ("p30", "p34", "p38", "p3c", "p40", "p44", "p48", "lo1", "hi1", "lo2", "hi2", "strength")}
    for key, base in (("p4c", calib["base_4c"]), ("p50", calib["base_50"])):
        if esi > 5:
            p[key] = int(((esi - 5) * base * 8) / 5) + base
        else:
            p[key] = int(base * esi / 5)
    p["p54"] = int(ebx * calib["base_54"] / 5) + calib["base_54"]
    p["p58"] = int(ebx * calib["base_58"] / 5) + calib["base_58"]
    p["esi"] = esi
    return p


def blend_amount(iso: float, chroma_slider: int = 5) -> float:
    """0x140395dd0: 0.5 at ISO <= 100 rising linearly to 1.0 at ISO 1600, then
    ramped towards 1 by the slider above its midpoint. float32 like the engine."""
    iso = round(iso)
    if iso >= 1600:
        x4 = _f32(1.0)
    else:
        t = _f32(_f32(iso - 100) / _f32(1500))
        x4 = _f32(_f32(_f32(1.0) - t) * _f32(0.5) + t * _f32(1.0))
    esi = slider_params(CALIB_7CM2, chroma_slider)["esi"]
    if esi <= 5:
        return float(x4)
    t2 = _f32(_f32(esi - 5) / _f32(5))
    return float(_f32(_f32(1.0) - t2) * x4 + t2)


# --- the whole stage ---


def marble_ycc_planes(y: np.ndarray, c1: np.ndarray, c2: np.ndarray, params: dict) -> tuple[np.ndarray, np.ndarray]:
    """Cleaned full-res chroma planes (before the amount blend) from full-res YCC."""
    H, W = y.shape
    W2 = W // 2
    # 2:1 pair mean, truncating (setup_6290)
    c1h = ((c1[:, 0:2 * W2:2].astype(np.uint32) + c1[:, 1:2 * W2:2]) >> 1).astype(np.uint16)
    c2h = ((c2[:, 0:2 * W2:2].astype(np.uint32) + c2[:, 1:2 * W2:2]) >> 1).astype(np.uint16)
    # 4x4 boxes to 1/4 res (cnr1_b760); partial boxes at the far edge keep the full divisor.
    hs, ws = (H + 3) // 4, (W + 3) // 4

    def box(p, bw, shift, rnd):
        ph = np.zeros((hs * 4, ws * bw), np.uint32)
        ph[:p.shape[0], :p.shape[1]] = p
        s = ph.reshape(hs, 4, ws, bw).sum(axis=(1, 3))
        v = (s + rnd) >> shift
        return np.where(v == 0, 1, v).astype(np.uint16)

    d0 = box(y[:, :ws * 4], 4, 4, 8)
    d1 = box(c1h, 2, 3, 4)
    d2 = box(c2h, 2, 3, 4)
    pad = 6
    p0, p1, p2 = (np.pad(d, pad, mode="edge") for d in (d0, d1, d2))
    q1, q2 = cnr2_threshold_mean(p0, p1, p2, params)
    s1 = cnr3_blur(q1, params["p54"])
    s2 = cnr3_blur(q2, params["p58"])
    # cnr4 reads cell (j, i) and (j+1, i+1): pass the core plus one pad row/col.
    core = (slice(pad, pad + hs + 1), slice(pad, pad + ws + 1))
    u1 = cnr4_upsample(s1[core], hs, ws)[:H, :W2]
    u2 = cnr4_upsample(s2[core], hs, ws)[:H, :W2]
    b2, b1 = cnr4_protect(u1, u2, c2h, params)
    # nearest 2x horizontal (fin_5f20)
    c1f = np.repeat(b2, 2, axis=1)
    c2f = np.repeat(b1, 2, axis=1)
    if W2 * 2 < W:
        c1f = np.pad(c1f, ((0, 0), (0, W - 2 * W2)), mode="edge")
        c2f = np.pad(c2f, ((0, 0), (0, W - 2 * W2)), mode="edge")
    return c1f, c2f


def marble_chroma_nr_14bit(r, g, b, iso: float = 2000.0, chroma_slider: int = 5,
                           calib: dict | None = None, amount: float | None = None):
    """14-bit uint16 planes in, 14-bit planes out. The engine's stage without Clarity."""
    calib = calib or CALIB_7CM2
    params = slider_params(calib, chroma_slider)
    if amount is None:
        amount = blend_amount(iso, chroma_slider)
    luts = gamut_luts()
    rw, gw, bw = gamut_fwd(r, g, b, luts)
    y, c1, c2 = rgb_to_ycc(rw, gw, bw)
    c1n, c2n = marble_ycc_planes(y, c1, c2, params)
    a = _f32(amount)
    ia = _f32(1.0) - a
    c1m = np.clip(c1.astype(_f32) * ia + c1n.astype(_f32) * a, 0, 65535)
    c2m = np.clip(c2.astype(_f32) * ia + c2n.astype(_f32) * a, 0, 65535)
    ro, go, bo = ycc_to_rgb(y, c1m, c2m)
    return gamut_inv(ro, go, bo, luts)


def apply_marble_chroma_nr(rgb: np.ndarray, iso: float = 2000.0, chroma_slider: int = 5,
                           amount: float | None = None) -> np.ndarray:
    """float32 (h, w, 3) display RGB in 0..1 -> the same after the engine's cleanup."""
    if rgb.ndim != 3 or rgb.shape[-1] != 3:
        raise ValueError(f"expected an (h, w, 3) image, got {rgb.shape}")
    q = np.clip(np.rint(rgb.astype(_f32) * _f32(16383.0)), 0, 16383).astype(np.uint16)
    ro, go, bo = marble_chroma_nr_14bit(q[..., 0], q[..., 1], q[..., 2], iso, chroma_slider, amount=amount)
    out = np.empty_like(rgb, dtype=_f32)
    out[..., 0] = ro
    out[..., 1] = go
    out[..., 2] = bo
    out /= _f32(16383.0)
    return out


def marble_block(iso: int, calib: dict | None = None) -> dict:
    """Wire form for the browser: the ISO (the amount's only per-shot input), the
    auto amount at the default slider, and the threshold calibration."""
    calib = calib or CALIB_7CM2
    return {"iso": int(iso), "amountAuto": blend_amount(iso, 5), "calib": dict(calib)}
