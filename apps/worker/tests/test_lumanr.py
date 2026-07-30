"""Edit.exe's two luma NR stages: the kernels, and what they refuse to do."""

import numpy as np
import pytest

from llr_worker.sony import lumanr
from llr_worker.sony.lumanr import (
    BSNR_MARGIN,
    MARGIN,
    WEIGHT_UNIT,
    _box_mean_9x9,
    _bsnr_strip,
    _median_3x3,
    _median_5x5,
    _ynr_strip,
    apply_luma_nr,
    binomial_lowpass,
    fade_for_amount,
    weight_for_amount,
)
from llr_worker.sony.rawnr import DetailRestore, NoiseModel

# DSC03036's own model and detail pair, both read from the file rather than
# invented; the model is the one verified equal to a captured engine table at
# all 32768 entries.
MODEL = NoiseModel(lo=0, hi=2560, base=13, slope=78)
RESTORE = DetailRestore(gain=249, limit=1023)


def thresholds() -> np.ndarray:
    return MODEL.threshold(np.arange(0x4000)).astype(np.int32)


# ── the slider mappings ────────────────────────────────────────────────────


@pytest.mark.parametrize(("ui", "expected"), [(0, 0), (25, 256), (50, 512), (75, 768), (100, 1024)])
def test_the_amount_slider_moves_the_reference_from_the_mean_to_the_centre(ui, expected):
    assert weight_for_amount(ui) == expected


def test_the_neutral_amount_puts_the_reference_halfway():
    """The captured tables all hold 512, so this is the value that has to come
    out at UI 50 for the reproduction to match a real frame."""
    assert weight_for_amount(50) == WEIGHT_UNIT // 2


@pytest.mark.parametrize("ui", [25, 40, 50, 75, 100])
def test_the_baseline_stage_runs_at_full_strength_from_a_quarter_up(ui):
    assert fade_for_amount(ui) == 0.0


def test_the_baseline_stage_only_switches_off_at_the_very_bottom():
    """The fade weights the *original*, so 1.0 means the stage does nothing.
    Getting this backwards inverts what the slider appears to do."""
    assert fade_for_amount(0) == pytest.approx(1.0)
    assert fade_for_amount(12.5) == pytest.approx(0.5)


# ── the kernels ────────────────────────────────────────────────────────────


def test_the_lowpass_is_the_binomial_kernel():
    delta = np.zeros((5, 5), dtype=np.int32)
    delta[2, 2] = 16
    out = binomial_lowpass(delta)
    assert out[2, 2] == 4
    assert out[1, 2] == out[3, 2] == out[2, 1] == out[2, 3] == 2
    assert out[1, 1] == out[1, 3] == out[3, 1] == out[3, 3] == 1


def test_the_lowpass_leaves_a_flat_plane_alone():
    flat = np.full((8, 8), 1234, dtype=np.int32)
    assert np.all(binomial_lowpass(flat)[1:-1, 1:-1] == 1234)


def test_the_box_mean_matches_a_brute_force_nine_by_nine():
    rng = np.random.default_rng(0)
    plane = rng.integers(0, 0x4000, (24, 24), dtype=np.int32)
    got = _box_mean_9x9(plane)
    for y in range(4, 20):
        for x in range(4, 20):
            want = int(plane[y - 4:y + 5, x - 4:x + 5].sum()) // 81
            assert got[y, x] == want, (y, x)


@pytest.mark.parametrize(("fn", "size"), [(_median_3x3, 3), (_median_5x5, 5)])
def test_the_median_networks_agree_with_a_plain_sort(fn, size):
    """The 3x3 path is a hand-written 19-comparator network rather than a sort,
    so it needs checking against the thing it replaces."""
    rng = np.random.default_rng(1)
    plane = rng.integers(0, 0x4000, (20, 20), dtype=np.int32)
    got = fn(plane)
    r = size // 2
    for y in range(r, 20 - r):
        for x in range(r, 20 - r):
            want = int(np.median(plane[y - r:y + r + 1, x - r:x + r + 1]))
            assert got[y, x] == want, (y, x)


def test_the_median_network_handles_ties():
    """Repeated values are where a mis-wired comparator usually still passes on
    random data."""
    plane = np.array([[5, 5, 5], [5, 1, 5], [5, 5, 5]], dtype=np.int32)
    assert _median_3x3(plane)[1, 1] == 5


# ── BSNR_Y ────────────────────────────────────────────────────────────────


