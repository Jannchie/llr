"""Bayer pack/unpack plumbing and the CFA-support guard."""

from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from llr_worker import denoise as denoise_module
from llr_worker.denoise import (
    _LUMA_CHROMA,
    _VST,
    DEFAULT_MODEL,
    FALLBACK_MODEL,
    PassthroughDenoiser,
    WaveletDenoiser,
    _CurveVST,
    _plane_black_levels,
    _plane_colors,
    canonical_plane_order,
    cfa_is_bayer_2x2,
    denoise_raw_inplace,
    effective_model,
    estimate_noise_model,
    get_denoiser,
    model_uses_chroma,
    pack_bayer,
    unpack_bayer,
)
from llr_worker.sony.rawnr import NoiseModel

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

        def __call__(self, planes, sigma=None, cfa=None, noise=None, detail=None,
                     *, chroma_scale=1.0, sensor_levels=None, strength=1.0):
            seen.update(cfa=cfa, noise=noise, detail=detail, chroma=chroma_scale,
                        levels=sensor_levels)
            return planes

    raw = make_raw(random_mosaic(np.random.default_rng(31), 16, 16))
    raw.color_desc = b"RGBG"
    curve = _FlatThenRamp()
    denoise_raw_inplace(raw, Spy(), noise=curve, detail=(0.97, 16.8), chroma_scale=1.4)
    assert seen["cfa"] == ["R", "G", "G", "B"]
    assert seen["noise"] is curve
    assert seen["detail"] == (0.97, 16.8)
    assert seen["chroma"] == 1.4
    # The sensor's own levels ride along so a denoiser can undo the
    # normalisation. SonyRawNRDenoiser cannot run without them: its thresholds
    # are indexed by the raw level with black still in it, and handing it
    # black-subtracted planes lifts the whole green plane by 512 levels.
    black, white = seen["levels"]
    assert list(black) == list(raw.black_level_per_channel)
    assert white == float(raw.white_level)


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

    FLOOR, TOP, KNEE = 2e-4, 1e-3, 0.125

    def noise_shape_at(self, level: np.ndarray) -> np.ndarray:
        return self.FLOOR + (self.TOP - self.FLOOR) * np.clip(level / self.KNEE, 0.0, 1.0)


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


def test_a_measured_curve_reaches_the_denoiser_and_denoises() -> None:
    """The curve path has to be a working denoiser, not merely a different one.

    Separate from the "beats the fit" test below because it needs the opposite
    frame: noise well above the texture, where shrinkage is unambiguously a win.
    That test's frame is deliberately quiet, so it cannot assert this.
    """
    rng = np.random.default_rng(5)
    clean = np.linspace(0.05, 0.95, 64 * 64).reshape(64, 64).astype(np.float32)
    planes = np.stack([clean + rng.normal(0, 0.004, clean.shape) for _ in range(4)], axis=-1)
    planes = np.clip(planes.astype(np.float32), 0.0, 1.0)

    measured = WaveletDenoiser()(planes.copy(), None, ["R", "G", "G", "B"], _FlatThenRamp())
    truth = clean[..., None]
    assert np.abs(measured - truth).mean() < np.abs(planes - truth).mean()


def test_restoring_detail_keeps_texture_a_plain_shrinkage_flattens() -> None:
    """The point of the parameter: measured against Edit.exe we smoothed 2-8x
    too hard, and this is the knob that closes it.

    A frame with fine texture *and* noise. Restoring the finest level has to
    leave more of the texture standing than plain shrinkage does, while still
    denoising -- if it merely undid the denoising, the error against the truth
    would go back up to the noisy input's.
    """
    rng = np.random.default_rng(77)
    yy, xx = np.mgrid[0:128, 0:128].astype(np.float32)
    clean = 0.3 + 0.06 * np.sin(xx / 2.0) * np.cos(yy / 2.5)   # fine texture
    clean = np.repeat(clean[..., None], 4, axis=-1).astype(np.float32)
    noisy = np.clip(clean + rng.normal(0, 0.012, clean.shape).astype(np.float32), 0, 1)

    dn = WaveletDenoiser()
    flat = dn(noisy.copy(), None, ["R", "G", "G", "B"], None, None)
    kept = dn(noisy.copy(), None, ["R", "G", "G", "B"], None, (0.97, 16.8))

    def texture(a: np.ndarray) -> float:
        y = a.mean(axis=-1)
        return float((y[:, 1:] - y[:, :-1]).std())

    assert texture(kept) > texture(flat) * 1.2, "restore did not keep more texture"
    # …and it is still a denoiser, not a bypass.
    err_in = float(np.abs(noisy - clean).mean())
    assert float(np.abs(kept - clean).mean()) < err_in


