r"""`ZcTaskSIMDITP`'s stages as compiled kernels, one pass per stage.

Why per-pixel kernels rather than whole-array numpy: the operator is memory
bound, not compute bound. Written as chains of whole-strip array expressions, a
33 MP frame streamed a few hundred full planes through DRAM with ~20 float32
temporaries live per strip; measured on a 12900K, `demosaic` took 9.15 s on one
thread and 7.51 s on six -- six times the cores bought 18%, which is what a
bandwidth wall looks like. Here each output pixel is computed from its
neighbourhood in one pass, so only the planes that cross a stage boundary (W,
m1, m3, base and four activity maps) ever reach memory: same frame, 2.78 s on
one thread and 0.43 s on twelve.

Bit-exactness is the whole constraint -- these planes are compared against the
engine to 1 LSB, so a reassociated sum is a regression. The rules followed here,
all of them load-bearing:

* every literal is `np.float32(...)`; a bare Python float would promote the
  expression to float64 and round at a different point.
* every accumulator starts at `_ZERO` and takes its terms in the order the
  transcription that was scored against the engine did (aggregate's tap order,
  the FIRs' ascending k, malvar's row-major 5x5, the activity kernels' row-major
  order). In float32 `(a + b) + c` is not `a + (b + c)`, and `_ZERO + x` is not
  `x` when x is a negative zero, which the -0.0625 FIR taps do produce.
* no `fastmath`: it licenses both reassociation and FMA contraction.
* the unwritten zeros matter. Each engine stage writes a sub-rectangle of a
  zeroed plane and later stages read the zeros outside it, so the same borders
  are reproduced here: `_lp_h` returns 0 outside `[2, w - 2)`, the aggregate
  reads 0 past the right edge (the engine's row padding), anisotropy is 0
  outside rect inset by 4, and t1..t4 are 0 outside the 2x2-aligned rectangle.

What the stages mean and how they were decoded is in `sony/itp.py`'s docstring.
"""
from __future__ import annotations

import numpy as np
from numba import njit

F = np.float32

_ZERO = F(0.0)
_ONE = F(1.0)
_HALF = F(0.5)
_TWO = F(2.0)
_FOUR = F(4.0)
_SIX = F(6.0)
_EIGHT = F(8.0)
_C36 = F(36.0)
_QUARTER = F(0.25)
_EIGHTH = F(0.125)
_N16 = F(1.0 / 16.0)
_N32 = F(1.0 / 32.0)
_N64 = F(1.0 / 64.0)
_N88 = F(1.0 / 88.0)
_LEVEL_MAX = F(16383.0)

#: COEF_SAME's five non-zero taps (k = 0, 2, 4, 6, 8) and COEF_CROSS's four
#: (k = 1, 3, 5, 7), in the order `_fir_h` accumulated them.
_S0, _S2, _S4, _S6, _S8 = F(-0.0625), F(0.25), F(0.625), F(0.25), F(-0.0625)
_X1, _X3, _X5, _X7 = F(-0.05566406), F(0.55566406), F(0.55566406), F(-0.05566406)

#: MALVAR's four distinct taps; every one of the 25 is non-zero, so all are taken.
_MA, _MB, _MC, _MD, _ME = F(-1.0), F(8.0), F(10.0), F(-2.0), F(44.0)

_CRIT_P0, _CRIT_P1, _CRIT_P2 = F(1.0), F(1.0), F(0.125)
_ANISO_FLOOR = F(1.0)
_ANISO_LO, _ANISO_HI = F(0.5), F(1.0)
_ANISO_BIAS, _ANISO_SLOPE = F(0.2), F(0.8)
_ANISO_EPS = F(0.01)

_JIT = {"cache": True, "nogil": True}
#: The leaf primitives are inlined at the Numba IR level rather than left to
#: LLVM: they are called up to fifty times per pixel and each one is three flops.
_INL = {"nogil": True, "inline": "always"}


# -- primitives (0x3b0f00 / 0x3b1040 / 0x3b11f0 / 0x3b1330), with the borders ----

@njit(**_INL)
def _lp_h(W, y, x, w):
    if x < 2 or x >= w - 2:
        return _ZERO
    return ((W[y, x - 2] + _SIX * W[y, x]) + W[y, x + 2]) * _EIGHTH


