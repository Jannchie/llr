r"""Edit.exe's demosaic, `ZcTaskSIMDITP`, decoded stage by stage and reproduced.

This module is the reference: readable whole-array numpy, one function per
decoded stage, and what the engine dumps in sony_repro/tools/itp_verify.py are
checked against. It is also about eight times slower than it needs to be, so
`sony.itp` normally runs `itp_numba`, a literal per-pixel transcription of
everything below. Nothing here may change shape without that one changing with
it: the two are tested bit for bit.

This is the step llr had never reproduced: LibRaw's AHD was standing in for it,
and the measured gap (notes/measured-chroma-gap.md §2.19.1, §2.20) sat exactly
here -- with denoising off on both sides, llr's demosaic left the noise ~25%
more chromatic than Edit's (0.848 against 0.678 chroma/luma), and Edit's
demosaic halves the chroma/luma ratio relative to a neutral one while keeping
*more* luma detail. Both are properties of this operator.

How it was decoded (sony_repro/tools/itp_flow_probe.py, itp_dump_probe.py,
itp_verify.py; per-stage disassembly summaries under tmp/itp_summ/): the
processor object's whole vtable was read at runtime, which turned the already
decoded scalar orchestrators (static-itp-spica.md §2.5/§2.6) into the SIMD
data flow for free; then every stage's inputs and outputs were dumped from one
exec and each formula below was checked against them on three frames (two
bodies' worth of white balance, ISO 320/2000/4000). Every stage is bit-exact
except the two that accumulate nine or more float32 terms, which agree to
float32 rounding (max 2.4e-4 on values ~1000); end to end the three uint16
output planes match the engine to within 1 LSB on a handful of pixels.

The data flow. `W` is the mosaic converted to float32 (black subtracted,
white-balance gain applied); every plane below is full-tile, float32.

    orch_110  direction weights
        cost_h / cost_v   v = (2c - l - r)/4 over same-colour taps (spacing 2);
                          second output keeps |v| on green sites only
        aggregate         5x5 weighted sum of that sparse map (see aggregate())
        direction_weight  m1 = A/(A+B), A = max(0, aggH-1)+1/8, B likewise on aggV
        confidence        m3 = 0.5 * min(r, 1/r), r = (aggH+1)/(aggV+1)
    orch_118  the base plane (green everywhere)
        base_candidates   f1/f2: at green sites the [1,0,6,0,1]/8 low-pass of W,
                          elsewhere the midpoint of the two neighbours (H / V)
        green_candidates  e1/e2: 9-tap FIR of W with one coefficient set on green
                          sites and another on red/blue sites (H / V)
        d = (f + e)/2;  blend = (1-m1)*d_h + m1*d_v
        malvar            5x5 Malvar-style kernel on the green-only checkerboard
        anisotropy        a [0.5, 0.96) map from four directional activities
        base = (1-w)*blend + w*malvar,  w = max(m3, anisotropy)
    orch_190  the two colour-difference planes
        lp_h/lp_v, mid_h/mid_v   the same primitives as base_candidates, unmasked
        colour_diff_fields       four direction-filled (R-G) / (B-G) fields
        bD  = (1-m1)*t1 + m1*t2       b50 = (1-m1)*t3 + m1*t4
    pack   R = trunc(clamp(bD + base)), G = trunc(clamp(base)), B = trunc(clamp(b50 + base))
           clamped to 0..16383

The output domain: W = gain * (raw - black) with gain = trunc(WB * 33/32) / 2048
per RGGB phase (three frames, exact), so sensor white on green lands at
0.515625 * 15871 = 8184 -- Sony's tone LUT white index is 8192 (sony/tone.py),
which is how the rest of the Sony chain already reads camera RGB. Dividing the
uint16 planes by 8192 therefore drops straight into `apply_sony_profile`.

Tiling: the engine runs 1136x684 tiles with a 16-pixel halo and consumes only
the central 1024 pixels. Here the frame is padded by HALO (reflect, Bayer-safe)
and processed in horizontal strips with the same halo; strips are seamless
because no stage reaches further than 12 pixels (verified in the tests by
comparing strips against a whole-frame run).

Things measured rather than assumed, worth keeping:

* aggregate()'s row-0 taps are shifted right by two columns and its normaliser
  is chosen per *row* only. Both are literal in the disassembly and both are
  needed for bit-exactness; the symmetric reading is wrong.
* colour_diff_fields() picks the planes that feed the edge weight by the
  *centre* pixel's phase, not the side pixels'. The opposite reading matched
  exactly half of every plane.
* direction_weight()'s dead zone and regulariser (1.0 and 1/8) were reverse-
  fitted from the dumps before the parameter block confirmed them; they are not
  zero even though a dump of the block after the exec reads zero.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import numpy as np

F = np.float32

#: Halo around a strip / the frame. The engine uses 16 and consumes 40 pixels
#: inside its tile; the stages here reach at most ~12 pixels, so 32 keeps the
#: consumed region well clear of any stage's unwritten margin.
HALO = 32

#: Sony's tone-LUT white index: the divisor that maps the engine's 14-bit
#: planes onto llr's camera RGB scale (sony/tone.py TONE_INDEX_WHITE).
ENGINE_WHITE = 8192.0
LEVEL_MAX = 16383.0

#: vt60 hands 0x3b1830 two nine-tap coefficient sets (offsets -4..+4). The
#: same-phase set is a mild sharpening low-pass over spacing-2 taps, the
#: cross-phase set interpolates from the odd offsets.
COEF_SAME = np.array([-0.0625, 0, 0.25, 0, 0.625, 0, 0.25, 0, -0.0625], F)
COEF_CROSS = np.array([0, -0.05566406, 0, 0.55566406, 0, 0.55566406, 0, -0.05566406, 0], F)

#: 0x3b14a0: 5x5 kernel on the green-only checkerboard, /64 on green sites
#: (the even-parity taps sum to 64) and /32 elsewhere (odd-parity taps sum to 32).
MALVAR = np.array([[-1, -1, -2, -1, -1],
                   [-1, 8, 10, 8, -1],
                   [-2, 10, 44, 10, -2],
                   [-1, 8, 10, 8, -1],
                   [-1, -1, -2, -1, -1]], F)

#: 0x3b3910's taps, (dy, dx) -> weight. Row 0 is shifted by +2 columns: the
#: centre weight 36 sits at (0, +2). Measured, and required for bit-exactness.
AGGREGATE_TAPS = {(-2, -2): 1, (-2, -1): 1, (-2, 0): 6, (-2, 1): 1, (-2, 2): 1,
                  (-1, -2): 1, (-1, -1): 6, (-1, 0): 6, (-1, 1): 6, (-1, 2): 1,
                  (0, 0): 6, (0, 1): 6, (0, 2): 36, (0, 3): 6, (0, 4): 6,
                  (1, -2): 1, (1, -1): 6, (1, 0): 6, (1, 1): 6, (1, 2): 1,
                  (2, -2): 1, (2, -1): 1, (2, 0): 6, (2, 1): 1, (2, 2): 1}

#: 0x3b2be0's parameter record (0x5a8b68): dead-zone 0, floor 1.0, output
#: clamp [0.5, 1.0], affine (1.0 + 0.2 - r) * 0.8.
ANISO_FLOOR = 1.0
ANISO_LO, ANISO_HI = 0.5, 1.0
ANISO_BIAS, ANISO_SLOPE = 0.2, 0.8

#: 0x35f1c0's parameter record (0x5a8b48, read at the call): dead zones 1.0
#: and 1.0, regulariser 0.125.
CRIT_P0, CRIT_P1, CRIT_P2 = 1.0, 1.0, 0.125

Rect = tuple[int, int, int, int]


def green_mask(h: int, w: int) -> np.ndarray:
    """RGGB: green where (x ^ y) & 1 == 1."""
    yy, xx = np.mgrid[0:h, 0:w]
    return ((xx ^ yy) & 1) == 1


def wb_gains(wb_rggb: tuple[float, float, float, float]) -> np.ndarray:
    """vt58's per-phase gains: trunc(WB * 33/32) / 2048.

    WB 1838/1024/1024/2155 -> 1895/1056/1056/2222, 2455/1024/1024/1782 ->
    2531/1056/1056/1837, 2503/1024/1024/1596 -> 2581/1056/1056/1645; all three
    frames reproduce the engine's W to 1e-10.
    """
    return np.array([np.trunc(float(v) * 33.0 / 32.0) / 2048.0 for v in wb_rggb], F)


def convert(mosaic: np.ndarray, gains: np.ndarray, black: float) -> np.ndarray:
    """vt58 (0x35ad60): uint16 mosaic -> float32 W, gain * (v - black) per phase."""
    m = mosaic.astype(F)
    out = np.empty_like(m)
    b = F(black)
    out[0::2, 0::2] = (m[0::2, 0::2] - b) * F(gains[0])
    out[0::2, 1::2] = (m[0::2, 1::2] - b) * F(gains[1])
    out[1::2, 0::2] = (m[1::2, 0::2] - b) * F(gains[2])
    out[1::2, 1::2] = (m[1::2, 1::2] - b) * F(gains[3])
    return out


# -- primitives (0x3b0f00 / 0x3b1040 / 0x3b11f0 / 0x3b1330) ---------------------

def lp_h(W: np.ndarray) -> np.ndarray:
    out = np.zeros_like(W)
    out[:, 2:-2] = (W[:, :-4] + F(6) * W[:, 2:-2] + W[:, 4:]) * F(0.125)
    return out


def lp_v(W: np.ndarray) -> np.ndarray:
    out = np.zeros_like(W)
    out[2:-2, :] = (W[:-4, :] + F(6) * W[2:-2, :] + W[4:, :]) * F(0.125)
    return out


def mid_h(W: np.ndarray) -> np.ndarray:
    out = np.zeros_like(W)
    out[:, 1:-1] = (W[:, :-2] + W[:, 2:]) * F(0.5)
    return out


def mid_v(W: np.ndarray) -> np.ndarray:
    out = np.zeros_like(W)
    out[1:-1, :] = (W[:-2, :] + W[2:, :]) * F(0.5)
    return out


# -- orch_110: direction weights ----------------------------------------------

def cost_h(W: np.ndarray, green: np.ndarray) -> np.ndarray:
    """0x3af8d0's second output: |(2c - l - r)/4| on green sites, spacing 2."""
    v = np.zeros_like(W)
    v[:, 2:-2] = (F(2) * W[:, 2:-2] - W[:, :-4] - W[:, 4:]) * F(0.25)
    return np.where(green, np.abs(v), F(0)).astype(F)


