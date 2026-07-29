"""Bayer pack/unpack plumbing and the CFA-support guard."""

from types import SimpleNamespace

import numpy as np
import pytest

from llr_worker.denoise import (
    _LUMA_CHROMA,
    _VST,
    PassthroughDenoiser,
    WaveletDenoiser,
    _CurveVST,
    _plane_black_levels,
    _plane_colors,
    canonical_plane_order,
    cfa_is_bayer_2x2,
    denoise_raw_inplace,
    estimate_noise_model,
    get_denoiser,
    pack_bayer,
    unpack_bayer,
)

RGGB_PATTERN = np.array([[0, 1], [3, 2]], dtype=np.uint8)  # Sony A7C II layout
XTRANS_PATTERN = np.array(
    [
        [1, 1, 0, 1, 1, 2],
        [1, 1, 2, 1, 1, 0],
        [2, 0, 1, 0, 2, 1],
        [1, 1, 2, 1, 1, 0],
        [1, 1, 0, 1, 1, 2],
        [0, 2, 1, 2, 0, 1],
    ],
    dtype=np.uint8,
)


def make_raw(
    mosaic: np.ndarray,
    pattern: np.ndarray = RGGB_PATTERN,
    black: list[int] | None = None,
    white: int = 16383,
    top_margin: int = 0,
    left_margin: int = 0,
) -> SimpleNamespace:
    return SimpleNamespace(
        raw_image_visible=mosaic,
        raw_pattern=pattern,
        black_level_per_channel=black if black is not None else [512, 512, 512, 512],
        white_level=white,
        sizes=SimpleNamespace(top_margin=top_margin, left_margin=left_margin),
    )


def random_mosaic(rng: np.random.Generator, h: int = 8, w: int = 10) -> np.ndarray:
    return rng.integers(512, 16383, size=(h, w), dtype=np.uint16)


def test_pack_unpack_roundtrip_is_exact() -> None:
    rng = np.random.default_rng(7)
    mosaic = random_mosaic(rng, 64, 96)
    assert np.array_equal(unpack_bayer(pack_bayer(mosaic)), mosaic)


def test_pack_places_phases_by_parity() -> None:
    mosaic = np.arange(16, dtype=np.uint16).reshape(4, 4)
    planes = pack_bayer(mosaic)
    assert planes.shape == (2, 2, 4)
    assert np.array_equal(planes[..., 0], mosaic[0::2, 0::2])
    assert np.array_equal(planes[..., 3], mosaic[1::2, 1::2])


def test_passthrough_denoise_reproduces_mosaic_bit_for_bit() -> None:
    rng = np.random.default_rng(11)
    mosaic = random_mosaic(rng)
    original = mosaic.copy()
    stats = denoise_raw_inplace(make_raw(mosaic), PassthroughDenoiser())
    assert stats is not None
    assert stats.model == "passthrough"
    assert np.array_equal(mosaic, original)


def test_odd_trailing_row_and_column_left_untouched() -> None:
    rng = np.random.default_rng(3)
    mosaic = random_mosaic(rng, 9, 11)
    last_row = mosaic[-1].copy()
    last_col = mosaic[:, -1].copy()
    stats = denoise_raw_inplace(make_raw(mosaic), PassthroughDenoiser())
    assert stats is not None and (stats.height, stats.width) == (8, 10)
    assert np.array_equal(mosaic[-1], last_row)
    assert np.array_equal(mosaic[:, -1], last_col)


def test_non_bayer_cfa_is_skipped_without_mutation() -> None:
    rng = np.random.default_rng(5)
    mosaic = random_mosaic(rng, 12, 12)
    original = mosaic.copy()
    raw = make_raw(mosaic, pattern=XTRANS_PATTERN)
    assert not cfa_is_bayer_2x2(raw)
    assert denoise_raw_inplace(raw, WaveletDenoiser()) is None
    assert np.array_equal(mosaic, original)


def test_cfa_guard_accepts_plain_bayer() -> None:
    assert cfa_is_bayer_2x2(make_raw(np.zeros((4, 4), dtype=np.uint16)))
    assert not cfa_is_bayer_2x2(SimpleNamespace())


def test_plane_black_levels_gather_through_pattern() -> None:
    raw = make_raw(np.zeros((4, 4), dtype=np.uint16), black=[100, 200, 300, 400])
    # Pattern [[0,1],[3,2]] maps TL,TR,BL,BR -> colour 0,1,3,2.
    assert _plane_black_levels(raw).tolist() == [100, 200, 400, 300]