@njit(**_INL)
def _lp_v(W, y, x, h):
    if y < 2 or y >= h - 2:
        return _ZERO
    return ((W[y - 2, x] + _SIX * W[y, x]) + W[y + 2, x]) * _EIGHTH


@njit(**_INL)
def _mid_h(W, y, x, w):
    if x < 1 or x >= w - 1:
        return _ZERO
    return (W[y, x - 1] + W[y, x + 1]) * _HALF


@njit(**_INL)
def _mid_v(W, y, x, h):
    if y < 1 or y >= h - 1:
        return _ZERO
    return (W[y - 1, x] + W[y + 1, x]) * _HALF


@njit(**_INL)
def _u_at(W, y, x):
    """u = np.where(green, W, 0): the green-only checkerboard malvar/anisotropy see."""
    if ((x ^ y) & 1) == 1:
        return W[y, x]
    return _ZERO


@njit(**_INL)
def _act(a, b, c):
    """|a + c - 2b| + |b - c| + |a - b|, accumulated left to right."""
    return (abs((a + c) - _TWO * b) + abs(b - c)) + abs(a - b)


# -- vt58 (0x35ad60): the working plane ------------------------------------------

@njit(**_JIT)
def _convert(mosaic, gains, black, W):
    h, w = mosaic.shape
    b = F(black)
    for y in range(h):
        if (y & 1) == 0:
            ga, gb = gains[0], gains[1]
        else:
            ga, gb = gains[2], gains[3]
        for x in range(0, w - 1, 2):
            W[y, x] = (F(mosaic[y, x]) - b) * ga
            W[y, x + 1] = (F(mosaic[y, x + 1]) - b) * gb
        if (w & 1) == 1:
            W[y, w - 1] = (F(mosaic[y, w - 1]) - b) * ga


# -- orch_110: direction weights -------------------------------------------------

@njit(**_INL)
def _agg_tap(src, y, x, w):
    """Row 0's taps reach x + 4: the engine reads its zero row padding there."""
    if x >= w:
        return _ZERO
    return src[y, x]


@njit(**_INL)
def _aggregate_at(src, y, x, w, n):
    """AGGREGATE_TAPS in dict order: the twelve 6s, then the twelve 1s, then the 36."""
    a = _ZERO
    a = a + src[y - 2, x]
    a = a + src[y - 1, x - 1]
    a = a + src[y - 1, x]
    a = a + src[y - 1, x + 1]
    a = a + src[y, x]
    a = a + _agg_tap(src, y, x + 1, w)
    a = a + _agg_tap(src, y, x + 3, w)
    a = a + _agg_tap(src, y, x + 4, w)
    a = a + src[y + 1, x - 1]
    a = a + src[y + 1, x]
    a = a + src[y + 1, x + 1]
    a = a + src[y + 2, x]
    b = _ZERO
    b = b + src[y - 2, x - 2]
    b = b + src[y - 2, x - 1]
    b = b + src[y - 2, x + 1]
    b = b + src[y - 2, x + 2]
    b = b + src[y - 1, x - 2]
    b = b + src[y - 1, x + 2]
    b = b + src[y + 1, x - 2]
    b = b + src[y + 1, x + 2]
    b = b + src[y + 2, x - 2]
    b = b + src[y + 2, x - 1]
    b = b + src[y + 2, x + 1]
    b = b + src[y + 2, x + 2]
    c = _ZERO
    c = c + _agg_tap(src, y, x + 2, w)
    return ((_SIX * a + b) + _C36 * c) * n