def test_detail_restore_is_bounded_by_its_limit() -> None:
    """The clamp is the halo limiter; a limit of zero must restore nothing."""
    rng = np.random.default_rng(78)
    noisy = np.clip(0.3 + rng.normal(0, 0.01, (96, 96, 4)), 0, 1).astype(np.float32)
    dn = WaveletDenoiser()
    plain = dn(noisy.copy(), None, ["R", "G", "G", "B"], None, None)
    clamped = dn(noisy.copy(), None, ["R", "G", "G", "B"], None, (1.0, 0.0))
    assert np.allclose(plain, clamped, atol=1e-6)


def test_the_chroma_control_moves_colour_noise_and_leaves_luma_alone() -> None:
    """Colour NR has to act on colour only, or it is just a second strength knob."""
    rng = np.random.default_rng(91)
    # Real colour structure, not a flat patch: on pure noise BayesShrink's
    # signal_var hits its floor, the threshold runs away and every setting above
    # zero zeroes the chroma levels outright, so the control looks inert.
    yy, xx = np.mgrid[0:96, 0:96].astype(np.float32)
    base = 0.3 + 0.04 * np.sin(xx / 11.0)
    tint = 0.03 * np.cos(yy / 9.0)
    planes = np.stack([base + tint, base, base, base - tint], axis=-1).astype(np.float32)
    planes = np.clip(planes + rng.normal(0, 0.01, planes.shape).astype(np.float32), 0, 1)

    dn = WaveletDenoiser()
    out = {c: dn(planes.copy(), None, ["R", "G", "G", "B"], None, None, chroma_scale=c)
           for c in (0.0, 1.0, 2.0)}

    def chroma(a: np.ndarray) -> float:
        return float((a[..., 0] - a[..., 3]).std())

    def luma(a: np.ndarray) -> float:
        return float(a.mean(axis=-1).std())

    assert chroma(out[0.0]) > chroma(out[1.0]) > chroma(out[2.0])
    # Luma must barely notice: the basis is orthonormal, so the chroma rows it
    # scales do not carry Y.
    assert luma(out[0.0]) == pytest.approx(luma(out[2.0]), rel=0.05)


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

    The frame is deliberately quiet, so it cannot also carry the "still a
    denoiser" claim — with noise this far below the texture, shrinkage costs
    more than it removes. That claim needs a noisy frame, which is what
    `test_a_measured_curve_reaches_the_denoiser_and_denoises` uses.
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


# ── Sony's own filter ──


def _sony_model(base: int = 13, slope: int = 78, hi: int = 2560) -> NoiseModel:
    """A curve shaped like the ones the bodies here write.

    These are DSC03036's actual tags (ISO 2000), so the thresholds the tests
    below run at are the ones a real frame produces rather than round numbers.
    """
    return NoiseModel(lo=0, hi=hi, base=base, slope=slope)