def test_plane_black_levels_roll_for_odd_margins() -> None:
    raw = make_raw(np.zeros((4, 4), dtype=np.uint16), black=[100, 200, 300, 400])
    # An odd top margin swaps the pattern rows: [[3,2],[0,1]].
    assert _plane_black_levels(raw, row_phase=1).tolist() == [400, 300, 100, 200]
    # An odd left margin swaps the columns: [[1,0],[2,3]].
    assert _plane_black_levels(raw, col_phase=1).tolist() == [200, 100, 300, 400]


def test_wavelet_denoiser_reduces_noise() -> None:
    rng = np.random.default_rng(19)
    clean = np.full((64, 64, 4), 0.4, dtype=np.float32)
    noisy = np.clip(clean + rng.normal(0.0, 0.05, clean.shape).astype(np.float32), 0.0, 1.0)
    denoised = WaveletDenoiser()(noisy)
    assert denoised.shape == noisy.shape
    assert float(np.std(denoised)) < float(np.std(noisy)) * 0.7
    assert abs(float(np.mean(denoised)) - 0.4) < 0.01


# ── Noise model / VST ──


def test_noise_model_recovers_planted_gain_and_read_noise() -> None:
    """Recovers the planted model to within the low-decile estimator's own bias.

    Taking the flattest tenth of blocks underestimates variance by design — it is
    what keeps scene structure out of the fit — so the fit runs low. Only the
    *shape* has to be right (noise equally strong at every brightness); the
    absolute scale is recalibrated from the wavelet MAD at shrinkage time.
    """
    rng = np.random.default_rng(23)
    gain, read_var = 2.0e-4, 4.0e-7
    # Steps 16 px wide, so every 8x8 block sits at one brightness: a ramp would
    # add its own in-block variance and land in the fitted read floor.
    steps = np.repeat(np.linspace(0.02, 0.9, 16, dtype=np.float32), 16)
    clean = np.repeat(steps[None, :], 256, axis=0)[..., None].repeat(4, axis=-1)
    noisy = clean + rng.normal(0.0, np.sqrt(gain * clean + read_var)).astype(np.float32)
    fit_gain, fit_read = estimate_noise_model(noisy)
    assert 0.5 * gain <= fit_gain <= 1.5 * gain
    assert 0.0 <= fit_read <= 5.0 * read_var


def test_noise_model_falls_back_to_gaussian_on_a_noiseless_frame() -> None:
    gain, read_var = estimate_noise_model(np.full((128, 128, 4), 0.5, dtype=np.float32))
    assert gain == 0.0
    assert read_var == pytest.approx(0.0, abs=1e-9)


@pytest.mark.parametrize(("gain", "read_var"), [(1.8e-4, 3e-7), (0.0, 1e-6)])
def test_vst_roundtrips_and_flattens_variance(gain: float, read_var: float) -> None:
    vst = _VST(gain, read_var)
    y = np.linspace(0.0, 1.0, 512, dtype=np.float32)
    assert np.allclose(vst.inverse(vst.forward(y)), y, atol=2e-3)

    # The point of the transform: noise std becomes level-independent.
    rng = np.random.default_rng(29)
    stds = []
    for level in (0.02, 0.2, 0.9):
        s = np.full(20000, level, dtype=np.float32)
        s = s + rng.normal(0.0, np.sqrt(gain * level + read_var), s.shape).astype(np.float32)
        stds.append(float(vst.forward(s).std()))
    assert max(stds) / min(stds) < 1.3
    assert all(0.7 < s < 1.4 for s in stds)


# ── Luma/chroma basis ──


def test_luma_chroma_basis_is_orthonormal() -> None:
    assert np.allclose(_LUMA_CHROMA @ _LUMA_CHROMA.T, np.eye(4), atol=1e-6)


def test_canonical_plane_order_follows_the_cfa() -> None:
    assert canonical_plane_order(["R", "G", "G", "B"]) == (0, 1, 2, 3)
    assert canonical_plane_order(["B", "G", "G", "R"]) == (3, 1, 2, 0)
    assert canonical_plane_order(["G", "R", "B", "G"]) == (1, 0, 3, 2)
    assert canonical_plane_order(None) == (0, 1, 2, 3)
    # Not an RGB Bayer CFA: no basis, caller shrinks per plane instead.
    assert canonical_plane_order(["R", "G", "B", "E"]) is None
    assert canonical_plane_order(["R", "G"]) is None


