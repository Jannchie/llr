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
* The filter that consumes the curve is `rawnr_simd.py`, transcribed from the
  AVX2 kernels: it runs on the Bayer mosaic split into four half-resolution
  phase planes (PIPELINE.md 7.13.3). Its analysis step is bit-identical to the
  engine's and end to end it explains 100.00% of what the engine did on a
  captured tile. What is still missing there is green's *filter* kernel
  (`0x3a0c30`), decoded only as far as its analysis.
* This module on its own only exposes the camera's measurement, for llr's
  wavelet denoiser to use, and claims nothing about matching Edit.exe.
  Reproducing Edit means using `rawnr_simd.py` as well -- the two halves are
  not separable, and adopting a parameter from one into the other has twice
  made the result worse (see DETAIL_GAIN_UNIT below).
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import numpy as np

from .sr2 import read_sr2_scalars

#: Full scale of the engine's working domain, and the ceiling it clamps
#: thresholds to. The table the engine builds is 32768 entries long, but its
#: filters clamp the level to this before indexing (`rawnr_simd.py`), so the top
#: half is never reached and this — not the table's length — is the domain.
#:
#: ⚠️ The level indexing this curve is the sensor's **raw** value, black level
#: still in it. This said "black-subtracted, so 0 is black" until it was
#: measured, and that was wrong: feeding `rawnr_simd` black-subtracted planes
#: drives green's `ref = c - d + OFFSET_GREEN` negative in the shadows, where
#: the clamp at zero pins it and the filter's closing `- offset` lifts the whole
#: plane by 512 — measured as a +148 level shift and a 38% change rate, against
#: 2.8-3.9% for red and blue in that same run. On raw values all four phases
#: agree instead: 0.78-1.17% changed, mean drift within ±0.3 of zero. This
#: body's black level of 512 is exactly `-OFFSET_GREEN`: that step *is* the
#: engine's own black alignment, which is why only green carries an offset.
#: See `sony_repro/tools/rawnr_simd_domain.py` for the adjudication.
#:
#: `noise_shape_at` below is unaffected — it only promises a shape, and its
#: caller (llr's wavelet) works on normalised planes and fits its own scale.
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
#:
#: The same holds for the gain and limit triples below, checked over 65 frames
#: spanning ISO 100 to 8000: all three planes carry identical values in every
#: one, so reading index 0 for those is equally safe. Worth knowing because a
#: per-plane difference would land squarely on the colour differences, which is
#: where the remaining gap to Edit lives — it was a natural suspect and it is
#: not the cause.
STRENGTH_TAGS = (0x78C9, 0x78CA, 0x78CB)

#: Detail re-injection, one per plane. Sony's filter smooths a *lowpassed*
#: plane and then adds the original finest-scale detail back as
#: ``clamp(d * gain / 256, ±limit)``, so `gain` is how much of the fine detail
#: survives denoising and `limit` caps the excursion at an edge (a halo
#: limiter). Measured on the bodies here `gain` sits just under 256, i.e. Sony
#: puts nearly all of it back — which is why its RAW-domain stage removes only
#: about a tenth of the fine detail (PIPELINE.md 7.13, notes/static-rawnr.md 7).
DETAIL_GAIN_TAGS = (0x78CC, 0x78CD, 0x78CE)
DETAIL_LIMIT_TAGS = (0x78CF, 0x78D0, 0x78D1)

#: `gain` is 8.8 fixed point, so this is "restore all of it".
#:
#: The engine caps it there. The tag runs 216..433 over the frames here, and
#: probing RawNRSIMD's parameter block gives 256 for tags of 480 and 268 but the
#: tag itself for 249 and 216 — so what it loads is ``min(tag, 256)``, which
#: resolves the "constant or clamped?" question left open in
#: notes/static-rawnr.md 5.1 (both frames sampled there had tags above 256, so
#: they could not tell the two apart).
#:
#: ⚠️ **Do not apply that clamp here.** It was tried and measured: llr's luma
#: detail on DSC02995 went 1.685 -> 1.528 against Edit's 1.872, i.e. further
#: *below* Edit rather than closer, and the colour differences did not move
#: (1.211/1.049 -> 1.188/1.066). The reason is that this gain feeds a wavelet,
#: not Sony's sigma filter, and the two do not remove the same amount of detail
#: to begin with — Edit's RAW stage costs about a tenth of the fine detail where
#: ours costs most of it. Above 256 the tag is therefore doing useful work as
#: compensation. Faithful to the engine's *parameter* is not the same as
#: faithful to its *result*, and the result is what is being reproduced.
DETAIL_GAIN_UNIT = 256

_MODEL_TAGS = (LEVEL_LO_TAG, LEVEL_HI_TAG, BASE_COEFF_TAG, SLOPE_COEFF_TAG,
               *STRENGTH_TAGS)