def _sony_case(seed: int, level: float = 1400.0, spread: float = 40.0,
               shape: tuple[int, int] = (64, 64)) -> tuple[np.ndarray, np.ndarray, dict]:
    """A noisy frame in raw levels, the same frame normalised, and the kwargs.

    The Sony denoiser needs its inputs in two domains at once -- normalised
    planes in, raw levels to compare against -- plus `cfa`, `noise` and
    `sensor_levels` on every call. Building that inline made three tests
    identical apart from the seed, so the next required kwarg would have been
    four edits with a silent pass for whichever one was missed.
    """
    black = np.array([512.0] * 4, np.float32)
    white = 16383.0
    raw = np.random.default_rng(seed).normal(
        level, spread, (*shape, 4)).astype(np.float32)
    planes = ((raw - black) / (white - black)).astype(np.float32)
    return raw, planes, {"cfa": ["R", "G", "G", "B"], "noise": _sony_model(),
                         "sensor_levels": (black, white)}


def test_the_sony_denoiser_refuses_without_the_camera_s_own_curve() -> None:
    """Its thresholds are the operator, so there is nothing to fall back on.

    Raising beats guessing here: the caller (cli.py) knows whether the file
    carries the tags and falls back to the wavelet with a line on stderr, which
    is diagnosable. A denoiser that quietly invented a threshold would render a
    different picture with no way to tell.
    """
    d = get_denoiser("sony")
    _, planes, kw = _sony_case(0, spread=0.0, shape=(32, 32))
    with pytest.raises(ValueError, match="noise model"):
        d(planes, **{**kw, "noise": None})
    with pytest.raises(ValueError, match="sensor"):
        d(planes, **{**kw, "sensor_levels": None})


def test_the_sony_denoiser_leaves_a_flat_field_alone() -> None:
    """Nothing to denoise means nothing changed, on all four phases.

    This is the test that catches the domain error: run on black-subtracted
    planes, green's `ref = c - d - 512` goes negative in the shadows, clamps at
    zero, and the filter's closing `- offset` lifts the plane by 512 levels.
    A constant field makes that a visible offset rather than a subtle one.
    """
    # A mid-shadow level, which is where the domain error showed up.
    raw_level = 900.0
    _, planes, kw = _sony_case(0, level=raw_level, spread=0.0, shape=(48, 48))
    black, white = kw["sensor_levels"]
    out = get_denoiser("sony")(planes, **kw)
    assert out.shape == planes.shape
    back = out * (white - black) + black
    assert np.allclose(back, raw_level, atol=1.0), float(np.abs(back - raw_level).max())


def test_the_sony_denoiser_works_in_raw_levels_not_normalised_ones() -> None:
    """Green must behave like red and blue; the domain error made it not.

    Measured on a real ISO 2000 frame, the four phases change by 1.27-1.59% of
    their own mean. Black-subtracted they came out at 4.6/38.9/38.8/3.4 — green
    an order of magnitude out, and lifted by 145 levels. So the property to
    pin is that green does not stand apart, which is what a wrong domain breaks
    and a right one cannot.
    """
    raw, planes, kw = _sony_case(11)
    black, white = kw["sensor_levels"]
    out = get_denoiser("sony")(planes, **kw)
    back = out * (white - black) + black
    drift = np.abs(back.mean(axis=(0, 1)) - raw.mean(axis=(0, 1)))
    # No phase may drift by even a tenth of what the domain error cost green.
    assert drift.max() < 14.0, list(drift)
    changed = np.abs(back - raw).mean(axis=(0, 1)) / raw.mean(axis=(0, 1))
    assert changed.max() < 4.0 * changed.min(), list(changed)


def test_the_sony_denoiser_inverts_the_detail_restore_pair_exactly() -> None:
    """`DetailRestore` travels dimensionless; this puts the engine's units back.

    Both halves are recoverable — `fraction` is gain/256 and the limit is in
    units of the threshold at `hi` — so the engine's own gain and limit reach
    the filter unchanged. Checked by giving the pair a gain of zero, which is
    the one setting whose effect is unmistakable: no detail restored at all,
    so the output is the sigma filter's mean and strictly smoother.
    """
    _, planes, kw = _sony_case(13)
    d = get_denoiser("sony")
    full = d(planes, detail=(1.0, 20.0), **kw)
    none = d(planes, detail=(0.0, 20.0), **kw)
    assert float(none.std()) < float(full.std())


