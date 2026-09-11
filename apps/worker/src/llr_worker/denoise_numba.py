"""The compiled half of `denoise`'s plane plumbing: pack, scale, clip, unpack.

None of this is interesting arithmetic -- it is the normalisation either side of
a denoiser, a multiply, an add and a clamp per element. It is here because on a
33 MP frame it was 0.64 s of the RawNR stage's 0.83 s, for a reason that has
nothing to do with the arithmetic: a numpy chain writes a whole 132 MB plane set
per operator and reads it back for the next, and the machine measured copies at
21 GB/s (12.5 ms for a threaded 132 MB copy). What costs is the number of
passes, so threading the chain as it stood could not have fixed it. Fused into
one traversal each and threaded, the same work is 0.09 s.

So these kernels are worth reading for their *pass structure*, not their maths:
each one is a whole chain -- `pack_bayer` + `astype` + subtract + divide + clip,
say -- collapsed into a single traversal that reads its input once and writes
its output once.

The order of operations per element is the whole-array chain's, and that is
not decoration: `denoise_raw_inplace` round-trips the mosaic through [0, 1] and
back, and `SonyRawNRDenoiser` round-trips it again through raw levels, so the
result depends on float32 rounding in both directions. `tests/test_denoise.py`
pins the round trip bit for bit (a passthrough denoise must return the original
mosaic exactly).

Two numba details that would otherwise be silent:

* `np.trunc` and `np.rint` return **float64** in numba even for a float32
  argument. Both are wrapped in `np.float32(...)`, which is exact -- rounding a
  float32 to an integer always lands on a value float32 can hold -- but leaving
  the float64 in would promote everything downstream of it.
* No Python float literals in an expression with a float32; they are float64 and
  promote it. Constants come in as `np.float32` arguments or are spelled
  `np.float32(...)`.

Every kernel takes `y0`/`y1` and writes only those output rows, so the caller
can spread them over threads; `nogil=True` is what makes those threads real.
"""

from __future__ import annotations

import numba
import numpy as np

# Plane k sits at (k >> 1, k & 1) in the 2x2 CFA tile -- TL, TR, BL, BR, which
# is `denoise.pack_bayer`'s order. Written as arithmetic rather than a table so
# the packing and the unpacking below cannot disagree about it.


@numba.njit(cache=True, nogil=True)
def pack_normalise_rows(mosaic, black, scale, out, y0, y1):
    """`clip((pack_bayer(mosaic).astype(f32) - black) / scale, 0, 1)`, one pass.

    Reads the mosaic straight out of the caller's (strided) visible crop rather
    than a contiguous copy of it -- the copy existed only so `pack_bayer` could
    stack four strided views, and there is no stacking here.
    """
    zero = np.float32(0.0)
    one = np.float32(1.0)
    w2 = out.shape[1]
    for i in range(y0, y1):
        top = 2 * i
        for j in range(w2):
            left = 2 * j
            for k in range(4):
                v = (np.float32(mosaic[top + (k >> 1), left + (k & 1)]) - black[k]) / scale[k]
                if v < zero:
                    v = zero
                elif v > one:
                    v = one
                out[i, j, k] = v


@numba.njit(cache=True, nogil=True)
def denormalise_rows(planes, scale, black, white, mosaic, y0, y1):
    """`unpack_bayer(rint(clip(planes*scale + black, 0, white)).astype(u16))`.

    Writes the mosaic in place, which is what `denoise_raw_inplace` promises its
    caller. `np.rint` is round-half-to-even, the same tie-break numpy's is --
    worth saying because the obvious `int(x + 0.5)` is not, and the difference
    lands on every pixel whose level sits exactly halfway.
    """
    zero = np.float32(0.0)
    w2 = planes.shape[1]
    for i in range(y0, y1):
        top = 2 * i
        for j in range(w2):
            left = 2 * j
            for k in range(4):
                v = planes[i, j, k] * scale[k] + black[k]
                if v < zero:
                    v = zero
                elif v > white:
                    v = white
                mosaic[top + (k >> 1), left + (k & 1)] = np.uint16(np.float32(np.rint(v)))


@numba.njit(cache=True, nogil=True)
def to_levels_rows(planes, span, black, full, out, y0, y1):
    """`clip(planes*span + black, 0, full)`: normalised back to sensor levels.

    Black included, and the multiply before the add, because that is the order
    `SonyRawNRDenoiser` ran it in when the kernels were scored against the
    engine (see `SensorLevels`).
    """
    zero = np.float32(0.0)
    w2 = planes.shape[1]
    for i in range(y0, y1):
        for j in range(w2):
            for k in range(4):
                v = planes[i, j, k] * span[k]
                v = v + black[k]
                if v < zero:
                    v = zero
                elif v > full:
                    v = full
                out[i, j, k] = v


@numba.njit(cache=True, nogil=True)
def unscale_rows(planes, black, span, y0, y1):
    """`(planes - black) / span` in place: sensor levels back to normalised."""
    w2 = planes.shape[1]
    for i in range(y0, y1):
        for j in range(w2):
            for k in range(4):
                planes[i, j, k] = (planes[i, j, k] - black[k]) / span[k]