def test_the_baseline_stage_leaves_a_flat_plane_untouched():
    """Sliced at BSNR_MARGIN rather than MARGIN: on its own this stage is valid
    that far in, and the wider MARGIN only exists because YNR reads its output."""
    flat = np.full((32, 32), 1000, dtype=np.int32)
    core = (slice(BSNR_MARGIN, -BSNR_MARGIN), slice(BSNR_MARGIN, -BSNR_MARGIN))
    out = _bsnr_strip(flat, thresholds(), 512, RESTORE.gain, RESTORE.limit, 0.0)
    assert np.array_equal(out[core], flat[core])


def test_the_baseline_stage_barely_denoises_with_a_bodys_own_gain():
    """Measured, not aspirational: gain 249 of 256 puts the noise back along
    with the detail, so this stage removes about a seventh of it. Pinned because
    it is the reason reproducing Edit's default faithfully is *not* a way to get
    a cleaner image."""
    rng = np.random.default_rng(2)
    core = (slice(BSNR_MARGIN, -BSNR_MARGIN), slice(BSNR_MARGIN, -BSNR_MARGIN))
    plane = np.clip(1200 + rng.normal(0, 30, (96, 96)), 0, 0x3FFF).astype(np.int32)
    out = _bsnr_strip(plane, thresholds(), 512, RESTORE.gain, RESTORE.limit, 0.0)
    ratio = out[core].std() / plane[core].std()
    assert 0.8 < ratio < 0.95


def test_the_baseline_stage_cannot_remove_an_isolated_impulse():
    """Its own edge-preserving test protects the impulse: every neighbour falls
    outside the threshold, so the count stays at one and the centre pixel comes
    through. What survives is exactly the 3x3 lowpass's attenuation.

    This is the same structural blind spot as the wavelet denoiser's garrote
    shrinkage, reached from the opposite direction, and it is why the specks a
    user complains about are not fixed by this stage."""
    plane = np.full((32, 32), 1200, dtype=np.int32)
    plane[16, 16] = 0x3FFF
    out = _bsnr_strip(plane, thresholds(), 512, RESTORE.gain, RESTORE.limit, 0.0)
    survives = (out[16, 16] - 1200) / (0x3FFF - 1200)
    assert survives > 0.2


def test_the_baseline_stages_fade_hands_back_the_original():
    rng = np.random.default_rng(3)
    plane = np.clip(1200 + rng.normal(0, 30, (32, 32)), 0, 0x3FFF).astype(np.int32)
    out = _bsnr_strip(plane, thresholds(), 512, RESTORE.gain, RESTORE.limit, 1.0)
    assert np.array_equal(out, plane)


def test_the_baseline_stage_keeps_both_sides_of_a_step():
    plane = np.full((32, 32), 500, dtype=np.int32)
    plane[:, 16:] = 3000
    out = _bsnr_strip(plane, thresholds(), 512, RESTORE.gain, RESTORE.limit, 0.0)
    assert np.all(out[BSNR_MARGIN:-BSNR_MARGIN, BSNR_MARGIN:12] == 500)
    assert np.all(out[BSNR_MARGIN:-BSNR_MARGIN, 20:-BSNR_MARGIN] == 3000)


# ── YNR ───────────────────────────────────────────────────────────────────


def test_the_median_stage_is_the_identity_at_the_neutral_amount():
    """Its percentage is (ui - 50) * 2, so nothing happens until the slider goes
    past halfway. Edit's default therefore has no median pass at all."""
    rng = np.random.default_rng(4)
    plane = np.clip(1200 + rng.normal(0, 30, (32, 32)), 0, 0x3FFF).astype(np.int32)
    assert np.array_equal(_ynr_strip(plane, 0, 0x1FFF, 3), plane)


def test_the_median_stage_clears_an_isolated_impulse():
    """What BSNR_Y cannot do. The residual is exactly 1 - percent/100, so a full
    percentage removes it outright."""
    plane = np.full((32, 32), 1200, dtype=np.int32)
    plane[16, 16] = 0x3FFF
    assert _ynr_strip(plane, 100, 0x1FFF, 3)[16, 16] == 1200


@pytest.mark.parametrize(("percent", "expected"), [(25, 0.75), (50, 0.5), (75, 0.25)])
def test_the_median_stages_impulse_residual_tracks_the_percentage(percent, expected):
    plane = np.full((32, 32), 1200, dtype=np.int32)
    plane[16, 16] = 0x3FFF
    out = _ynr_strip(plane, percent, 0x1FFF, 3)
    assert (out[16, 16] - 1200) / (0x3FFF - 1200) == pytest.approx(expected, abs=0.01)