@njit(**_JIT)
def _direction_maps(W, x0, y0, x1, y1):
    """cost_h / cost_v -> aggregate -> (direction_weight, confidence).

    The two cost maps are the only planes that live past this call's body; m1 and
    m3 are pointwise in the aggregates, so the aggregates never reach memory.
    """
    h, w = W.shape
    ch = np.zeros((h, w), F)
    cv = np.zeros((h, w), F)
    # the cost maps are non-zero only on green sites, (x ^ y) & 1 == 1, so each
    # row touches one column parity and the mask never enters the inner loop
    for y in range(h):
        for x in range(2 if (y & 1) == 1 else 3, w - 2, 2):
            ch[y, x] = abs(((_TWO * W[y, x] - W[y, x - 2]) - W[y, x + 2]) * _QUARTER)
    for y in range(2, h - 2):
        for x in range(0 if (y & 1) == 1 else 1, w, 2):
            cv[y, x] = abs(((_TWO * W[y, x] - W[y - 2, x]) - W[y + 2, x]) * _QUARTER)

    m1 = np.empty((h, w), F)
    m3 = np.empty((h, w), F)
    for y in range(h):
        in_row = y0 + 2 <= y < y1 - 2
        # aggregate()'s normaliser is chosen per row from (x0 ^ y), never per column
        n = _N32 if ((x0 ^ y) & 1) == 0 else _N88
        for x in range(w):
            ah = _ZERO
            av = _ZERO
            if in_row:
                if x0 + 2 <= x < x1 - 2:
                    ah = _aggregate_at(ch, y, x, w, n)
                    av = _aggregate_at(cv, y, x, w, n)
                elif (x0 <= x < x0 + 2) or (x1 - 2 <= x < x1):
                    ah = ch[y, x]
                    av = cv[y, x]
            a = max(_ZERO, ah - _CRIT_P0) + _CRIT_P2
            b = max(_ZERO, av - _CRIT_P1) + _CRIT_P2
            s = b + a
            m1[y, x] = a / s if s > _ZERO else _HALF
            r = (ah + _ONE) / (av + _ONE)
            inv = _ONE / r
            m3[y, x] = _HALF * (r if r <= inv else inv)
    return m1, m3


# -- orch_118: the base plane ----------------------------------------------------

@njit(**_INL)
def _fir_h(W, y, x, w, green):
    if x < 4 or x >= w - 4:
        return _ZERO
    a = _ZERO
    if green:
        a = a + _S0 * W[y, x - 4]
        a = a + _S2 * W[y, x - 2]
        a = a + _S4 * W[y, x]
        a = a + _S6 * W[y, x + 2]
        a = a + _S8 * W[y, x + 4]
    else:
        a = a + _X1 * W[y, x - 3]
        a = a + _X3 * W[y, x - 1]
        a = a + _X5 * W[y, x + 1]
        a = a + _X7 * W[y, x + 3]
    return a


@njit(**_INL)
def _fir_v(W, y, x, h, green):
    if y < 4 or y >= h - 4:
        return _ZERO
    a = _ZERO
    if green:
        a = a + _S0 * W[y - 4, x]
        a = a + _S2 * W[y - 2, x]
        a = a + _S4 * W[y, x]
        a = a + _S6 * W[y + 2, x]
        a = a + _S8 * W[y + 4, x]
    else:
        a = a + _X1 * W[y - 3, x]
        a = a + _X3 * W[y - 1, x]
        a = a + _X5 * W[y + 1, x]
        a = a + _X7 * W[y + 3, x]
    return a


@njit(**_JIT)
def _blend_plane(W, m1):
    """base_candidates + green_candidates -> d = (f + e)/2 -> lerp(d_h, d_v, m1)."""
    h, w = W.shape
    out = np.empty((h, w), F)
    for y in range(h):
        for x in range(w):
            green = ((x ^ y) & 1) == 1
            if green:
                f1 = _lp_h(W, y, x, w)
                f2 = _lp_v(W, y, x, h)
            else:
                f1 = _mid_h(W, y, x, w)
                f2 = _mid_v(W, y, x, h)
            d1 = _HALF * (f1 + _fir_h(W, y, x, w, green))
            d2 = _HALF * (f2 + _fir_v(W, y, x, h, green))
            m = m1[y, x]
            out[y, x] = (_ONE - m) * d1 + m * d2
    return out