_DETAIL_TAGS = (DETAIL_GAIN_TAGS[0], DETAIL_LIMIT_TAGS[0])

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
        normalises to. Note this differs from :meth:`threshold`, which is
        indexed by the sensor's raw level with black still in it — see
        ENGINE_FULL_SCALE. The rescaling here is approximate for that reason:
        it maps a normalised level onto the engine's scale as if black were
        zero. Harmless for a shape, wrong for a threshold.
        """
        scaled = np.asarray(level, dtype=np.float64) * ENGINE_FULL_SCALE
        return self.threshold(scaled).astype(np.float64) / ENGINE_FULL_SCALE


@dataclass(frozen=True)
class DetailRestore:
    """How much fine detail the body puts back after denoising.

    ``gain`` is 8.8 fixed point (256 = all of it) and ``limit`` caps the
    restored excursion, both exactly as Edit.exe's RawNR uses them. The Edge
    Noise Reduction slider modulates the pair around these values; see
    :func:`for_edge_slider` for the mapping, which is read out of the engine
    rather than fitted.
    """

    gain: int
    limit: int

    @property
    def fraction(self) -> float:
        """``gain`` as a plain multiplier on the detail that was removed."""
        return self.gain / DETAIL_GAIN_UNIT

    def limit_in_thresholds(self, model: NoiseModel) -> float:
        """``limit`` measured in units of the noise threshold at full signal.

        Both are on the engine's 14-bit scale, so their ratio is dimensionless
        and carries over to a denoiser working in any other units — which is
        what lets this drive `llr`'s wavelet stage without reproducing Sony's
        filter. Typically 15..25, i.e. the clamp only bites at a hard edge.
        """
        return self.limit / self._hi_threshold(model)

    @classmethod
    def from_dimensionless(cls, fraction: float, limit_in_thresholds: float,
                           model: NoiseModel) -> DetailRestore:
        """The inverse of :attr:`fraction` / :meth:`limit_in_thresholds`.

        Kept beside the forward pair so the two stay mutually inverse in one
        file; `denoise.SonyRawNRDenoiser` uses it to put the engine's own units
        back on a pair that travelled dimensionless.

        The gain clamp is the engine's: probing RawNRSIMD's parameter block
        returns min(tag, 256), not the tag (DETAIL_GAIN_UNIT). ⚠️ Its order
        against the Edge NR slider is *not* established -- all four probe
        points sat at neutral, where the two orders agree.
        """
        gain = min(round(float(fraction) * DETAIL_GAIN_UNIT), DETAIL_GAIN_UNIT)
        return cls(gain=gain,
                   limit=round(float(limit_in_thresholds) * cls._hi_threshold(model)))

    @staticmethod
    def _hi_threshold(model: NoiseModel) -> float:
        return max(float(model.threshold(model.hi)), 1.0)

    def for_edge_slider(self, ui: float) -> DetailRestore:
        """This pair as Edit.exe's Edge NR slider (0..100, 50 neutral) sets it.

        Read out of the engine, exact at every measured point once truncation
        toward zero is applied (notes/static-rawnr.md 7.1). Below neutral the
        gain climbs toward "restore everything" and the halo clamp opens up to
        8x; above neutral the gain falls to zero and the clamp stays put.
        """
        t = (float(ui) - 50.0) * 2.0
        if t >= 0.0:
            return DetailRestore(gain=int(self.gain * (1.0 - t / 100.0)), limit=self.limit)
        room = -t / 100.0
        return DetailRestore(
            gain=int(self.gain + (DETAIL_GAIN_UNIT - self.gain) * room),
            limit=int(self.limit + 7 * self.limit * room),
        )


def noise_model(path: str | Path) -> NoiseModel | None:
    """This shot's noise model, or None for anything that does not carry one.

    None covers every "not a Sony RAW with these tags" case — a JPEG, a DNG, a
    body that predates the group — because the caller's answer to all of them is
    the same: fall back to fitting the noise from the image.
    """
    return _read(path)[0]


def detail_restore(path: str | Path) -> DetailRestore | None:
    """This shot's detail re-injection pair, or None if the RAW has no group.

    Read alongside :func:`noise_model` — they share one decryption of the SR2
    block — but reported separately because they answer different questions:
    the curve says how much noise there is, this says how much of the fine
    detail Sony chose to keep afterwards.
    """
    return _read(path)[1]


def _read(path: str | Path) -> tuple[NoiseModel | None, DetailRestore | None]:
    try:
        st = Path(path).stat()
    except OSError:
        return None, None
    return _cached_read(str(path), st.st_size, int(st.st_mtime_ns))


@lru_cache(maxsize=8)
def _cached_read(path: str, size: int,
                 mtime: int) -> tuple[NoiseModel | None, DetailRestore | None]:
    # Keyed on the file's identity like sharpness._cached_calibration: reaching
    # the tags means decrypting the SR2 block, and every render of the same file
    # asks for the same answer. Both groups come out of the one walk.
    try:
        tags = read_sr2_scalars(path, (*_MODEL_TAGS, *_DETAIL_TAGS))
    except Exception:
        # The SR2 walk can fail at any step on a file that is not a Sony RAW,
        # not only at the missing-tag check.
        return None, None

    model = None
    if all(t in tags for t in _MODEL_TAGS):
        strength = tags[STRENGTH_TAGS[0]]
        fold = strength * _COEFF_NUMERATOR
        model = NoiseModel(
            lo=tags[LEVEL_LO_TAG],
            hi=tags[LEVEL_HI_TAG],
            base=(fold * tags[BASE_COEFF_TAG]) >> _STRENGTH_SHIFT,
            slope=(fold * tags[SLOPE_COEFF_TAG]) >> _STRENGTH_SHIFT,
        )

    detail = None
    if all(t in tags for t in _DETAIL_TAGS):
        # Deliberately the raw tag, not min(tag, DETAIL_GAIN_UNIT) — see that
        # constant's note for why the engine's own clamp does not belong here.
        detail = DetailRestore(gain=tags[_DETAIL_TAGS[0]], limit=tags[_DETAIL_TAGS[1]])
    return model, detail
