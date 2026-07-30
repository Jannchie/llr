"""Tests for the transcription of Edit.exe's `ZcTaskRawNRSIMD`.

The constants here are not preferences -- every one of them was read out of the
running engine, and the values are re-checked against a captured kernel context
by `sony_repro/tools/rawnr_simd.py --verify`. That capture is research data and
is not checked in, so what these tests can pin is the arithmetic and the
constants; the bit-level agreement with the engine lives with the capture.

The distinction matters because the engine's parameters have been adopted
without its filter twice before, and made the result worse both times. These
tests exist to stop the constants drifting back toward "what looks reasonable".
"""

from __future__ import annotations

import numpy as np
import pytest

from llr_worker.sony.rawnr_simd import (
    BLEND_NEUTRAL,
    DETAIL_GAIN,
    DETAIL_LIMIT,
    FILT_MARGIN,
    LEVEL_MAX,
    OFFSET_GREEN,
    OFFSET_RB,
    PHASE_MARGIN,
    analysis_green,
    analysis_rb,
    blend_table_value,
    denoise_phase_green,
    denoise_phase_rb,
    filt,
    iso_strength,
)


def _flat_thresholds(value: int = 50) -> np.ndarray:
    return np.full(1 << 15, value, np.int32)


def test_the_engine_constants_are_the_measured_ones() -> None:
    """Read out of the running process, not chosen.

    `gain` is the one worth guarding: the `0x78cc` tag reads 480 and 268 on the
    two frames checked, and using the tag here is exactly the mistake that made
    the result worse when it was tried against llr's wavelet. The SIMD path
    measured 256 -- detail passed through unchanged.
    """
    assert DETAIL_GAIN == 256
    assert DETAIL_LIMIT == 1023
    assert BLEND_NEUTRAL == 512
    assert (OFFSET_RB, OFFSET_GREEN) == (0, -512)


def test_a_constant_field_is_left_alone() -> None:
    """Nothing to denoise means nothing changed, on both branches.

    Detail is zero, every tap passes the threshold, and the mean of identical
    taps is the value itself -- so any offset or normalisation error shows up
    here as a shifted constant.
    """
    flat = np.full((64, 64), 4000.0, dtype=np.float32)
    out = denoise_phase_rb(flat, _flat_thresholds())
    assert np.allclose(out, 4000.0, atol=1e-3)
    out_g = denoise_phase_green(flat, flat.copy(), _flat_thresholds())
    assert np.allclose(out_g, 4000.0, atol=1e-3)


def test_the_margins_are_what_the_kernels_consume() -> None:
    """One pixel for the analysis, `FILT_MARGIN` for the filter.

    Pinned because getting this wrong is silent: an off-by-one here misaligns
    the output against the engine's without changing any single value, which is
    how a previous version of the reference reported a mismatch that was purely
    a crop error.
    """
    n = 40
    plane = np.random.default_rng(1).uniform(0, 8000, (n, n)).astype(np.float32)
    d, ref = analysis_rb(plane, OFFSET_RB)
    assert d.shape == ref.shape == (n - 2, n - 2)
    assert filt(d, ref, _flat_thresholds()).shape == (n - 2 - 2 * FILT_MARGIN,) * 2
    assert denoise_phase_rb(plane, _flat_thresholds()).shape == (n - 2 * PHASE_MARGIN,) * 2


def test_the_analysis_is_centre_minus_a_binomial_lowpass() -> None:
    """`d = (12c - 2*N4 - diag) / 16` on a plane where the answer is known.

    A linear ramp has zero second difference, so a symmetric low-pass returns
    the centre exactly and the detail must vanish. This catches a transposed or
    misweighted tap, which a noise test cannot see.
    """
    n = 32
    ramp = np.tile(np.arange(n, dtype=np.float32) * 100.0, (n, 1))
    d, ref = analysis_rb(ramp, OFFSET_RB)
    assert np.allclose(d, 0.0, atol=1e-3)
    assert np.allclose(ref, ramp[1:-1, 1:-1], atol=1e-3)


def test_the_green_analysis_weights_sum_to_the_engines_divisor() -> None:
    """Twelve own taps plus four cross taps at weight 3 make 24, over 28.

    Checked as a partition of unity: on a constant field the low-pass must
    return that constant, which holds only if the weights sum to the divisor.
    A ramp is not usable here -- the cross taps are offset half a pixel, so the
    green low-pass is deliberately not centred on its own sample.
    """
    flat = np.full((32, 32), 1234.0, dtype=np.float32)
    d, ref = analysis_green(flat, flat.copy(), OFFSET_GREEN)
    assert np.allclose(d, 0.0, atol=1e-3)
    assert np.allclose(ref, 1234.0 + OFFSET_GREEN, atol=1e-3)