@njit(**_INL)
def _malvar_at(W, y, x, h, w):
    """0x3b14a0's 5x5 on u, row-major; /64 on green sites and /32 elsewhere."""
    if y < 2 or y >= h - 2 or x < 2 or x >= w - 2:
        return _ZERO
    a = _ZERO
    a = a + _MA * _u_at(W, y - 2, x - 2)
    a = a + _MA * _u_at(W, y - 2, x - 1)
    a = a + _MD * _u_at(W, y - 2, x)
    a = a + _MA * _u_at(W, y - 2, x + 1)
    a = a + _MA * _u_at(W, y - 2, x + 2)
    a = a + _MA * _u_at(W, y - 1, x - 2)
    a = a + _MB * _u_at(W, y - 1, x - 1)
    a = a + _MC * _u_at(W, y - 1, x)
    a = a + _MB * _u_at(W, y - 1, x + 1)
    a = a + _MA * _u_at(W, y - 1, x + 2)
    a = a + _MD * _u_at(W, y, x - 2)
    a = a + _MC * _u_at(W, y, x - 1)
    a = a + _ME * _u_at(W, y, x)
    a = a + _MC * _u_at(W, y, x + 1)
    a = a + _MD * _u_at(W, y, x + 2)
    a = a + _MA * _u_at(W, y + 1, x - 2)
    a = a + _MB * _u_at(W, y + 1, x - 1)
    a = a + _MC * _u_at(W, y + 1, x)
    a = a + _MB * _u_at(W, y + 1, x + 1)
    a = a + _MA * _u_at(W, y + 1, x + 2)
    a = a + _MA * _u_at(W, y + 2, x - 2)
    a = a + _MA * _u_at(W, y + 2, x - 1)
    a = a + _MD * _u_at(W, y + 2, x)
    a = a + _MA * _u_at(W, y + 2, x + 1)
    a = a + _MA * _u_at(W, y + 2, x + 2)
    return a * _N64 if ((x ^ y) & 1) == 1 else a * _N32


@njit(**_JIT)
def _activity_maps(W):
    """anisotropy()'s four three-point activity planes; their borders stay zero
    and the 5x3 / 3x5 / 3x3 aggregations below read those zeros."""
    h, w = W.shape
    b0 = np.zeros((h, w), F)
    b1 = np.zeros((h, w), F)
    d1 = np.zeros((h, w), F)
    d2 = np.zeros((h, w), F)
    for y in range(h):
        for x in range(2, w - 2):
            b0[y, x] = _act(_u_at(W, y, x - 2), _u_at(W, y, x), _u_at(W, y, x + 2))
    for y in range(2, h - 2):
        for x in range(w):
            b1[y, x] = _act(_u_at(W, y - 2, x), _u_at(W, y, x), _u_at(W, y + 2, x))
    for y in range(1, h - 1):
        for x in range(1, w - 1):
            c = _u_at(W, y, x)
            d1[y, x] = _act(_u_at(W, y - 1, x + 1), c, _u_at(W, y + 1, x - 1))
            d2[y, x] = _act(_u_at(W, y - 1, x - 1), c, _u_at(W, y + 1, x + 1))
    return b0, b1, d1, d2


@njit(**_INL)
def _conv_k0(a, y, x):
    """K0 = [[1,1,1],[2,8,2],[6,6,6],[2,8,2],[1,1,1]], row-major over the pixel."""
    s = _ZERO
    s = s + a[y - 2, x - 1]
    s = s + a[y - 2, x]
    s = s + a[y - 2, x + 1]
    s = s + _TWO * a[y - 1, x - 1]
    s = s + _EIGHT * a[y - 1, x]
    s = s + _TWO * a[y - 1, x + 1]
    s = s + _SIX * a[y, x - 1]
    s = s + _SIX * a[y, x]
    s = s + _SIX * a[y, x + 1]
    s = s + _TWO * a[y + 1, x - 1]
    s = s + _EIGHT * a[y + 1, x]
    s = s + _TWO * a[y + 1, x + 1]
    s = s + a[y + 2, x - 1]
    s = s + a[y + 2, x]
    s = s + a[y + 2, x + 1]
    return s


@njit(**_INL)
def _conv_k1(a, y, x):
    """K1 = K0.T, row-major."""
    s = _ZERO
    s = s + a[y - 1, x - 2]
    s = s + _TWO * a[y - 1, x - 1]
    s = s + _SIX * a[y - 1, x]
    s = s + _TWO * a[y - 1, x + 1]
    s = s + a[y - 1, x + 2]
    s = s + a[y, x - 2]
    s = s + _EIGHT * a[y, x - 1]
    s = s + _SIX * a[y, x]
    s = s + _EIGHT * a[y, x + 1]
    s = s + a[y, x + 2]
    s = s + a[y + 1, x - 2]
    s = s + _TWO * a[y + 1, x - 1]
    s = s + _SIX * a[y + 1, x]
    s = s + _TWO * a[y + 1, x + 1]
    s = s + a[y + 1, x + 2]
    return s