def cost_v(W: np.ndarray, green: np.ndarray) -> np.ndarray:
    v = np.zeros_like(W)
    v[2:-2, :] = (F(2) * W[2:-2, :] - W[:-4, :] - W[4:, :]) * F(0.25)
    return np.where(green, np.abs(v), F(0)).astype(F)


def aggregate(src: np.ndarray, rect: Rect) -> np.ndarray:
    """0x3b3910 (vt[0xa0]): weighted 5x5 sum over the sparse cost map.

    6*A + B + 36*C with A the twelve weight-6 taps, B the twelve weight-1 taps,
    C the centre -- see AGGREGATE_TAPS for the shifted row 0. The normaliser is
    1/32 when (x0 ^ y) is even and 1/88 otherwise, one value per row: the engine
    broadcasts it per ymm and steps eight columns at a time from x0, so the
    column parity never enters. Rows y0+2..y1-2; the two outermost columns on
    each side are copied through.
    """
    x0, y0, x1, y1 = rect
    out = np.zeros_like(src)
    # row 0's taps reach x+4: the engine reads its row padding there. Pad so a
    # rect that ends at the plane edge still works; only the last two output
    # columns are affected and they are margin.
    padded = np.pad(src, ((0, 0), (0, 4)))
    ys, xs = slice(y0 + 2, y1 - 2), slice(x0 + 2, x1 - 2)
    A = np.zeros_like(src[ys, xs])
    B = np.zeros_like(A)
    C = np.zeros_like(A)
    for (dy, dx), wgt in AGGREGATE_TAPS.items():
        blk = padded[y0 + 2 + dy:y1 - 2 + dy, x0 + 2 + dx:x1 - 2 + dx]
        if wgt == 6:
            A = A + blk
        elif wgt == 1:
            B = B + blk
        else:
            C = C + blk
    rows = np.arange(y0 + 2, y1 - 2)[:, None]
    n = np.where(((x0 ^ rows) & 1) == 0, F(1 / 32), F(1 / 88)).astype(F)
    out[ys, xs] = (F(6) * A + B + F(36) * C) * n
    out[ys, x0:x0 + 2] = src[ys, x0:x0 + 2]
    out[ys, x1 - 2:x1] = src[ys, x1 - 2:x1]
    return out