def test_the_detail_gain_is_what_limits_how_much_it_denoises() -> None:
    """The averaging is strong; the engine then puts the detail straight back.

    At `gain = 256` the restored term is `d` itself -- the centre minus its own
    low-pass, i.e. the very high-frequency content the sigma filter just
    averaged out. So the stage removes far less than its averaging alone would,
    and that is the transcribed behaviour, not a shortfall: the engine was
    measured changing 0.46% of the signal on an ISO 100 tile.

    It is also the mechanism behind the note in `rawnr.DETAIL_GAIN_UNIT` --
    Edit's RAW stage costs about a tenth of the fine detail where llr's wavelet
    costs most of it. Asserted as a comparison between gains rather than as an
    absolute ratio, so it pins the cause rather than one frame's number.
    """
    rng = np.random.default_rng(7)
    n = 64
    clean = np.full((n, n), 4000.0, dtype=np.float32)
    noisy = clean + rng.normal(0, 30, clean.shape).astype(np.float32)
    m = PHASE_MARGIN
    ref = clean[m:n - m, m:n - m]
    thr = _flat_thresholds(200)
    before = float(np.std(noisy[m:n - m, m:n - m] - ref))
    without = float(np.std(denoise_phase_rb(noisy, thr, gain=0) - ref))
    with_detail = float(np.std(denoise_phase_rb(noisy, thr) - ref))
    # Averaging alone is a real denoiser.
    assert without < 0.35 * before, (before, without)
    # Putting the detail back gives most of the noise back with it.
    assert with_detail > 2.5 * without, (without, with_detail)
    # It is still a net reduction, just a modest one.
    assert with_detail < before, (before, with_detail)


def test_a_step_larger_than_the_threshold_survives() -> None:
    """Taps across the step are rejected, so the two levels stay apart.

    Measured well clear of the tap footprint on both sides. Near the step
    itself this operator has a degenerate case -- see
    `test_a_hard_step_can_reject_every_tap` -- which is why the edge's
    neighbourhood is excluded here rather than asserted on.
    """
    rng = np.random.default_rng(7)
    n = 64
    clean = np.tile(np.where(np.arange(n)[None, :] < n // 2, 2000.0, 6000.0), (n, 1))
    noisy = clean.astype(np.float32) + rng.normal(0, 30, (n, n)).astype(np.float32)
    out = denoise_phase_rb(noisy, _flat_thresholds(120))
    m = PHASE_MARGIN
    left = float(out[:, : n // 2 - m - 8].mean())
    right = float(out[:, n // 2 - m + 8:].mean())
    assert abs(left - 2000.0) < 30.0, left
    assert abs(right - 6000.0) < 30.0, right


def test_a_hard_step_can_reject_every_tap() -> None:
    """Characterises the `count == 0` case rather than pretending it cannot happen.

    `base` is a mix of the centre low-pass and its 3x3 mean, so at a step much
    larger than the threshold it lands *between* the two populations and no tap
    is within `thr` of it. The reference divides by `max(count, 1)`, which
    yields 0 there.

    This is transcribed behaviour, not chosen behaviour, and it agrees with the
    captured engine on 99.97% of points -- but the engine's own SIMD divide at
    zero was never read out, so this test records the case rather than blessing
    it. If a frame ever shows black pixels at hard edges, this is the first
    place to look.
    """
    n = 48
    step = np.tile(np.where(np.arange(n)[None, :] < n // 2, 2000.0, 6000.0), (n, 1))
    out = denoise_phase_rb(step.astype(np.float32), _flat_thresholds(120))
    assert float(out.min()) == 0.0, "the degenerate case no longer reproduces"


def test_the_output_stays_on_the_engines_scale() -> None:
    """Clamped to 0..16383 -- the engine's 14-bit working range."""
    rng = np.random.default_rng(3)
    plane = rng.uniform(-500, 20000, (48, 48)).astype(np.float32)
    out = denoise_phase_rb(plane, _flat_thresholds(200))
    assert out.min() >= 0.0
    assert out.max() <= LEVEL_MAX


@pytest.mark.parametrize(("iso", "want"), [
    (50, 0.4), (100, 0.4), (400, 0.4), (1600, 1.0), (4000, 1.0), (25600, 1.0),
])
def test_the_iso_strength_plateaus_are_the_engines(iso: float, want: float) -> None:
    """Flat at 0.4 to ISO 400, flat at 1.0 from 1600 (`0x39fb4c..0x39fbbe`).

    This is why the stage does almost nothing at base ISO -- measured at 0.46%
    of the signal on an ISO 100 tile against 7.97% at ISO 8000.
    """
    assert iso_strength(iso) == pytest.approx(want)


def test_the_iso_strength_is_linear_in_iso_between_the_plateaus() -> None:
    """Linear in ISO itself, not in its logarithm.

    Worth pinning because log-spacing is the intuitive choice for an ISO ramp
    and would put the midpoint at ISO 800 rather than 1000.
    """
    assert iso_strength(1000) == pytest.approx(0.7)
    assert iso_strength(800) == pytest.approx(0.6)


@pytest.mark.parametrize(("ui", "want"), [(0, 0), (50, BLEND_NEUTRAL), (100, 1024)])
def test_the_blend_table_matches_the_measured_slider_points(ui: int, want: int) -> None:
    """`trunc((t + 100) / 200 * 1024)`, checked at the three measured points."""
    assert blend_table_value(ui) == want


def test_the_blend_weight_selects_between_the_two_references() -> None:
    """0 is the neighbourhood mean, 1024 is the centre low-pass.

    The neutral 512 sits between them, and this is the axis the Manual Noise
    Reduction slider moves -- so a sign error here would invert that control.
    """
    rng = np.random.default_rng(5)
    plane = rng.uniform(1000, 5000, (48, 48)).astype(np.float32)
    d, ref = analysis_rb(plane, OFFSET_RB)
    thr = _flat_thresholds(80)
    lo = filt(d, ref, thr, blend=0)
    mid = filt(d, ref, thr, blend=BLEND_NEUTRAL)
    hi = filt(d, ref, thr, blend=1024)
    assert not np.allclose(lo, hi)
    # The neutral setting lies between the two extremes rather than outside.
    assert float(np.abs(mid - lo).mean()) < float(np.abs(hi - lo).mean())
