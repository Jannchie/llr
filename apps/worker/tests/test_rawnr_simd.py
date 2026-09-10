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
    BASE_GREEN_OTHER,
    BASE_GREEN_OWN,
    BASE_RB,
    BLEND_NEUTRAL,
    CROSS_GREEN,
    DETAIL_GAIN,
    DETAIL_LIMIT,
    FILT_MARGIN,
    OFFSET_GREEN,
    OFFSET_RB,
    OUTPUT_CEILING,
    PHASE_MARGIN,
    TABLE_SIZE,
    analysis_green,
    analysis_rb,
    blend_table_value,
    denoise_phase_green,
    denoise_phase_rb,
    filt,
    iso_strength,
)


def _flat_thresholds(value: int = 50) -> np.ndarray:
    return np.full(TABLE_SIZE, value, np.int32)


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


def test_the_green_analysis_reads_the_other_phase_on_the_right_diagonal() -> None:
    """The two phases' cross taps are point reflections of each other.

    They sit on opposite Bayer diagonals, so each sees the other's four nearest
    samples on the opposite side. Getting the sign of `dx` wrong here was worth
    7 levels on every green pixel -- it still explained 94% of the variance and
    still looked like a plausible image, but it pulled green away from red and
    blue and showed up as magenta/green fringing at a highlight edge.
    """
    a, b = CROSS_GREEN
    assert len(a) == len(b) == 4
    assert {(-dy, -dx) for dy, dx in a} == set(b)
    # All four must be the *nearest* diagonal neighbours, not a wider ring.
    for dy, dx in (*a, *b):
        assert max(abs(dy), abs(dx)) <= 1


def test_the_two_green_phases_analyse_differently() -> None:
    """`phase` has to reach the cross taps rather than being quietly dropped."""
    rng = np.random.default_rng(11)
    own = rng.normal(4000.0, 80.0, (24, 24)).astype(np.float32)
    other = rng.normal(4000.0, 80.0, (24, 24)).astype(np.float32)
    d0, r0 = analysis_green(own, other, OFFSET_GREEN, 0)
    d1, r1 = analysis_green(own, other, OFFSET_GREEN, 1)
    assert not np.array_equal(d0, d1)
    assert not np.array_equal(r0, r1)


def test_the_green_comparison_base_has_25_members_across_both_phases() -> None:
    """25 members, 13 own and 12 cross, at a measured 1/25 each.

    The count is not incidental: with `blend/1024 = 1/2` it makes every member
    weigh (1 - 1/2)/25 = 0.02, which is what each one measured at (0.0202), and
    the centre 0.52 against a measured 0.525. Drop or add a member and the whole
    table is at the wrong scale.
    """
    for phase, cross in enumerate(BASE_GREEN_OTHER):
        assert len(BASE_GREEN_OWN) + len(cross) == 25, phase
    assert len(BASE_GREEN_OWN) == 13
    assert (0, 0) in BASE_GREEN_OWN, "the centre is a member as well as the blend term"
    # Every member has to be reachable within the margin the filter trims.
    for dy, dx in (*BASE_GREEN_OWN, *BASE_GREEN_OTHER[0], *BASE_GREEN_OTHER[1]):
        assert max(abs(dy), abs(dx)) <= FILT_MARGIN


def test_the_green_base_is_neither_symmetric_nor_the_tap_set() -> None:
    """It looks wrong. It is not -- and 'fixing' it costs 99.85% -> 56%.

    Each of these was measured directly, by raising one position at a time and
    reading where the tap ring's accept/reject transition moved
    (sony_repro/tools/green_base_probe.py). The asymmetry is the finding.
    """
    assert (2, 1) in BASE_GREEN_OWN and (-2, 1) not in BASE_GREEN_OWN
    assert (0, 1) not in BASE_GREEN_OWN, "the centre row holds only the centre"
    # None of the outer taps are members, which is what rules out "the base is
    # the tap set" -- the reading that two earlier experiments disagreed over.
    outer = {(dy, dx) for dy in (-4, -2, 0, 2, 4) for dx in (-4, -2, 0, 2, 4)
             if max(abs(dy), abs(dx)) == 4}
    assert outer.isdisjoint(BASE_GREEN_OWN)
    # The two phases genuinely differ; sharing one table is the bug this guards.
    assert set(BASE_GREEN_OTHER[0]) != set(BASE_GREEN_OTHER[1])
    want_rb = tuple((dy * 2, dx * 2) for dy in (-1, 0, 1) for dx in (-1, 0, 1))
    assert want_rb == BASE_RB


def test_the_green_phases_denoise_differently() -> None:
    """`phase` has to reach the base table -- silently ignoring it is the risk.

    Swapping the two tables still produces a plausible-looking image; it just
    reproduces the engine on 56% of points instead of 99.85%. So the guard is
    that the two phases disagree at all on the same input.
    """
    rng = np.random.default_rng(7)
    plane = rng.normal(4000.0, 60.0, (40, 40)).astype(np.float32)
    other = rng.normal(4000.0, 60.0, (40, 40)).astype(np.float32)
    table = _flat_thresholds(30)
    a = denoise_phase_green(plane, other, table, phase=0)
    b = denoise_phase_green(plane, other, table, phase=1)
    assert a.shape == b.shape
    assert not np.array_equal(a, b)