def direction_weight(aggH: np.ndarray, aggV: np.ndarray) -> np.ndarray:
    """0x35f1c0 (vt[0xb0]): m = A / (A + B); 0.5 when the sum is not positive."""
    A = (np.maximum(F(0), aggH - F(CRIT_P0)) + F(CRIT_P2)).astype(F)
    B = (np.maximum(F(0), aggV - F(CRIT_P1)) + F(CRIT_P2)).astype(F)
    S = (B + A).astype(F)
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.where(S > 0, (A / S).astype(F), F(0.5)).astype(F)


def confidence(aggH: np.ndarray, aggV: np.ndarray) -> np.ndarray:
    """0x361950 (vt[0xb8]): 0.5 * min(r, 1/r), r = (aggH + 1) / (aggV + 1)."""
    r = ((aggH + F(1)) / (aggV + F(1))).astype(F)
    return (F(0.5) * np.minimum(r, F(1) / r)).astype(F)


# -- orch_118: the base plane ---------------------------------------------------

def base_candidates(W: np.ndarray, green: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """0x35f510 (vt[0xf0]): the low-pass on green sites, the midpoint elsewhere."""
    return (np.where(green, lp_h(W), mid_h(W)).astype(F),
            np.where(green, lp_v(W), mid_v(W)).astype(F))


def _fir_h(src: np.ndarray, c: np.ndarray) -> np.ndarray:
    out = np.zeros_like(src)
    w = src.shape[1]
    acc = np.zeros_like(src[:, 4:-4])
    for k in range(9):
        if c[k] != 0:
            d = k - 4
            acc = acc + c[k] * src[:, 4 + d:w - 4 + d]
    out[:, 4:-4] = acc
    return out


def _fir_v(src: np.ndarray, c: np.ndarray) -> np.ndarray:
    out = np.zeros_like(src)
    h = src.shape[0]
    acc = np.zeros_like(src[4:-4, :])
    for k in range(9):
        if c[k] != 0:
            d = k - 4
            acc = acc + c[k] * src[4 + d:h - 4 + d, :]
    out[4:-4, :] = acc
    return out


def green_candidates(W: np.ndarray, green: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """0x3b1830 (vt[0xe8]): 9-tap FIRs, COEF_SAME on green sites, COEF_CROSS elsewhere."""
    e1 = np.where(green, _fir_h(W, COEF_SAME), _fir_h(W, COEF_CROSS))
    e2 = np.where(green, _fir_v(W, COEF_SAME), _fir_v(W, COEF_CROSS))
    return e1.astype(F), e2.astype(F)


def malvar(u: np.ndarray, green: np.ndarray) -> np.ndarray:
    """0x3b14a0 (vt[0xc0]) on the green-only checkerboard u."""
    h, w = u.shape
    acc = np.zeros_like(u[2:-2, 2:-2])
    for a in range(5):
        for b in range(5):
            if MALVAR[a, b] != 0:
                acc = acc + MALVAR[a, b] * u[a:h - 4 + a, b:w - 4 + b]
    out = np.zeros_like(u)
    out[2:-2, 2:-2] = np.where(green[2:-2, 2:-2], acc * F(1 / 64), acc * F(1 / 32))
    return out


def _act(a: np.ndarray, b: np.ndarray, c: np.ndarray) -> np.ndarray:
    """|a + c - 2b| + |b - c| + |a - b| -- the three-point activity."""
    out = np.abs(a + c - F(2) * b)
    out += np.abs(b - c)
    out += np.abs(a - b)
    return out


def _conv_into(acc: np.ndarray, a: np.ndarray, K: np.ndarray, cx: int, m: int) -> None:
    """acc[m:-m, m:-m] += sum_ij K[i, j] * a[y + i - kh//2, x + j - kw//2 + cx], by slices."""
    h, w = a.shape
    kh, kw = K.shape
    tmp = np.empty_like(acc[m:-m, m:-m])
    for i in range(kh):
        for j in range(kw):
            k = K[i, j]
            if k == 0:
                continue
            dy, dx = i - kh // 2, j - kw // 2 + cx
            view = a[m + dy:h - m + dy, m + dx:w - m + dx]
            if k == 1:
                acc[m:-m, m:-m] += view
            else:
                np.multiply(view, F(k), out=tmp)
                acc[m:-m, m:-m] += tmp


def anisotropy(u: np.ndarray, rect: Rect) -> np.ndarray:
    """0x3b2be0 (via vt[0xc8]): the diagonal-dominance control map.

    Four three-point activities |a + c - 2b| + |b - c| + |a - b| (axial with
    spacing 2, diagonal with spacing 1), aggregated with a 5x3 / 3x5 kernel
    (axial, transposes of each other) and a 3x3 kernel (diagonal, windows
    centred two columns left and right of the pixel). Normalisers: axial per
    lane, 1/32 or 1/16 by (y, x - x0) parity; diagonal per row, 0.25 or 0.125.
    Then r = (Ah + Av + 0.01) / (Ad1 + Ad2 + 0.01) with each term floored at
    1.0, and out = clamp((1.2 - r) * 0.8, 0.5, 1.0). Flat areas give 0.5.
    """
    x0, y0, x1, y1 = rect
    h, w = u.shape
    # activities, each placed at its centre pixel; unwritten borders stay 0
    b0 = np.zeros_like(u)
    b0[:, 2:-2] = _act(u[:, :-4], u[:, 2:-2], u[:, 4:])
    b1 = np.zeros_like(u)
    b1[2:-2, :] = _act(u[:-4, :], u[2:-2, :], u[4:, :])
    d1 = np.zeros_like(u)   # anti-diagonal: (y-1, x+1), (y, x), (y+1, x-1)
    d1[1:-1, 1:-1] = _act(u[:-2, 2:], u[1:-1, 1:-1], u[2:, :-2])
    d2 = np.zeros_like(u)   # main diagonal: (y-1, x-1), (y, x), (y+1, x+1)
    d2[1:-1, 1:-1] = _act(u[:-2, :-2], u[1:-1, 1:-1], u[2:, 2:])

    K0 = np.array([[1, 1, 1], [2, 8, 2], [6, 6, 6], [2, 8, 2], [1, 1, 1]], F)
    K1 = np.ascontiguousarray(K0.T)
    Kd = np.array([[1, 1, 1], [1, 4, 1], [1, 1, 1]], F)
    M = 4
    Ah = np.zeros_like(u)
    _conv_into(Ah, b0, K0, 0, M)
    Av = np.zeros_like(u)
    _conv_into(Av, b1, K1, 0, M)
    Ad1 = np.zeros_like(u)
    _conv_into(Ad1, d1, Kd, -2, M)
    Ad2 = np.zeros_like(u)
    _conv_into(Ad2, d2, Kd, 2, M)
    del b0, b1, d1, d2

    yy, xx = np.mgrid[0:h, 0:w]
    xl = (xx - x0) & 1
    ye = (yy & 1) == 0
    nax = np.where(ye, np.where(xl == 0, F(1 / 32), F(1 / 16)),
                   np.where(xl == 0, F(1 / 16), F(1 / 32))).astype(F)
    ndi = np.where(ye, F(0.25), F(0.125)).astype(F)
    floor = F(ANISO_FLOOR)
    Ah *= nax
    np.maximum(Ah, floor, out=Ah)
    Av *= nax
    np.maximum(Av, floor, out=Av)
    Ad1 *= ndi
    np.maximum(Ad1, floor, out=Ad1)
    Ad2 *= ndi
    np.maximum(Ad2, floor, out=Ad2)
    Ah += Av
    Ah += F(0.01)
    Ad1 += Ad2
    Ad1 += F(0.01)
    r = Ah / Ad1
    o = np.maximum(np.minimum((F(ANISO_HI) - (r - F(ANISO_BIAS))) * F(ANISO_SLOPE),
                              F(ANISO_HI)), F(ANISO_LO)).astype(F)
    out = np.zeros_like(u)
    out[y0 + 4:y1 - 4, x0 + 4:x1 - 4] = o[y0 + 4:y1 - 4, x0 + 4:x1 - 4]
    return out


def lerp(a: np.ndarray, b: np.ndarray, w: np.ndarray) -> np.ndarray:
    """(1 - w) * a + w * b -- 0x3b4230, 0x3b43b0 and 0x3b2300 are all this."""
    return ((F(1) - w) * a + w * b).astype(F)


# -- orch_190: colour differences -----------------------------------------------

def edge_weight(a: np.ndarray, b: np.ndarray, ctr: np.ndarray) -> np.ndarray:
    """vt[0x170] (0x35d3a0): |b - ctr| / (|a - ctr| + |b - ctr|), 0.5 at 0/0.

    Used as w * X(a side) + (1 - w) * X(b side): the side closer to the centre
    gets the larger weight.
    """
    da = np.abs(a - ctr)
    db = np.abs(b - ctr)
    s = da + db
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.where(s == 0, F(0.5), db / s).astype(F)


def colour_diff_fields(lpH: np.ndarray, lpV: np.ndarray, midH: np.ndarray, midV: np.ndarray,
                       rect: Rect) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """0x3b0370 (vt[0x178]): four direction-filled colour-difference fields.

    V = lpH - midH is the horizontal estimate of "this site's colour minus the
    other", H = lpV - midV the vertical one; S = (-1)^(x+y) turns both into
    R-G / B-G. Then

        t1 = S*V direct on even rows, interpolated from the rows above/below on odd rows
        t3 = the complement (direct on odd rows)                -> the (B-G) horizontal field
        t2 = S*H direct on even columns, interpolated left/right on odd columns
        t4 = the complement

    Interpolation weights come from edge_weight over the *green* estimates at
    the two sides and the centre: where S = +1 the sides read lpH and the
    centre midH, where S = -1 the reverse (the vertical case; lpV/midV for the
    horizontal one). Which plane is which is decided by the centre pixel.
    Written over rect inset by 2, in 2x2 steps.
    """
    h, w = lpH.shape
    x0, y0, x1, y1 = rect
    yy, xx = np.mgrid[0:h, 0:w]
    S = np.where(((xx + yy) & 1) == 0, F(1), F(-1))
    SV = (S * (lpH - midH)).astype(F)
    SH = (S * (lpV - midV)).astype(F)
    even_row = (yy & 1) == 0
    even_col = (xx & 1) == 0
    plus = S > 0

    def vinterp(X):
        out = np.zeros_like(X)
        pc = plus[1:-1, :]
        a = np.where(pc, lpH[:-2, :], midH[:-2, :])
        b = np.where(pc, lpH[2:, :], midH[2:, :])
        c = np.where(pc, midH[1:-1, :], lpH[1:-1, :])
        wgt = edge_weight(a, b, c)
        out[1:-1, :] = wgt * X[:-2, :] + (F(1) - wgt) * X[2:, :]
        return out

    def hinterp(X):
        out = np.zeros_like(X)
        pc = plus[:, 1:-1]
        a = np.where(pc, lpV[:, :-2], midV[:, :-2])
        b = np.where(pc, lpV[:, 2:], midV[:, 2:])
        c = np.where(pc, midV[:, 1:-1], lpV[:, 1:-1])
        wgt = edge_weight(a, b, c)
        out[:, 1:-1] = wgt * X[:, :-2] + (F(1) - wgt) * X[:, 2:]
        return out

    vi = vinterp(SV)
    hi = hinterp(SH)
    fields = (np.where(even_row, SV, vi), np.where(even_col, SH, hi),
              np.where(even_row, vi, SV), np.where(even_col, hi, SH))
    ry = ((y1 - 2) - (y0 + 2) + 1) // 2 * 2
    rx = ((x1 - 2) - (x0 + 2) + 1) // 2 * 2
    outs = []
    for t in fields:
        o = np.zeros_like(t)
        o[y0 + 2:y0 + 2 + ry, x0 + 2:x0 + 2 + rx] = t[y0 + 2:y0 + 2 + ry, x0 + 2:x0 + 2 + rx]
        outs.append(o.astype(F))
    return outs[0], outs[1], outs[2], outs[3]


def pack(base: np.ndarray, bD: np.ndarray, b50: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """The exec's tail loop (0x3af500): three uint16 planes, truncated, clamped to 14 bits."""
    def q(x):
        return np.trunc(np.clip(x, F(0), F(LEVEL_MAX))).astype(np.uint16)
    return q(bD + base), q(base), q(b50 + base)


def itp_tile(mosaic: np.ndarray, gains: np.ndarray, black: float,
             rect: Rect) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """One engine tile: uint16 mosaic (black included) -> (R, G, B) uint16 planes.

    `rect` is the valid rectangle inside the tile (the engine's is the tile
    inset by its 16-pixel halo). Outputs are trustworthy from about 12 pixels
    inside `rect`; the margin holds each stage's unwritten zeros.
    """
    W = convert(mosaic, gains, black)
    green = green_mask(*W.shape)

    aggH = aggregate(cost_h(W, green), rect)
    aggV = aggregate(cost_v(W, green), rect)
    m1 = direction_weight(aggH, aggV)
    m3 = confidence(aggH, aggV)
    del aggH, aggV

    f1, f2 = base_candidates(W, green)
    e1, e2 = green_candidates(W, green)
    d1 = (F(0.5) * (f1 + e1)).astype(F)
    d2 = (F(0.5) * (f2 + e2)).astype(F)
    del f1, f2, e1, e2
    blend = lerp(d1, d2, m1)
    del d1, d2
    u = np.where(green, W, F(0)).astype(F)
    base = lerp(blend, malvar(u, green), np.maximum(m3, anisotropy(u, rect)))
    del blend, u, m3

    t1, t2, t3, t4 = colour_diff_fields(lp_h(W), lp_v(W), mid_h(W), mid_v(W), rect)
    bD = lerp(t1, t2, m1)
    b50 = lerp(t3, t4, m1)
    return pack(base, bD, b50)


def demosaic(mosaic: np.ndarray, wb_rggb: tuple[float, float, float, float], black: float,
             strip_rows: int = 128, workers: int = 6) -> np.ndarray:
    """The whole frame: RGGB uint16 mosaic (black included) -> float32 (h, w, 3)
    camera RGB on llr's scale (engine 14-bit planes / ENGINE_WHITE).

    Reflect-pads by HALO (even, so the CFA phase is preserved) and runs
    itp_tile over horizontal strips that overlap by HALO on each side, taking
    only each strip's interior. Row count per strip is a cache/memory knob, not
    a result knob.
    """
    if mosaic.ndim != 2:
        raise ValueError("mosaic must be a 2-D Bayer plane")
    h, w = mosaic.shape
    gains = wb_gains(wb_rggb)
    padded = np.pad(mosaic, HALO, mode="reflect")
    out = np.empty((h, w, 3), F)
    scale = F(1.0 / ENGINE_WHITE)
    pw = w + 2 * HALO

    def run(r0: int) -> None:
        r1 = min(h, r0 + strip_rows)
        tile = padded[r0:r1 + 2 * HALO, :]
        th = tile.shape[0]
        rect = (HALO // 2, HALO // 2, pw - HALO // 2, th - HALO // 2)
        planes = itp_tile(tile, gains, black, rect)
        for c, p in enumerate(planes):
            out[r0:r1, :, c] = p[HALO:th - HALO, HALO:pw - HALO].astype(F) * scale

    starts = list(range(0, h, strip_rows))
    # numpy releases the GIL inside its ufuncs, so strips overlap on real cores;
    # the worker count is a memory knob (each strip holds ~20 float32 planes).
    n_workers = max(1, min(workers, len(starts)))
    if n_workers == 1:
        for r0 in starts:
            run(r0)
    else:
        with ThreadPoolExecutor(max_workers=n_workers) as pool:
            list(pool.map(run, starts))
    return out

