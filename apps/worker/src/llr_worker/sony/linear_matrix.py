"""LinearMatrix16 — Imaging Edge's segmented colour matrix.

This is the whole of Sony's colour step: no XYZ detour, no separate camera-to-
standard-space matrix. `ZcTaskSIMDLinearMatrix16` applies not one 3x3 but a
*hue-segmented* family of them:

1. The ARW carries a 6 x 16 knot table (sr2.py). The six rows are m01, m02, m10,
   m12, m20, m21, stored negated; the diagonal is implied by "every row sums to
   1", i.e. the matrix leaves neutrals untouched.
2. Those 16 knots expand to 1024 matrices by *circular* linear interpolation,
   knots sitting at 0, 64, ..., 960, with the last segment wrapping back to knot
   0. Verified against a live dump of the engine's expanded table: max diff 0.0.
3. Each pixel picks its matrix by a 0..1023 hue-angle index. The engine uses a
   fixed-point atan approximation; this uses a real atan2 plus a calibration LUT
   fitted to the engine's own indices (median error 1/1024).

Because every row sums to 1, the transform is scale-invariant and neutral-
preserving — which is why it can be fed camera RGB at any exposure scale.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

N_KNOT = 16
KNOT_STEP = 64
N_INDEX = N_KNOT * KNOT_STEP  # 1024
OFFDIAG = ((0, 1), (0, 2), (1, 0), (1, 2), (2, 0), (2, 1))

_DATA = Path(__file__).resolve().parent / "data"
_HUE_LUT: np.ndarray | None = None


def load_hue_lut() -> np.ndarray:
    """The bundled hue-angle -> segment-index calibration (cached)."""
    global _HUE_LUT
    if _HUE_LUT is None:
        with np.load(_DATA / "hue_index_lut.npz") as z:
            _HUE_LUT = z["lut"]
    return _HUE_LUT


def matrices_from_coeff(coeff: np.ndarray) -> np.ndarray:
    """(6, 16) knot coefficients -> (16, 3, 3) knot matrices."""
    o = -np.asarray(coeff, dtype=np.float32)
    m = np.zeros((o.shape[1], 3, 3), dtype=np.float32)
    for k, (i, j) in enumerate(OFFDIAG):
        m[:, i, j] = o[k]
    m[:, 0, 0] = 1 - o[0] - o[1]
    m[:, 1, 1] = 1 - o[2] - o[3]
    m[:, 2, 2] = 1 - o[4] - o[5]
    return m


def expand(knots: np.ndarray) -> np.ndarray:
    """(16, 3, 3) knot matrices -> (1024, 3, 3), circular linear interpolation."""
    k = np.asarray(knots, dtype=np.float32)
    idx = np.arange(N_INDEX)
    b = idx // KNOT_STEP
    t = ((idx - b * KNOT_STEP) / np.float32(KNOT_STEP)).astype(np.float32)
    lo, hi = k[b], k[(b + 1) % N_KNOT]
    return (lo + (hi - lo) * t[:, None, None]).astype(np.float32)


def hue_index(rgb: np.ndarray, lut: np.ndarray | None = None) -> np.ndarray:
    """Linear RGB -> 0..1023 hue-angle index.

    Index error near neutral is harmless: every segment matrix has rows summing
    to 1, so achromatic pixels map identically whichever segment they land in.
    """
    if lut is None:
        lut = load_hue_lut()
    a = np.asarray(rgb, dtype=np.float32)
    y = a.sum(axis=-1) / np.float32(3.0)
    ang = np.arctan2(a[..., 0] - y, a[..., 2] - y) % np.float32(2 * np.pi)
    n = len(lut)
    bi = np.clip((ang / np.float32(2 * np.pi) * n).astype(np.int32), 0, n - 1)
    return np.clip(np.rint(lut[bi]).astype(np.int32), 0, N_INDEX - 1)


class SegmentedMatrix:
    """An expanded segment table, ready to apply to an image."""

    def __init__(self, coeff: np.ndarray, hue_lut: np.ndarray | None = None):
        self.coeff = np.asarray(coeff, dtype=np.float32)
        self.knots = matrices_from_coeff(self.coeff)
        self.table = expand(self.knots)
        self._hue_lut = hue_lut

    @classmethod
    def from_arw(cls, path: str | Path, hue_lut: np.ndarray | None = None) -> SegmentedMatrix:
        from .sr2 import linear_matrix_coeff

        return cls(linear_matrix_coeff(path), hue_lut)

    def apply(self, rgb: np.ndarray) -> np.ndarray:
        """Apply the matching segment matrix to every pixel of a (..., 3) array.

        The compiled kernel (linear_matrix_numba) does the work; apply_reference
        below is the numpy form it reproduces, kept as the test's oracle.
        """
        from . import linear_matrix_numba

        lut = self._hue_lut if self._hue_lut is not None else load_hue_lut()
        return linear_matrix_numba.apply(np.asarray(rgb, dtype=np.float32), self.table, lut)

    def apply_reference(self, rgb: np.ndarray) -> np.ndarray:
        """The whole-array numpy form: a 3x3 gathered per pixel, then einsum.
        2.35 s and a 1.2 GB temporary on a 33 MP frame, which is why it is no
        longer what apply runs — but it is what the kernel is held to."""
        a = np.asarray(rgb, dtype=np.float32)
        m = self.table[hue_index(a, self._hue_lut)]
        return np.einsum("...ij,...j->...i", m, a)