@njit(**_INL)
def _conv_kd(a, y, x, cx):
    """Kd = [[1,1,1],[1,4,1],[1,1,1]] on a window centred cx columns off the pixel."""
    s = _ZERO
    s = s + a[y - 1, x + cx - 1]
    s = s + a[y - 1, x + cx]
    s = s + a[y - 1, x + cx + 1]
    s = s + a[y, x + cx - 1]
    s = s + _FOUR * a[y, x + cx]
    s = s + a[y, x + cx + 1]
    s = s + a[y + 1, x + cx - 1]
    s = s + a[y + 1, x + cx]
    s = s + a[y + 1, x + cx + 1]
    return s


@njit(**_JIT)
def _apply_base(W, m3, blend, x0, y0, x1, y1):
    """base = lerp(blend, malvar(u), max(m3, anisotropy(u, rect))), in place.

    Every term is pointwise in `blend`, so the plane is updated where it lies:
    one fewer 130 MB temporary per frame. anisotropy() writes only rect inset by
    4 and is zero outside it, and m3 is strictly positive, so out there the
    weight is m3 and the aggregation is skipped entirely.
    """
    h, w = W.shape
    b0, b1, d1, d2 = _activity_maps(W)
    ay0, ay1 = y0 + 4, y1 - 4
    ax0, ax1 = x0 + 4, x1 - 4
    for y in range(h):
        ye = (y & 1) == 0
        ndi = _QUARTER if ye else _EIGHTH
        in_row = ay0 <= y < ay1
        for x in range(w):
            wgt = m3[y, x]
            if in_row and ax0 <= x < ax1:
                # the axial normaliser is 1/32 where the row parity and the
                # column-offset parity agree and 1/16 where they do not
                nax = _N32 if ye == (((x - x0) & 1) == 0) else _N16
                ah = max(_conv_k0(b0, y, x) * nax, _ANISO_FLOOR)
                av = max(_conv_k1(b1, y, x) * nax, _ANISO_FLOOR)
                ad1 = max(_conv_kd(d1, y, x, -2) * ndi, _ANISO_FLOOR)
                ad2 = max(_conv_kd(d2, y, x, 2) * ndi, _ANISO_FLOOR)
                r = ((ah + av) + _ANISO_EPS) / ((ad1 + ad2) + _ANISO_EPS)
                o = (_ANISO_HI - (r - _ANISO_BIAS)) * _ANISO_SLOPE
                an = max(min(o, _ANISO_HI), _ANISO_LO)
                if an > wgt:
                    wgt = an
            blend[y, x] = (_ONE - wgt) * blend[y, x] + wgt * _malvar_at(W, y, x, h, w)


# -- orch_190 + the exec's tail loop (0x3af500) ----------------------------------

@njit(**_INL)
def _edge_weight(a, b, c):
    """vt[0x170]: |b - c| / (|a - c| + |b - c|), 0.5 at 0/0."""
    da = abs(a - c)
    db = abs(b - c)
    s = da + db
    return _HALF if s == _ZERO else db / s


@njit(**_INL)
def _colour_diffs(W, m, y, x, h, w):
    """colour_diff_fields + the two lerps at one pixel -> (bD, b50).

    lp/mid are recomputed from W rather than materialised: four more full planes
    cost more bandwidth than the twenty-odd extra flops per pixel cost time.
    """
    plus = ((x + y) & 1) == 0
    s = _ONE if plus else -_ONE
    ns = -s
    lh = _lp_h(W, y, x, w)
    mh = _mid_h(W, y, x, w)
    lv = _lp_v(W, y, x, h)
    mv = _mid_v(W, y, x, h)
    sv = s * (lh - mh)
    sh = s * (lv - mv)

    vi = _ZERO
    if 1 <= y < h - 1:
        lhu = _lp_h(W, y - 1, x, w)
        mhu = _mid_h(W, y - 1, x, w)
        lhd = _lp_h(W, y + 1, x, w)
        mhd = _mid_h(W, y + 1, x, w)
        # which plane feeds the weight is decided by the *centre* pixel's phase
        wg = _edge_weight(lhu, lhd, mh) if plus else _edge_weight(mhu, mhd, lh)
        vi = wg * (ns * (lhu - mhu)) + (_ONE - wg) * (ns * (lhd - mhd))

    hi = _ZERO
    if 1 <= x < w - 1:
        lvl = _lp_v(W, y, x - 1, h)
        mvl = _mid_v(W, y, x - 1, h)
        lvr = _lp_v(W, y, x + 1, h)
        mvr = _mid_v(W, y, x + 1, h)
        wg = _edge_weight(lvl, lvr, mv) if plus else _edge_weight(mvl, mvr, lv)
        hi = wg * (ns * (lvl - mvl)) + (_ONE - wg) * (ns * (lvr - mvr))

    if (y & 1) == 0:
        t1, t3 = sv, vi
    else:
        t1, t3 = vi, sv
    if (x & 1) == 0:
        t2, t4 = sh, hi
    else:
        t2, t4 = hi, sh
    return (_ONE - m) * t1 + m * t2, (_ONE - m) * t3 + m * t4


