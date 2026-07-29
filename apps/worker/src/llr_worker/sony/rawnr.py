"""The noise model Sony's camera measures and writes into every ARW.

`llr_worker.denoise` fits `var = gain*mean + read_var` from the frame itself,
which works on any sensor but has to separate noise from texture to do it. Sony
does not have to: the body knows its own sensor and its own ISO, and it writes
the answer into the RAW. Reading it is strictly better information than fitting
it, for the frames that carry it.

Edit.exe's `ZcTaskRawNRHalf` turns those tags into a 32768-entry table indexed
by the *local signal level* (PIPELINE.md 7.13):

    base   = strength * base_k  * 3 >> 8
    slope  = strength * slope_k * 3 >> 8
    thr(v) = clamp(((clamp(v, lo, hi) - lo) * slope >> 12) + base, 0, 16383)

so the threshold is piecewise linear in the level: flat below `lo`, rising to
`hi`, flat above. Brighter pixels are allowed a larger deviation before they
count as signal — the usual shot-noise argument, though note the ramp is linear
in the level rather than in its square root, and it stops entirely at `hi`
(2048 of 16383 at base ISO, an eighth of full scale). Sony deliberately does
not raise the threshold further into the highlights.

What the number *is*: the inclusion radius of a sigma filter, i.e. roughly
k*sigma rather than sigma itself. Measured across the checked-in frames it runs
3..16 at ISO 100 and 11..53 at ISO 1250, on the engine's 14-bit scale.

Scope, stated plainly:

* The tag -> calibration mapping is read out of Edit.exe's parser at
  `+0x1611a5` and up, one tag per slot with no clamping. The `>256` branch that
  sits just past it belongs to Sharpness (`sharpness.py`), not to this group.
* These tags feed `ZcTaskRawNRHalf`. The kernel that actually runs on the
  execution path, `ZcTaskRawNRSIMD`, is a *different* implementation — float32
  AVX2 with a wider footprint — and its `exec` reads only the `0x1018..0x1028`
  group. Whether it reaches these thresholds by another route is unknown, so
  this module claims to expose the camera's noise model, not to reproduce what
  Edit.exe renders.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .sr2 import read_sr2_scalars

#: Full scale of the engine's working domain, and the ceiling it clamps
#: thresholds to. The plane is black-subtracted, so 0 is black and this is white.
ENGINE_FULL_SCALE = 0x3FFF

LEVEL_LO_TAG = 0x78C5      # below this level the threshold is constant
LEVEL_HI_TAG = 0x78C6      # above it, constant again
BASE_COEFF_TAG = 0x78C7    # threshold at `lo`, before the strength scaling
SLOPE_COEFF_TAG = 0x78C8   # rise per level, in 1/4096ths

#: One strength per plane of the engine's three. Every checked-in frame has all
#: three equal, so nothing here has yet been able to tell them apart; they are
#: kept separate rather than collapsed so a frame that differs would show it.
STRENGTH_TAGS = (0x78C9, 0x78CA, 0x78CB)

_MODEL_TAGS = (LEVEL_LO_TAG, LEVEL_HI_TAG, BASE_COEFF_TAG, SLOPE_COEFF_TAG,
               *STRENGTH_TAGS)

_STRENGTH_SHIFT = 8   # the >> 8 in base/slope
_SLOPE_SHIFT = 12     # the >> 12 in the ramp
_COEFF_NUMERATOR = 3  # the * 3 both coefficients carry


@dataclass(frozen=True)
class NoiseModel:
    """A frame's threshold-versus-level curve, on the engine's 14-bit scale.

    `base` and `slope` already have the plane's strength folded in, exactly as
    the engine folds it before building its table, so the curve is a plain
    piecewise-linear function of the level and nothing else is needed to
    evaluate it.
    """

    lo: int
    hi: int
    base: int
    slope: int

    def threshold(self, level: np.ndarray | float) -> np.ndarray:
        """Threshold for a local signal level, both on the 0..16383 scale.

        Integer arithmetic throughout, matching the engine's table build: the
        shifts are floor divisions and every term here is non-negative, so
        there is no rounding to disagree about.
        """
        x = np.clip(np.asarray(level, dtype=np.int64), self.lo, self.hi)
        raw = (((x - self.lo) * self.slope) >> _SLOPE_SHIFT) + self.base
        return np.clip(raw, 0, ENGINE_FULL_SCALE)

    def threshold_normalised(self, level: np.ndarray | float) -> np.ndarray:
        """As :meth:`threshold`, but with both sides in [0, 1].

        The caller's plane must be black-subtracted and scaled so that 1.0 is
        the sensor's white level — the domain `denoise.denoise_raw_inplace`
        normalises to. That is the same domain the engine's plane is in *if* no
        gain has been applied before this stage; nothing here verifies that, so
        treat the absolute scale as approximate and the shape as exact.
        """
        scaled = np.asarray(level, dtype=np.float64) * ENGINE_FULL_SCALE
        return self.threshold(scaled).astype(np.float64) / ENGINE_FULL_SCALE

    def table(self) -> np.ndarray:
        """The engine's own 32768-entry lookup, for comparing against a dump."""
        return self.threshold(np.arange(1 << 15, dtype=np.int64))


def noise_model(path: str | Path, plane: int = 0) -> NoiseModel | None:
    """This shot's noise model, or None for anything that does not carry one.

    None covers every "not a Sony RAW with these tags" case — a JPEG, a DNG, a
    body that predates the group — because the caller's answer to all of them is
    the same: fall back to fitting the noise from the image.
    """
    if not 0 <= plane < len(STRENGTH_TAGS):
        raise ValueError(f"plane must be 0..{len(STRENGTH_TAGS) - 1}, got {plane}")
    try:
        tags = read_sr2_scalars(path, _MODEL_TAGS)
    except Exception:
        # The SR2 walk can fail at any step on a file that is not a Sony RAW,
        # not only at the missing-tag check.
        return None
    if not all(t in tags for t in (*_MODEL_TAGS[:4], STRENGTH_TAGS[plane])):
        return None

    strength = tags[STRENGTH_TAGS[plane]]
    fold = strength * _COEFF_NUMERATOR
    return NoiseModel(
        lo=tags[LEVEL_LO_TAG],
        hi=tags[LEVEL_HI_TAG],
        base=(fold * tags[BASE_COEFF_TAG]) >> _STRENGTH_SHIFT,
        slope=(fold * tags[SLOPE_COEFF_TAG]) >> _STRENGTH_SHIFT,
    )
