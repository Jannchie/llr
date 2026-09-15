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
   everything goes through an f x f box to 1/f resolution, f = 4 or 8: the
   shot's SR2 tag 0x795e picks 8 (the bodies dumped write it from ISO 6400 up),
   which doubles the reach of every filter below at full resolution.
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

The thresholds, the protect terms and the factor are not the engine's: they are
the camera's, written per shot into the ARW's SR2SubIFD as tags 0x794a..0x795e
and copied verbatim into the calibration block (FUN_14015f540, calib+0x109c..
+0x10c4) that the stage reads (sony_repro/notes/highiso-denoise-gap.md 9).
``calib_from_sr2`` reads them; ``calib_for_iso`` is the fallback for a frame
without them, a table of the values 31 ILCE-7CM2 frames carried.

Everything below keeps the engine's integer and float32 semantics so the
reference tile in the test fixtures reproduces exactly. The whole frame is
processed at once; the engine works on 1080-px tiles whose 24-px margins are
discarded, and the filter's support (16 px at full res for the mean, plus the
blur and the upsampling) fits inside that margin, so whole-frame processing is
the tile result without the seams.
"""

from __future__ import annotations

from struct import error as struct_error

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


def bilinear_weights(f: int) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """The engine's cnr4 tap tables for factor `f` (A0, A1, B1, B0: the cell,
    its right neighbour, the diagonal, the one below), indexed by row phase r
    (0..f-1) times f/2 plus column phase c (0..f/2-1).

    Edit.exe .data 0x140559640.. (f = 4: 84/28/60/20/..) and 0x1405596c0..
    (f = 8: 105/75/45/15/..): row weights (2f-(2r+1), 2r+1) over 2f, column
    weights (f-(2c+1), 2c+1) over f, the product scaled so the four sum to 128;
    the phases are centred (1/8.. for f = 4, 1/16.. for f = 8). +64 >> 7."""
    r = np.arange(f)[:, None]
    c = np.arange(f // 2)[None, :]
    k = 128 // (2 * f * f)
    wy0, wy1 = 2 * f - (2 * r + 1), 2 * r + 1
    wx0, wx1 = f - (2 * c + 1), 2 * c + 1
    a0, a1, b1, b0 = ((w * k).astype(np.int64).ravel() for w in (wy0 * wx0, wy0 * wx1, wy1 * wx1, wy1 * wx0))
    return a0, a1, b1, b0


# f = 4 (Edit.exe 0x140559640..): row phases 1/8, 3/8, 5/8, 7/8 and column phases 1/4, 3/4.
_W_A0, _W_A1, _W_B1, _W_B0 = bilinear_weights(4)


def cnr4_upsample(src, hs, ws, f: int = 4):
    """1/f-res (hs+2, ws+2) plane with one replicated pad row/col at the far side
    -> half-res (f*hs, f/2*ws) with centred bilinear phases (cnr4_ea10 for
    f = 4, cnr4_fe70 for f = 8)."""
    wa0, wa1, wb1, wb0 = bilinear_weights(f)
    S = src.astype(np.int64)
    out = np.zeros((f * hs, (f // 2) * ws), np.int64)
    A = S[:hs, :ws]
    A1 = S[:hs, 1:ws + 1]
    B = S[1:hs + 1, :ws]
    B1 = S[1:hs + 1, 1:ws + 1]
    for r in range(f):
        for c in range(f // 2):
            k = (f // 2) * r + c
            v = (wa0[k] * A + wa1[k] * A1 + wb1[k] * B1 + wb0[k] * B + 64) >> 7
            # engine writes rows f*j + f/2 + r and columns f/2*i + f/4 + c: the
            # grid is offset by (+f/2, +f/4); the first rows and columns come
            # from the previous cell.
            dst = out[f // 2 + r::f, f // 4 + c::f // 2]
            nr, nc = min(dst.shape[0], hs), min(dst.shape[1], ws)
            dst[:nr, :nc] = v[:nr, :nc]
    # the first f/2 rows and f/4 columns are undefined in the engine (unwritten); replicate.
    out[0:f // 2, :] = out[f // 2:f // 2 + 1, :]
    out[:, 0:f // 4] = out[:, f // 4:f // 4 + 1]
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


# --- calibration: the shot's SR2 tags 0x794a..0x795e (calib+0x109c..+0x10c4) ---

#: SR2SubIFD tag -> calibration field, in the order FUN_14015f540 (0x140161c49..)
#: copies them into the block ZcTaskSIMDMarble reads. "enabled" is calib+0x109c
#: (the exec skips the whole chroma path when it is zero), "factor8" is
#: calib+0x109e (1 -> decimate by 8, cnr1_c070/cnr4_fe70; else 4). Every value
#: is read as a 16-bit word and sign-extended (movsx), whatever TIFF type the
#: camera wrote it with.
MARBLE_SR2_TAGS: dict[int, str] = {
    0x794A: "enabled", 0x794B: "p30", 0x794C: "p34", 0x794D: "p38", 0x794E: "p3c",
    0x794F: "p40", 0x7950: "p44", 0x7951: "p48", 0x7952: "base_4c", 0x7953: "base_50",
    0x7954: "base_54", 0x7955: "base_58", 0x7956: "x5c", 0x7957: "x60", 0x7958: "x64",
    0x7959: "lo1", 0x795A: "hi1", 0x795B: "lo2", 0x795C: "hi2", 0x795D: "strength",
    0x795E: "factor8",
}

# The block as an ISO 2000 ILCE-7CM2 frame (DSC03036) carries it; the tests'
# reference tile and every frame verified before the tags were found used it.
CALIB_7CM2 = {
    "p30": 8192, "p34": -4, "p38": 982, "p3c": -1, "p40": 158, "p44": -1, "p48": 187,
    "base_4c": 652, "base_50": 652, "base_54": 128, "base_58": 128,
    "lo1": 30, "hi1": 40, "lo2": 30, "hi2": 40, "strength": 205,
    "factor": 4,
}


def _s16(v: int) -> int:
    v &= 0xFFFF
    return v - 0x10000 if v >= 0x8000 else v


def calib_from_tags(tags: dict[int, int]) -> dict | None:
    """The calibration block from the shot's SR2 tag values (tag -> value as
    read), or None when any of the 21 is missing -- the engine keys the whole
    block on the file's SR2 version and a partial set is not a thing it sees.
    ``factor`` is 8 when tag 0x795e is 1, else 4; ``enabled`` mirrors 0x794a."""
    if any(t not in tags for t in MARBLE_SR2_TAGS):
        return None
    raw = {name: _s16(int(tags[t])) for t, name in MARBLE_SR2_TAGS.items()}
    factor8 = raw.pop("factor8")
    calib = {k: raw[k] for k in CALIB_7CM2 if k != "factor"}
    calib["factor"] = 8 if factor8 == 1 else 4
    calib["enabled"] = raw["enabled"] != 0
    calib["x5c"], calib["x60"], calib["x64"] = raw["x5c"], raw["x60"], raw["x64"]
    return calib


def calib_from_sr2(path) -> dict | None:
    """The Marble calibration the engine would build for this ARW, read from
    its SR2SubIFD; None for a file without the tags (not a Sony RAW, or one
    older than the SR2 version that carries them)."""
    from .sr2 import read_sr2_scalars

    try:
        tags = read_sr2_scalars(path, list(MARBLE_SR2_TAGS))
    except (OSError, KeyError, ValueError, struct_error):
        return None
    return calib_from_tags(tags)


# Fallback for a frame without the tags: what 31 ILCE-7CM2 frames carried, one
# row per ISO seen (sony_repro/notes/highiso-denoise-gap.md 9.1). The values
# are the camera's per shot, not a function of the ISO -- base_4c differs
# between two ISO 160 frames -- so this only stands in; a frame with the tags
# never comes here.
_ISO_TABLE: tuple[tuple[float, dict], ...] = (
    (100.0, {"p34": -2, "p38": 831, "p3c": 0, "p40": 102, "p44": 0, "p48": 120, "base_4c": 128, "base_50": 128, "factor": 4}),
    (320.0, {"p34": -2, "p38": 831, "p3c": 0, "p40": 102, "p44": 0, "p48": 120, "base_4c": 355, "base_50": 355, "factor": 4}),
    (800.0, {"p34": -3, "p38": 840, "p3c": -1, "p40": 109, "p44": 0, "p48": 129, "base_4c": 603, "base_50": 603, "factor": 4}),
    (1000.0, {"p34": -3, "p38": 857, "p3c": -1, "p40": 123, "p44": 0, "p48": 144, "base_4c": 648, "base_50": 648, "factor": 4}),
    (1250.0, {"p34": -3, "p38": 886, "p3c": -1, "p40": 147, "p44": 0, "p48": 172, "base_4c": 732, "base_50": 732, "factor": 4}),
    (2000.0, {"p34": -4, "p38": 982, "p3c": -1, "p40": 158, "p44": -1, "p48": 187, "base_4c": 652, "base_50": 652, "factor": 4}),
    (2500.0, {"p34": -5, "p38": 1159, "p3c": -1, "p40": 160, "p44": -1, "p48": 194, "base_4c": 408, "base_50": 408, "factor": 4}),
    (4000.0, {"p34": -5, "p38": 1314, "p3c": -1, "p40": 164, "p44": -1, "p48": 204, "base_4c": 288, "base_50": 288, "factor": 4}),
    (6400.0, {"p34": -5, "p38": 1449, "p3c": -1, "p40": 174, "p44": -1, "p48": 225, "base_4c": 384, "base_50": 384, "factor": 8}),
    (8000.0, {"p34": -6, "p38": 1645, "p3c": -1, "p40": 175, "p44": -1, "p48": 226, "base_4c": 432, "base_50": 432, "factor": 8}),
    (25600.0, {"p34": -7, "p38": 2222, "p3c": -1, "p40": 184, "p44": -1, "p48": 487, "base_4c": 640, "base_50": 640, "factor": 8}),
)


def calib_for_iso(iso: float, calib: dict | None = None) -> dict:
    """The fallback calibration for `iso`: the table row at a listed ISO, the
    two neighbours interpolated in log-ISO between (integers rounded, the
    factor from the lower row), the ends held."""
    base = dict(calib or CALIB_7CM2)
    iso = float(iso)
    for row_iso, row in _ISO_TABLE:
        if iso == row_iso:
            base.update(row)
            return base
    if iso < _ISO_TABLE[0][0]:
        base.update(_ISO_TABLE[0][1])
        return base
    if iso > _ISO_TABLE[-1][0]:
        base.update(_ISO_TABLE[-1][1])
        return base
    k = next(i for i in range(len(_ISO_TABLE) - 1) if _ISO_TABLE[i][0] < iso < _ISO_TABLE[i + 1][0])
    (lo_iso, lo), (hi_iso, hi) = _ISO_TABLE[k], _ISO_TABLE[k + 1]
    t = (np.log(iso) - np.log(lo_iso)) / (np.log(hi_iso) - np.log(lo_iso))
    for key in lo:
        if key == "factor":
            base[key] = lo[key]
        else:
            base[key] = int(np.rint(lo[key] + (hi[key] - lo[key]) * t))
    return base


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
    p["factor"] = int(calib.get("factor", 4))
    return p


def blend_amount(iso: float, chroma_slider: int = 5) -> float:
    """How much of the cleaned chroma replaces the original: 1.0.

    0x140395dd0 has two paths. When the processor's mode word (+0x144) is 3 it
    returns 1.0 for any slider position (the ISO ramp below is skipped
    outright); otherwise 0.5 at ISO <= 100 rising linearly to 1.0 at ISO 1600,
    then ramped towards 1 by the slider above its midpoint. Every export
    captured runs the first path: at ISO 320 (cs_DSC02995, auto and manual
    NR, sony_repro/notes/highiso-denoise-gap.md 9.3) the blend reproduces the
    engine's output only at 1.0 -- the ramp's 0.573 is 1.7M pixels off. The
    ramp is kept below as `blend_amount_ramp` for the other path, which no
    export has been seen to take."""
    del iso, chroma_slider
    return 1.0


def blend_amount_ramp(iso: float, chroma_slider: int = 5) -> float:
    """The ISO-ramped path of 0x140395dd0 (mode word != 3): 0.5 at ISO <= 100
    rising linearly to 1.0 at ISO 1600, then ramped towards 1 by the slider
    above its midpoint. float32 like the engine. Not what an export uses."""
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
    # f x f boxes to 1/f res (cnr1_b760 for f = 4, cnr1_c070 for f = 8: the
    # scalar generic with shifts 2*log2(f) / 2*log2(f) - 1 and half-that rounding);
    # partial boxes at the far edge keep the full divisor.
    f = int(params.get("factor", 4))
    if f not in (4, 8):
        raise ValueError(f"Marble decimation factor must be 4 or 8, got {f}")
    hs, ws = (H + f - 1) // f, (W + f - 1) // f
    ysh = 2 * int(np.log2(f))

    def box(p, bw, shift):
        ph = np.zeros((hs * f, ws * bw), np.uint32)
        ph[:p.shape[0], :p.shape[1]] = p
        s = ph.reshape(hs, f, ws, bw).sum(axis=(1, 3))
        v = (s + (1 << (shift - 1))) >> shift
        return np.where(v == 0, 1, v).astype(np.uint16)

    d0 = box(y[:, :ws * f], f, ysh)
    d1 = box(c1h, f // 2, ysh - 1)
    d2 = box(c2h, f // 2, ysh - 1)
    pad = 6
    p0, p1, p2 = (np.pad(d, pad, mode="edge") for d in (d0, d1, d2))
    q1, q2 = cnr2_threshold_mean(p0, p1, p2, params)
    s1 = cnr3_blur(q1, params["p54"])
    s2 = cnr3_blur(q2, params["p58"])
    # cnr4 reads cell (j, i) and (j+1, i+1): pass the core plus one pad row/col.
    core = (slice(pad, pad + hs + 1), slice(pad, pad + ws + 1))
    u1 = cnr4_upsample(s1[core], hs, ws, f)[:H, :W2]
    u2 = cnr4_upsample(s2[core], hs, ws, f)[:H, :W2]
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
    calib = calib or calib_for_iso(iso)
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
                           amount: float | None = None, calib: dict | None = None) -> np.ndarray:
    """float32 (h, w, 3) display RGB in 0..1 -> the same after the engine's cleanup.
    `calib` is the shot's block (calib_from_sr2); without it the ISO table stands in."""
    if rgb.ndim != 3 or rgb.shape[-1] != 3:
        raise ValueError(f"expected an (h, w, 3) image, got {rgb.shape}")
    q = np.clip(np.rint(rgb.astype(_f32) * _f32(16383.0)), 0, 16383).astype(np.uint16)
    ro, go, bo = marble_chroma_nr_14bit(q[..., 0], q[..., 1], q[..., 2], iso, chroma_slider, calib=calib, amount=amount)
    out = np.empty_like(rgb, dtype=_f32)
    out[..., 0] = ro
    out[..., 1] = go
    out[..., 2] = bo
    out /= _f32(16383.0)
    return out


def marble_block(iso: int, calib: dict | None = None) -> dict:
    """Wire form for the browser: the ISO (the amount's per-shot input), the
    auto amount at the default slider, and the threshold calibration -- the
    shot's own (calib_from_sr2, with its decimation factor) when the caller
    has it, else the ISO table's. The shader takes the thresholds as given."""
    calib = dict(calib or calib_for_iso(iso))
    calib.setdefault("factor", 4)
    wire = {k: calib[k] for k in CALIB_7CM2}
    return {"iso": int(iso), "amountAuto": blend_amount(iso, 5), "calib": wire}