def test_a_cross_plane_base_without_the_other_plane_is_refused() -> None:
    """Falling back to own-plane-only would score 47.9% and look almost right."""
    ref = np.full((30, 30), 4000.0, np.float32)
    with pytest.raises(ValueError, match="other green phase"):
        filt(np.zeros_like(ref), ref, _flat_thresholds(),
             base_own=BASE_GREEN_OWN, base_other=BASE_GREEN_OTHER[0])


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


def test_a_hard_step_keeps_the_centre_because_that_tap_is_never_tested() -> None:
    """At a hard step every *other* tap is rejected -- the centre still stands.

    `base` is centre-dominated, so at a step much larger than the threshold it
    lands between the two populations and nothing in the neighbourhood is within
    `thr`. This used to be the `count == 0` case, and writing 0 there put raw
    level 0 into 0.45% of a real ISO 2000 frame.

    It is not a special case at all: the engine never thresholds the centre tap
    (24 `vandnps`/`vmovmskps` pairs against 25 taps), so `count` is at least 1
    everywhere and the pixel falls back to its own value by construction.
    """
    n = 48
    step = np.tile(np.where(np.arange(n)[None, :] < n // 2, 2000.0, 6000.0), (n, 1))
    out = denoise_phase_rb(step.astype(np.float32), _flat_thresholds(120))
    assert float(out.min()) > 1900.0, float(out.min())
    assert float(out.max()) < 6100.0, float(out.max())


def test_the_threshold_lookup_clamps_to_the_table_not_to_level_max() -> None:
    """A ref above LEVEL_MAX must still reach its own table entry.

    The engine's ceiling on this lookup is 262143 (the constant its `vminps`
    broadcasts), far above anything the analysis can emit -- which already
    clamps ref to [0, 32767]. So it never truncates the index. Clamping to
    LEVEL_MAX instead folds every bright ref onto one entry; it changes nothing
    on frames whose ref tops out in the low thousands, which is exactly why it
    survived unnoticed.
    """
    n = 24
    hi = 20000.0                    # above LEVEL_MAX, inside the table
    ref = np.full((n, n), hi, np.float32)
    table = _flat_thresholds(10)
    table[int(hi)] = 4000           # only reachable without the LEVEL_MAX clamp
    # One deviating tap, 100 above the flat field. Every centre here looks up
    # table[20000] under the correct clamp -- threshold 4000, so that tap is
    # accepted and averaged in; clamped to LEVEL_MAX the threshold is 10 and it
    # is rejected, leaving the neighbours untouched.
    c = n // 2
    ref[c, c] = hi + 100.0
    out = filt(np.zeros_like(ref), ref, table, gain=0)
    # Read a neighbour that has the deviating pixel among its taps, not the
    # pixel itself -- the centre tap is unconditional, so it proves nothing.
    got = float(out[c - FILT_MARGIN, c - FILT_MARGIN + 2])
    assert got > hi + 1.0, got


def test_the_centre_tap_is_never_thresholded() -> None:
    """The one thing that took red/blue from 99.97% to 100.0000% bit-identical.

    Directly: an isolated pixel far outside the threshold, with a flat
    neighbourhood. Every other tap is rejected against it, so if the centre were
    thresholded too the output would be the neighbourhood level; because it is
    not, the output is the pixel's own value (minus the offset).

    Without this, one frame's green sat at 89.6% (sony_repro/tools/
    thr_source_test.py) -- it is worth far more than "one tap in 25" implies,
    because `base` is centre-dominated and so the centre is rejected exactly
    where the neighbourhood already was.
    """
    # Straight at `filt`, not through `denoise_phase_rb`: the analysis step is a
    # low-pass, so by the time the filter saw it the isolated pixel would have
    # been smeared into its neighbours and the test would prove nothing.
    n = 40
    ref = np.full((n, n), 3000.0, np.float32)
    c = n // 2
    ref[c, c] = 12000.0            # far beyond the threshold below
    out = filt(np.zeros_like(ref), ref, _flat_thresholds(100), gain=0)
    got = float(out[c - FILT_MARGIN, c - FILT_MARGIN])
    assert got == pytest.approx(12000.0, abs=1.0), got


def test_the_output_stays_on_the_engines_scale() -> None:
    """Clamped to 0..262143, which is the constant the engine broadcasts.

    Not 0..16383. The 14-bit figure is the *working scale* of the data, not the
    ceiling this stage applies -- assuming the two were the same is exactly the
    mistake `OUTPUT_CEILING` documents. Nothing measured reaches it (outputs top
    out near 3.3k), so the two are indistinguishable on real frames; the point
    of pinning it is that the code says what the engine says.
    """
    rng = np.random.default_rng(3)
    plane = rng.uniform(-500, 20000, (48, 48)).astype(np.float32)
    out = denoise_phase_rb(plane, _flat_thresholds(200))
    assert out.min() >= 0.0
    assert out.max() <= OUTPUT_CEILING


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