def test_the_sony_denoiser_clamps_the_detail_gain_at_unity() -> None:
    """The engine loads `min(tag, 256)`, so a tag above it must not amplify.

    Sony's tags run 216..433 and the SIMD parameter block reads 256 for the
    ones above unity — adopting the raw tag is the mistake `rawnr.py`'s
    DETAIL_GAIN_UNIT documents. A fraction of 1.7 (a tag of 433) must land on
    the same picture as a fraction of 1.0.
    """
    _, planes, kw = _sony_case(17)
    d = get_denoiser("sony")
    assert np.array_equal(d(planes, detail=(433 / 256, 20.0), **kw),
                          d(planes, detail=(1.0, 20.0), **kw))


def test_color_nr_does_not_reach_the_sony_denoiser_and_says_so() -> None:
    """`uses_chroma` must match what the operator actually does with it.

    The declaration is what keeps Color NR out of the decode cache key, so a
    denoiser that quietly started (or stopped) consuming `chroma_scale` without
    updating the flag would either serve stale pixels or re-decode for nothing.
    Pin both halves: the flag, and the behaviour it claims.
    """
    _, planes, kw = _sony_case(19)
    d = get_denoiser("sony")
    assert d.uses_chroma is False
    assert np.array_equal(d(planes, chroma_scale=0.1, **kw),
                          d(planes, chroma_scale=4.0, **kw))
    assert model_uses_chroma("sony") is False
    # The wavelet does consume it, which is why the flag cannot be a constant.
    assert model_uses_chroma("wavelet") is True


def test_the_fallback_is_resolved_from_the_frame_not_assumed(
        monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Which denoiser runs must be answerable *before* anything is keyed on it.

    `sony` needs the camera's noise tags; a frame without them falls back to the
    wavelet, and the two disagree about Color NR. Deciding that after building a
    cache key is what would file one denoiser's pixels under the other's key
    (cli._key_tweaks). The need is the denoiser's own declaration
    (`requires_noise_model`), not "the default is the fussy one".
    """
    import llr_worker.sony.rawnr as rawnr

    path = tmp_path / "x.ARW"
    monkeypatch.setattr(rawnr, "noise_model", lambda p: _sony_model())
    assert effective_model(DEFAULT_MODEL, path) == DEFAULT_MODEL
    monkeypatch.setattr(rawnr, "noise_model", lambda p: None)
    assert effective_model(DEFAULT_MODEL, path) == FALLBACK_MODEL
    # The fallback target itself never re-falls -- that was an infinite fallback
    # when FALLBACK_MODEL still pointed at DEFAULT_MODEL.
    assert effective_model(FALLBACK_MODEL, path) == FALLBACK_MODEL
    assert get_denoiser(DEFAULT_MODEL).requires_noise_model
    assert not get_denoiser(FALLBACK_MODEL).requires_noise_model


def test_the_sony_denoiser_writes_back_by_the_iso_strength() -> None:
    """Strength 0 hands the input straight back (the engine's truncated blend
    of the input with itself), strength 1 is the full filter, and a strength
    in between lands between them on every pixel the filter moved."""
    raw, planes, kw = _sony_case(5)
    black, white = kw["sensor_levels"]
    d = get_denoiser("sony")
    span = white - black
    none = d(planes, **kw, strength=0.0) * span + black
    full = d(planes, **kw, strength=1.0) * span + black
    half = d(planes, **kw, strength=0.4) * span + black
    np.testing.assert_allclose(none, np.trunc(np.clip(raw, 0, 16383)), atol=1e-2)
    moved = np.abs(full - raw) > 2.0
    assert moved.any()
    between = (np.minimum(raw, full) - 1.0 <= half) & (half <= np.maximum(raw, full) + 1.0)
    assert between[moved].all()
    # and the result is integer-valued in raw levels: the engine truncates
    assert np.allclose(full, np.trunc(full + 1e-3), atol=1e-2)


def test_denoise_warmup_compiles_the_plane_kernels() -> None:
    """Safe to call twice; cli.py calls it on a background thread at startup."""
    denoise_module.warmup()
    denoise_module.warmup()
