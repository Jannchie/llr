"""`SegmentedMatrix.apply` as a compiled per-pixel kernel.

The numpy form (`linear_matrix.apply_reference`) gathers a 3x3 matrix per pixel
-- `table[index]` on a 33 MP frame is a 1.2 GB temporary -- and then einsums
it: 2.35 s per decode, the largest single cost of a re-decode and pure memory
traffic. Here each pixel reads its own nine coefficients out of the 36 KB
table and writes three floats, so the frame streams through once: 0.15 s on
twelve threads, and it is what every denoise or DCP change now waits on.

What is reproduced is the numpy path, in its own float32 order: the luma is
`(r + g) + b` over 3, the angle a float32 atan2 reduced modulo 2 pi, the bin
the truncated float32 product, and the three sums accumulate left to right.
The index is not the engine's own (linear_matrix.hue_index says why -- a
calibration LUT stands in for its fixed-point atan), so agreement with the
numpy path is measured rather than assumed: tests/test_linear_matrix.py holds
the two to the same index on all but a few pixels in a million and the output
to float32 rounding.
"""
from __future__ import annotations

import math

import numpy as np
from numba import njit

from .rawnr_simd import _run_rows

F = np.float32

_TWO_PI = F(2.0 * math.pi)


@njit(cache=True, nogil=True)
def _rows(a: np.ndarray, table: np.ndarray, lut: np.ndarray, r0: int, r1: int, out: np.ndarray) -> None:
    """Rows [r0, r1) of a (h, w, 3) float32 frame through the segment table."""
    n = lut.shape[0]
    n_f = F(n)
    n_index = table.shape[0]
    three = F(3.0)
    for y in range(r0, r1):
        for x in range(a.shape[1]):
            r = a[y, x, 0]
            g = a[y, x, 1]
            b = a[y, x, 2]
            # numpy: a.sum(axis=-1) / float32(3) -- (r + g) + b, then the divide.
            luma = ((r + g) + b) / three
            ang = F(math.atan2(r - luma, b - luma))
            # np.mod on float32 is Python's floor mod (fmod, then the sign
            # fix), which is what `%` compiles to on two float32s.
            ang = ang % _TWO_PI
            # ang is in [0, 2 pi), so only the top can overrun (at exactly 2 pi).
            bi = min(int(ang / _TWO_PI * n_f), n - 1)
            k = int(np.rint(lut[bi]))
            if k < 0:
                k = 0
            elif k > n_index - 1:
                k = n_index - 1
            for i in range(3):
                # einsum's float32 accumulation, left to right.
                out[y, x, i] = (table[k, i, 0] * r + table[k, i, 1] * g) + table[k, i, 2] * b


def apply(a: np.ndarray, table: np.ndarray, lut: np.ndarray, workers: int = 12,
          strip_rows: int = 256) -> np.ndarray:
    """A (..., 3) float32 array through the (1024, 3, 3) table, by row strips
    on threads (rawnr_simd._run_rows: the kernel is nogil, so they overlap)."""
    a = np.ascontiguousarray(a, dtype=np.float32)
    shape = a.shape
    # An image goes through as its rows; anything else (a list of pixels, a
    # tile with more axes) as one long row, which is one strip and one thread.
    frame = a if a.ndim == 3 else a.reshape(1, -1, shape[-1])
    h = frame.shape[0]
    out = np.empty_like(frame)
    table = np.ascontiguousarray(table, dtype=np.float32)
    lut = np.ascontiguousarray(lut, dtype=np.float32)
    _run_rows(h, lambda r0, r1: _rows(frame, table, lut, r0, r1, out), strip_rows, workers)
    return out.reshape(shape)


def warmup() -> None:
    """Compile the kernel so the first frame does not pay for it."""
    _rows(np.zeros((1, 2, 3), np.float32), np.zeros((2, 3, 3), np.float32),
          np.zeros(4, np.float32), 0, 1, np.zeros((1, 2, 3), np.float32))