def test_plane_colors_gather_through_pattern_and_margins() -> None:
    raw = make_raw(np.zeros((4, 4), dtype=np.uint16))
    raw.color_desc = b"RGBG"
    # Pattern [[0,1],[3,2]] over "RGBG" -> R, G, G(second), B.
    assert _plane_colors(raw) == ["R", "G", "G", "B"]
    assert _plane_colors(raw, row_phase=1) == ["G", "B", "R", "G"]
    assert _plane_colors(SimpleNamespace()) is None


def test_denoise_passes_the_cfa_and_the_noise_curve_through() -> None:
    seen: dict[str, object] = {}

    class Spy:
        name = "spy"

        def __call__(self, planes, sigma=None, cfa=None, noise=None):
            seen["cfa"] = cfa
            seen["noise"] = noise
            return planes

    raw = make_raw(random_mosaic(np.random.default_rng(31), 16, 16))
    raw.color_desc = b"RGBG"
    curve = _FlatThenRamp()
    denoise_raw_inplace(raw, Spy(), noise=curve)
    assert seen["cfa"] == ["R", "G", "G", "B"]
    assert seen["noise"] is curve


# ── End to end on synthetic sensor noise ──


def test_denoiser_beats_the_noisy_input_against_ground_truth() -> None:
    """The property that matters: output is closer to the truth than the input."""
    rng = np.random.default_rng(37)
    yy, xx = np.mgrid[0:192, 0:192].astype(np.float32)
    # Structure at several scales, so a denoiser that just blurs cannot win.
    base = 0.25 + 0.15 * np.sin(xx / 9.0) * np.cos(yy / 13.0) + 0.1 * (xx > 96)
    clean = np.stack([base, base * 1.1, base * 1.1, base * 0.8], axis=-1).astype(np.float32)
    gain, read_var = 1.8e-4, 3e-7
    noisy = np.clip(clean + rng.normal(0.0, np.sqrt(gain * clean + read_var)).astype(np.float32), 0, 1)

    out = WaveletDenoiser()(noisy, None, ["R", "G", "G", "B"])
    err_in = float(np.sqrt(((noisy - clean) ** 2).mean()))
    err_out = float(np.sqrt(((out - clean) ** 2).mean()))
    assert err_out < err_in * 0.7
    # And it must not shift the exposure while doing it.
    assert abs(float(out.mean() - clean.mean())) < 2e-4


def test_denoiser_handles_planes_smaller_than_the_coarsest_level() -> None:
    rng = np.random.default_rng(41)
    tiny = rng.random((9, 7, 4), dtype=np.float32) * 0.5
    out = WaveletDenoiser()(tiny, None, ["R", "G", "G", "B"])
    assert out.shape == tiny.shape
    assert np.isfinite(out).all()


def test_get_denoiser_rejects_unknown_model() -> None:
    with pytest.raises(ValueError, match="unknown denoise model"):
        get_denoiser("does-not-exist")
    assert get_denoiser("passthrough") is get_denoiser("passthrough")


# ── A measured noise curve instead of a fitted one ──


class _FlatThenRamp:
    """A stand-in for the shape a Sony body writes: flat, ramp, flat again.

    Deliberately not a `gain*mean + read_var` curve — the upper plateau is the
    part `_VST` structurally cannot represent, and the reason `_CurveVST`
    exists.
    """

    def __init__(self, floor: float = 2e-4, top: float = 1e-3, knee: float = 0.125) -> None:
        self.floor, self.top, self.knee = floor, top, knee

    def noise_shape_at(self, level: np.ndarray) -> np.ndarray:
        ramp = self.floor + (self.top - self.floor) * np.clip(level / self.knee, 0.0, 1.0)
        return ramp


def test_curve_vst_flattens_a_shape_no_poisson_fit_could_hold() -> None:
    """The transform has to equalise noise across the plateau, not just the ramp.

    Above the knee the curve is *flat*, so a Poisson-Gaussian model — whose
    sigma keeps growing as sqrt(level) — would over-stabilise there and leave
    the highlights looking quieter than they are, which is how highlights end
    up over-smoothed. Sampling either side of the knee is what catches that.
    """
    curve = _FlatThenRamp()
    vst = _CurveVST(curve)
    rng = np.random.default_rng(31)
    stds = []
    for level in (0.01, 0.1, 0.4, 0.95):
        s = np.full(40000, level, dtype=np.float32)
        s = s + rng.normal(0.0, float(curve.noise_shape_at(np.array(level))), s.shape)
        stds.append(float(vst.forward(s.astype(np.float32)).std()))
    assert max(stds) / min(stds) < 1.15