@njit(**_INL)
def _quantise(v):
    """pack()'s trunc(clip(v, 0, 16383)) -> uint16."""
    if v < _ZERO:
        v = _ZERO
    elif v > _LEVEL_MAX:
        v = _LEVEL_MAX
    return np.uint16(np.trunc(v))


@njit(**_INL)
def _field_rect(x0, y0, x1, y1):
    """colour_diff_fields writes rect inset by 2, rounded down to whole 2x2 steps."""
    ry = ((y1 - 2) - (y0 + 2) + 1) // 2 * 2
    rx = ((x1 - 2) - (x0 + 2) + 1) // 2 * 2
    return x0 + 2, y0 + 2, x0 + 2 + rx, y0 + 2 + ry


@njit(**_JIT)
def _planes(W, m1, base, x0, y0, x1, y1):
    h, w = W.shape
    fx0, fy0, fx1, fy1 = _field_rect(x0, y0, x1, y1)
    r_out = np.empty((h, w), np.uint16)
    g_out = np.empty((h, w), np.uint16)
    b_out = np.empty((h, w), np.uint16)
    for y in range(h):
        in_row = fy0 <= y < fy1
        for x in range(w):
            bv = base[y, x]
            g = _quantise(bv)
            g_out[y, x] = g
            if in_row and fx0 <= x < fx1:
                bd, b50 = _colour_diffs(W, m1[y, x], y, x, h, w)
                r_out[y, x] = _quantise(bd + bv)
                b_out[y, x] = _quantise(b50 + bv)
            else:
                # t1..t4 are zero outside the written rectangle, so bD = b50 = 0
                r_out[y, x] = g
                b_out[y, x] = g
    return r_out, g_out, b_out


@njit(**_JIT)
def _pipeline(mosaic, gains, black, x0, y0, x1, y1):
    h, w = mosaic.shape
    W = np.empty((h, w), F)
    _convert(mosaic, gains, black, W)
    m1, m3 = _direction_maps(W, x0, y0, x1, y1)
    base = _blend_plane(W, m1)
    _apply_base(W, m3, base, x0, y0, x1, y1)
    return W, m1, base


@njit(**_JIT)
def itp_tile(mosaic, gains, black, x0, y0, x1, y1):
    """One engine tile with the rect unpacked: uint16 mosaic -> (R, G, B) uint16 planes."""
    W, m1, base = _pipeline(mosaic, gains, black, x0, y0, x1, y1)
    return _planes(W, m1, base, x0, y0, x1, y1)


@njit(**_JIT)
def strip(mosaic, gains, black, x0, y0, x1, y1, top, bottom, left, right, scale, out):
    """One `demosaic` strip: tile in, its scaled interior straight into `out`.

    Folding the crop and the /8192 into the kernel keeps the three uint16 planes
    from being materialised and read back -- one more full pass over the frame
    plus 200 MB of allocation per frame otherwise.
    """
    h, w = mosaic.shape
    W, m1, base = _pipeline(mosaic, gains, black, x0, y0, x1, y1)
    fx0, fy0, fx1, fy1 = _field_rect(x0, y0, x1, y1)
    for y in range(top, bottom):
        oy = y - top
        in_row = fy0 <= y < fy1
        for x in range(left, right):
            ox = x - left
            bv = base[y, x]
            g = F(_quantise(bv)) * scale
            out[oy, ox, 1] = g
            if in_row and fx0 <= x < fx1:
                bd, b50 = _colour_diffs(W, m1[y, x], y, x, h, w)
                out[oy, ox, 0] = F(_quantise(bd + bv)) * scale
                out[oy, ox, 2] = F(_quantise(b50 + bv)) * scale
            else:
                out[oy, ox, 0] = g
                out[oy, ox, 2] = g
