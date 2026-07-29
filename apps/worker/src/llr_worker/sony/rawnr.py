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
* **The curve is the one the shipped kernel uses.** `ZcTaskRawNRSIMD` builds
  its threshold tables somewhere other than its `exec`, so this was in doubt
  until they were read out of the running process: on two frames the captured
  tables match what this module computes from the tags entry for entry
  (11/45/53 at ISO 1250, 3/16/16 at ISO 100). See `rawnr_probe.py`.
* What is *not* reproduced is the filter that consumes the curve. Sony's runs
  on the Bayer mosaic split into four half-resolution phase planes, and its
  per-pixel maths is still undecoded (PIPELINE.md 7.13.2). So this exposes the
  camera's measurement for our own denoiser to use, and claims nothing about
  matching Edit.exe's output.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import numpy as np

from .sr2 import read_sr2_scalars

#: Full scale of the engine's working domain, and the ceiling it clamps
#: thresholds to. The plane is black-subtracted, so 0 is black and this is
#: white. The table the engine builds is 32768 entries long, but its filters
#: clamp the level to this before indexing (`rawnr_simd.py`), so the top half
#: is never reached and this — not the table's length — is the domain.
ENGINE_FULL_SCALE = 0x3FFF

LEVEL_LO_TAG = 0x78C5      # below this level the threshold is constant
LEVEL_HI_TAG = 0x78C6      # above it, constant again
BASE_COEFF_TAG = 0x78C7    # threshold at `lo`, before the strength scaling
SLOPE_COEFF_TAG = 0x78C8   # rise per level, in 1/4096ths

#: One strength per plane of the engine's three. Every checked-in frame has all
#: three equal — `test_sony.py` asserts it — so the model below reads the first
#: and speaks for the whole frame. All three are still required to be present,
#: so a body that carries only some of them falls through to the fitted path
#: rather than being read on a partial group.
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

    def noise_shape_at(self, level: np.ndarray | float) -> np.ndarray:
        """How the noise *grows* with the level, in [0, 1], shape only.

        This is :meth:`threshold` rescaled, and the name says shape rather than
        sigma on purpose: the engine's number is a sigma filter's inclusion
        radius, so it is proportional to the noise but by an unknown constant
        that mixes in how much smoothing Sony wanted. Consumers must take the
        curve's *shape* — how much noisier a highlight is than a shadow — and
        get the absolute scale from the frame, which is the easy half to
        measure and the half this cannot supply.

        The caller's plane must be black-subtracted and scaled so 1.0 is the
        sensor's white level, which is what `denoise.denoise_raw_inplace`
        normalises to.
        """
        scaled = np.asarray(level, dtype=np.float64) * ENGINE_FULL_SCALE
        return self.threshold(scaled).astype(np.float64) / ENGINE_FULL_SCALE


def noise_model(path: str | Path) -> NoiseModel | None:
    """This shot's noise model, or None for anything that does not carry one.

    None covers every "not a Sony RAW with these tags" case — a JPEG, a DNG, a
    body that predates the group — because the caller's answer to all of them is
    the same: fall back to fitting the noise from the image.
    """
    try:
        st = Path(path).stat()
    except OSError:
        return None
    return _cached_model(str(path), st.st_size, int(st.st_mtime_ns))


@lru_cache(maxsize=8)
def _cached_model(path: str, size: int, mtime: int) -> NoiseModel | None:
    # Keyed on the file's identity like sharpness._cached_calibration: reaching
    # the tags means decrypting the SR2 block, and every render of the same file
    # asks for the same answer.
    try:
        tags = read_sr2_scalars(path, _MODEL_TAGS)
    except Exception:
        # The SR2 walk can fail at any step on a file that is not a Sony RAW,
        # not only at the missing-tag check.
        return None
    if not all(t in tags for t in _MODEL_TAGS):
        return None

    strength = tags[STRENGTH_TAGS[0]]
    fold = strength * _COEFF_NUMERATOR
    return NoiseModel(
        lo=tags[LEVEL_LO_TAG],
        hi=tags[LEVEL_HI_TAG],
        base=(fold * tags[BASE_COEFF_TAG]) >> _STRENGTH_SHIFT,
        slope=(fold * tags[SLOPE_COEFF_TAG]) >> _STRENGTH_SHIFT,
    )