def test_curve_vst_roundtrips() -> None:
    vst = _CurveVST(_FlatThenRamp())
    y = np.linspace(0.0, 1.0, 512, dtype=np.float32)
    assert np.allclose(vst.inverse(vst.forward(y)), y, atol=1e-3)


def test_a_zero_noise_curve_does_not_blow_the_transform_up() -> None:
    """A curve reporting no noise would divide by zero; the floor must hold.

    Not hypothetical: the threshold a camera writes is an integer, and it
    reaches 0 wherever the body decided to do nothing at all.
    """

    class Zero:
        def noise_shape_at(self, level: np.ndarray) -> np.ndarray:
            return np.zeros_like(level)

    vst = _CurveVST(Zero())
    y = np.linspace(0.0, 1.0, 64, dtype=np.float32)
    out = vst.forward(y)
    assert np.all(np.isfinite(out))
    assert np.allclose(vst.inverse(out), y, atol=1e-3)


def test_a_measured_curve_reaches_the_denoiser_and_changes_the_result() -> None:
    """Wiring test: the curve must actually steer the output, not ride along.

    A parameter that is accepted and ignored is the failure this asserts
    against — same frame, same seed, one with the curve and one without, and
    the two results have to differ.
    """
    rng = np.random.default_rng(5)
    clean = np.linspace(0.05, 0.95, 64 * 64).reshape(64, 64).astype(np.float32)
    planes = np.stack([clean + rng.normal(0, 0.004, clean.shape) for _ in range(4)], axis=-1)
    planes = np.clip(planes.astype(np.float32), 0.0, 1.0)

    dn = WaveletDenoiser()
    fitted = dn(planes.copy(), None, ["R", "G", "G", "B"], None)
    measured = dn(planes.copy(), None, ["R", "G", "G", "B"], _FlatThenRamp())
    assert not np.allclose(fitted, measured)
    # Both still have to be denoisers, not just different.
    for out in (fitted, measured):
        assert np.abs(out - clean[..., None]).mean() < np.abs(planes - clean[..., None]).mean()


def test_denoise_stats_say_where_the_noise_model_came_from() -> None:
    rng = np.random.default_rng(11)
    raw = make_raw(random_mosaic(rng, 32, 32))
    stats = denoise_raw_inplace(raw, PassthroughDenoiser())
    assert stats is not None and stats.noise_source == "fitted"

    raw = make_raw(random_mosaic(rng, 32, 32))
    stats = denoise_raw_inplace(raw, PassthroughDenoiser(), noise=_FlatThenRamp())
    assert stats is not None and stats.noise_source == "camera"

    raw = make_raw(random_mosaic(rng, 32, 32))
    stats = denoise_raw_inplace(raw, PassthroughDenoiser(), sigma=0.01)
    assert stats is not None and stats.noise_source == "sigma"


def test_the_measured_curve_beats_a_fit_where_the_fit_cannot_reach() -> None:
    """The justification for the wiring: it has to win, not merely differ.

    The frame carries noise with a flat-topped shape — the one `estimate_noise_model`
    structurally cannot represent, since it only fits `gain*mean + read_var`. So the
    fit over-estimates noise in the highlights and smooths detail that was never
    noisy. Measured against the truth, the camera's curve has to come out ahead
    there; elsewhere the two should be close, which is why this compares the
    bright end rather than the whole frame.
    """
    rng = np.random.default_rng(101)
    curve = _FlatThenRamp()
    # A gradient that spans the knee, plus texture so there is detail to lose.
    ramp = np.linspace(0.02, 0.98, 256, dtype=np.float32)
    clean = np.repeat(ramp[None, :], 256, axis=0)
    clean = clean + 0.02 * np.sin(np.arange(256, dtype=np.float32) / 2.0)[:, None]
    clean = np.clip(clean, 0.0, 1.0)
    sigma = np.asarray(curve.noise_shape_at(clean), dtype=np.float32)
    planes = np.stack(
        [np.clip(clean + rng.normal(0, 1, clean.shape).astype(np.float32) * sigma, 0, 1)
         for _ in range(4)], axis=-1)

    dn = WaveletDenoiser()
    fitted = dn(planes.copy(), None, ["R", "G", "G", "B"], None)
    measured = dn(planes.copy(), None, ["R", "G", "G", "B"], curve)

    truth = clean[..., None]
    bright = clean > 0.5
    err_fit = float(np.abs(fitted - truth)[bright].mean())
    err_mea = float(np.abs(measured - truth)[bright].mean())
    assert err_mea < err_fit, f"measured {err_mea:.3g} did not beat fitted {err_fit:.3g}"