def test_the_median_stages_mask_only_protects_strong_gradients():
    """The test is on the root-mean-square of the Sobel pair, so a clean step of
    amplitude d fires at d > threshold/sqrt(8) -- about 17.7% of full scale at
    the default. A 2500-count step is below that and is not protected."""
    weak = np.full((32, 32), 500, dtype=np.int32)
    weak[:, 16:] = 3000  # 2500 < 0x1fff/sqrt(8) ~ 2896
    strong = np.full((32, 32), 500, dtype=np.int32)
    strong[:, 16:] = 4000  # 3500 > 2896
    # An extra pixel on the edge shows whether the original was passed through:
    # inside the mask it survives, outside it is medianed away. bool() because a
    # numpy scalar's False is not Python's False singleton, so `is` would never
    # match.
    for plane, protected in ((weak, False), (strong, True)):
        p = plane.copy()
        p[16, 15] = plane[16, 15] + 400
        out = _ynr_strip(p, 100, 0x1FFF, 3)
        assert bool(out[16, 15] == p[16, 15]) is protected


def test_the_median_stage_keeps_both_sides_of_a_step():
    """Not because of the mask -- a median preserves a step by itself."""
    plane = np.full((32, 32), 500, dtype=np.int32)
    plane[:, 16:] = 3000
    out = _ynr_strip(plane, 100, 0x1FFF, 3)
    assert np.all(out[:, 2:14] == 500)
    assert np.all(out[:, 18:-2] == 3000)


def test_the_wider_median_removes_more():
    rng = np.random.default_rng(5)
    plane = np.clip(1200 + rng.normal(0, 30, (48, 48)), 0, 0x3FFF).astype(np.int32)
    core = (slice(4, -4), slice(4, -4))
    small = _ynr_strip(plane, 100, 0x1FFF, 3)
    wide = _ynr_strip(plane, 100, 0x1FFF, 5)
    assert wide[core].std() < small[core].std()


# ── the driver ────────────────────────────────────────────────────────────


def test_the_border_comes_back_untouched():
    """The engine writes only an inset region and does not clamp at the edges;
    callers must not read the ring."""
    rng = np.random.default_rng(6)
    plane = rng.integers(0, 0x4000, (64, 64), dtype=np.int32)
    out, _ = apply_luma_nr(plane, MODEL, RESTORE, 50)
    assert np.array_equal(out[:MARGIN], plane[:MARGIN])
    assert np.array_equal(out[-MARGIN:], plane[-MARGIN:])
    assert np.array_equal(out[:, :MARGIN], plane[:, :MARGIN])
    assert np.array_equal(out[:, -MARGIN:], plane[:, -MARGIN:])


def test_strips_leave_no_seam(monkeypatch):
    """Each strip is grown by the halo both stages read and then trimmed, so the
    result must not depend on how the frame was cut up."""
    rng = np.random.default_rng(7)
    plane = np.clip(1200 + rng.normal(0, 40, (140, 48)), 0, 0x3FFF).astype(np.int32)
    monkeypatch.setattr(lumanr, "STRIP_ROWS", 4096)
    whole, _ = apply_luma_nr(plane, MODEL, RESTORE, 80)
    monkeypatch.setattr(lumanr, "STRIP_ROWS", 16)
    striped, _ = apply_luma_nr(plane, MODEL, RESTORE, 80)
    assert np.array_equal(whole, striped)


def test_the_median_pass_is_skipped_below_the_neutral_amount():
    """Not just weighted to zero -- skipped, so a default request pays nothing
    for the expensive stage."""
    rng = np.random.default_rng(8)
    plane = np.clip(1200 + rng.normal(0, 30, (64, 64)), 0, 0x3FFF).astype(np.int32)
    _, stats = apply_luma_nr(plane, MODEL, RESTORE, 50)
    assert stats.ynr_percent == 0
    _, stats = apply_luma_nr(plane, MODEL, RESTORE, 100)
    assert stats.ynr_percent == 100


def test_a_stronger_amount_denoises_more():
    rng = np.random.default_rng(9)
    plane = np.clip(1200 + rng.normal(0, 30, (96, 96)), 0, 0x3FFF).astype(np.int32)
    core = (slice(MARGIN, -MARGIN), slice(MARGIN, -MARGIN))
    stds = [apply_luma_nr(plane, MODEL, RESTORE, ui)[0][core].std() for ui in (50, 75, 100)]
    assert stds[0] > stds[1] > stds[2]
