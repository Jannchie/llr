"""The compiled half of `rawnr_simd`: its three kernels, as compiled loops.

Why compiled loops rather than whole-plane numpy: the sigma filter spelled as
25 whole-strip array passes was 1.5 s of the RawNR stage's 2.5 s on a 33 MP
frame, and a strip schedule around it only made the 25 temporaries fit in
cache, not fewer. Carrying a short segment of one row through all 25 taps
instead keeps the whole working set in L1, and measured on that frame takes
`filt` from 1.53 s to 0.08 s (24 threads) and the two analyses from 0.32 s to
0.04 s.

Every expression here is the transcription that was scored against the
engine's own planes, operation for operation and in the same order, because
it is only bit-exact in that order (see `rawnr_simd.filt`: own-then-cross
member accumulation, the centre tap accumulated in its scan position, `base`
in the engine's own `[(1024-blend)*mean + blend*centre] * (1/1024)` form).
float32 addition is not associative, so "the same arithmetic in a tidier
order" is a different result, and the pixels it moves are exactly the ones
sitting an ulp from a threshold.

Three rules follow from that, and none of them is style:

* **No `fastmath`.** It licenses reassociation and FMA contraction; `base`
  alone is two multiplies and an add, which an FMA would round once instead of
  twice, and `total` is a 25-term chain a reassociation would reorder.
* **No Python float literals in the kernels.** `x * 2.0` in numba is a float64
  multiply, and one of those promotes the rest of the expression. Every
  constant arrives as a `np.float32` argument, and every cast is spelled
  `np.float32(...)`.
* **The tap list is the caller's.** `rawnr_simd` builds it in scan order, which
  decides where the unconditional centre tap lands in the accumulation.

`tests/test_rawnr_simd.py` pins the kernels to the engine's own planes;
`sony_repro/tools/rawnr_export_verify.py` checks the compiled path still
reproduces the engine's own export tile at 100.0000%.

Each kernel takes `y0`/`y1` and writes only those output rows, so the caller
can schedule strips over threads. `nogil=True` is what makes that schedule real
threads rather than a queue behind the GIL.
"""

from __future__ import annotations

import numba
import numpy as np


@numba.njit(cache=True, nogil=True)
def filt_rows(d, ref, other, thr_tab, tap_off, centre_k, own_off, other_off, out,
              y0, y1, r, chunk, w_mean, w_ctr, inv_blend, inv_n,
              gain_f, limit_f, nlimit_f, off_f, thr_hi, ceil_f):
    """`rawnr_simd.filt`'s operator for output rows `y0:y1`, a row segment at a time.

    The neighbour tables arrive as *flat* element offsets (`dy*stride + dx`)
    rather than (dy, dx) pairs, so a tap costs one integer add instead of a
    multiply and two: `ref`, `other` and `d` are the same shape, so one offset
    is valid in all three. `tap_off` is the 5x5 set in scan order and `centre_k`
    its centre's index in that order -- the one tap the engine never thresholds.

    Why `chunk` rather than one pixel start to finish. Each pixel's 25 taps are
    one serial chain of float32 additions -- and it has to stay one, since that
    order is the bit-exactness -- so a per-pixel kernel spends its time waiting
    on the adder's 4-cycle latency and retires roughly one tap per four cycles.
    Different pixels' chains are independent, so running a short segment of the
    row through one tap at a time interleaves them: the inner loops below are
    unit-stride over `chunk` lanes and vectorise. Nothing about a pixel's own
    arithmetic changes -- same taps, same order, same rounding. Measured on one
    8 MP red/blue plane, single-threaded: 0.70 s per pixel with branches, 0.52 s
    per pixel with selects, 0.16 s this way, all three bit-identical.
    `chunk` is sized so the five working buffers stay in L1.

    The accept test is a select rather than a branch: it is unpredictable by
    construction (it is a noise threshold), and it has to vectorise. Adding
    `0.0` for a rejected tap is a true no-op rather than an approximation --
    `total` starts at +0.0 and IEEE addition only loses a sign on -0.0, which no
    sum reaching this line can be.
    """
    rf = ref.ravel()
    of = other.ravel()
    df = d.ravel()
    zero = np.float32(0.0)
    one = np.float32(1.0)
    n_taps = tap_off.shape[0]
    n_own = own_off.shape[0]
    n_other = other_off.shape[0]
    stride = ref.shape[1]
    width = out.shape[1]
    m = chunk if chunk < width else width
    base = np.empty(m, dtype=np.float32)
    thr = np.empty(m, dtype=np.float32)
    total = np.empty(m, dtype=np.float32)
    count = np.empty(m, dtype=np.float32)
    for i in range(y0, y1):
        row = (i + r) * stride + r
        for j0 in range(0, width, m):
            n = m if j0 + m <= width else width - j0
            p0 = row + j0

            # Own members then cross members, straight through: NOT
            # sum(own) + sum(cross). The engine is one `vaddps` chain and the
            # two groupings land on different last bits.
            off = own_off[0]
            for j in range(n):
                base[j] = rf[p0 + j + off]
            for k in range(1, n_own):
                off = own_off[k]
                for j in range(n):
                    base[j] += rf[p0 + j + off]
            for k in range(n_other):
                off = other_off[k]
                for j in range(n):
                    base[j] += of[p0 + j + off]

            for j in range(n):
                centre = rf[p0 + j]
                # The engine's exact order: [(1024-blend)*mean + blend*centre]
                # * (1/1024), with mean = members * (1/n).
                base[j] = (w_mean * (base[j] * inv_n) + w_ctr * centre) * inv_blend
                # thresholds[clip(ref, 0, len-1).astype(int32)] -- clamped to
                # the *table*, whose ceiling the engine puts far above anything
                # `analysis_*` can emit, so the lookup is never truncated.
                t = centre
                if t < zero:
                    t = zero
                if t > thr_hi:
                    t = thr_hi
                thr[j] = thr_tab[np.int32(t)]
                total[j] = zero
                count[j] = zero

            # The centre tap is accumulated where the scan reaches it, not
            # hoisted out as the total's seed -- green reproduces the engine on
            # 89.3% instead of 100% if that order changes.
            for k in range(n_taps):
                off = tap_off[k]
                if k == centre_k:
                    for j in range(n):
                        total[j] += rf[p0 + j + off]
                        count[j] += one
                else:
                    for j in range(n):
                        v = rf[p0 + j + off]
                        acc = abs(base[j] - v) < thr[j]
                        total[j] += v if acc else zero
                        count[j] += one if acc else zero

            for j in range(n):
                # `count` is at least 1 because the centre tap is unconditional.
                o = total[j] / count[j]
                boost = df[p0 + j] * gain_f
                if boost < nlimit_f:
                    boost = nlimit_f
                elif boost > limit_f:
                    boost = limit_f
                o = (o + boost) - off_f
                if o < zero:
                    o = zero
                elif o > ceil_f:
                    o = ceil_f
                out[i, j0 + j] = o


@numba.njit(cache=True, nogil=True)
def strength_rows(filtered, original, out, s, one_minus_s, i0, i1):
    """`apply_strength` over a flat element range: `trunc(s*f + (1-s)*orig)`.

    Flat rather than 2-D because the exec's write-back is elementwise and its
    callers are not all the same rank -- the frame path hands it (H, W, 4) and
    the fixture test a single (96, 96) plane. `one_minus_s` is computed once by
    the caller.

    `np.trunc` returns float64 in numba even for a float32 argument; the
    `np.float32` around it is exact (truncating a float32 lands on a value
    float32 holds) and keeps that out of the result.
    """
    for i in range(i0, i1):
        v = s * filtered[i]
        v = v + one_minus_s * original[i]
        out[i] = np.float32(np.trunc(v))


@numba.njit(cache=True, nogil=True)
def analysis_rb_rows(a, off_f, d_out, ref_out, y0, y1):
    """`analysis_rb` for output rows `y0:y1`. `d = (12c - 2*N4 - diag)/16`."""
    zero = np.float32(0.0)
    hi = np.float32(32767.0)
    two = np.float32(2.0)
    twelve = np.float32(12.0)
    inv16 = np.float32(1.0 / 16.0)
    width = d_out.shape[1]
    for i in range(y0, y1):
        y = i + 1
        for j in range(width):
            x = j + 1
            c = a[y, x]
            n4 = ((a[y - 1, x] + a[y + 1, x]) + a[y, x - 1]) + a[y, x + 1]
            diag = ((a[y - 1, x - 1] + a[y - 1, x + 1]) + a[y + 1, x - 1]) + a[y + 1, x + 1]
            dv = ((twelve * c - two * n4) - diag) * inv16
            rv = (c - dv) + off_f
            if rv < zero:
                rv = zero
            elif rv > hi:
                rv = hi
            d_out[i, j] = dv
            ref_out[i, j] = rv


@numba.njit(cache=True, nogil=True)
def analysis_green_rows(a, b, cross, off_f, d_out, ref_out, y0, y1):
    """`analysis_green` for output rows `y0:y1`.

    `cross` is the phase's four taps on the *other* green plane, weighted 3
    against the own plane's 12 -- the other phase's samples sit half a pixel
    away on the diagonal, i.e. closer than this plane's own neighbours.
    """
    zero = np.float32(0.0)
    hi = np.float32(32767.0)
    two = np.float32(2.0)
    three = np.float32(3.0)
    twentyfour = np.float32(24.0)
    inv28 = np.float32(1.0 / 28.0)
    c0y, c0x = cross[0, 0], cross[0, 1]
    c1y, c1x = cross[1, 0], cross[1, 1]
    c2y, c2x = cross[2, 0], cross[2, 1]
    c3y, c3x = cross[3, 0], cross[3, 1]
    width = d_out.shape[1]
    for i in range(y0, y1):
        y = i + 1
        for j in range(width):
            x = j + 1
            c = a[y, x]
            n4 = ((a[y - 1, x] + a[y + 1, x]) + a[y, x - 1]) + a[y, x + 1]
            diag = ((a[y - 1, x - 1] + a[y - 1, x + 1]) + a[y + 1, x - 1]) + a[y + 1, x + 1]
            cr = ((b[y + c0y, x + c0x] + b[y + c1y, x + c1x])
                  + b[y + c2y, x + c2x]) + b[y + c3y, x + c3x]
            total = (two * n4 + diag) + three * cr
            dv = (twentyfour * c - total) * inv28
            rv = (c - dv) + off_f
            if rv < zero:
                rv = zero
            elif rv > hi:
                rv = hi
            d_out[i, j] = dv
            ref_out[i, j] = rv
