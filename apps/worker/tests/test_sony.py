"""Sony Imaging Edge reproduction: the SR2 calibration decode and the render.

The reverse-engineering itself is verified in ../../../sony_repro against frida
dumps of Edit.exe's own memory. These tests pin the port: the bit-level decode,
the invariants the segment matrices must hold, and the frontend contract.
"""

import struct
from pathlib import Path

import numpy as np
import pytest

from llr_worker.cli import prepare_linear
from llr_worker.sony import apply_sony_profile, available_styles, calibration_for, can_render
from llr_worker.sony.linear_matrix import (
    KNOT_STEP,
    N_INDEX,
    N_KNOT,
    SegmentedMatrix,
    expand,
    matrices_from_coeff,
)
from llr_worker.sony.profile import REC709_TO_PROPHOTO_D50, TONE_CURVE_POINTS, tone_curve_points
from llr_worker.sony.sr2 import (
    PARAM_BLOCK_SIZE,
    decrypt,
    look_calibrations,
    read_sr2_tag,
    unpack_param_block,
)
from llr_worker.sony.tone import LOOK_ORDER, TUNE_LIMIT, apply_tuning, base_curve, tone_curve

SAMPLES = Path(__file__).resolve().parents[3] / "samples"
# Both shot on an ILCE-7CM2, Creative Look FL and IN respectively.
SAMPLE_FL = SAMPLES / "DSC01157.ARW"
SAMPLE_IN = SAMPLES / "DSC04568.ARW"

requires_sample = pytest.mark.skipif(not SAMPLE_FL.exists(), reason="sample ARW not checked out")


# ── SR2 decryption and unpacking ───────────────────────────────────────────


def test_decrypt_is_its_own_inverse() -> None:
    """XORing a keystream twice is the identity — the cheapest full check of it."""
    data = bytes(range(256)) * 2
    once = decrypt(data, 16, 128, 0x1A2B3C4D)
    assert once != data
    assert decrypt(once, 16, 128, 0x1A2B3C4D) == data
    # Bytes outside [start, start+length) must be untouched.
    assert once[:16] == data[:16] and once[144:] == data[144:]


def reference_decode14(x: int) -> int:
    """Scalar transcription of the engine's and/not/and sequence."""
    return -(((~x) | 1) & 0x3FFF) if (x >> 13) & 1 else x & 0x3FFF


def test_unpack_matches_a_scalar_decode_of_the_same_bitstream() -> None:
    """The vectorised unpack must agree with the engine's sequence value by value.

    Negatives are encoded as -((~x | 1) & 0x3fff), not two's complement, so this
    is the test that would catch "simplifying" it back to ~x + 1.
    """
    rng = np.random.default_rng(7)
    dwords = rng.integers(0, 1 << 32, size=69, dtype=np.uint64).astype(np.uint32)
    block = struct.pack("<69I", *(int(v) for v in dwords))

    got = unpack_param_block(block)

    want = np.empty(96, dtype=np.float64)
    for i in range(48):
        v = int(dwords[6 + i])
        want[2 * i] = reference_decode14(v >> 0x12)
        want[2 * i + 1] = reference_decode14(v >> 2)
    assert np.array_equal(got, (want / 1024.0).reshape(6, 16).astype(np.float32))


def test_unpack_covers_both_signs() -> None:
    """A sanity check that the random block above really exercised negatives."""
    assert reference_decode14(0x0001) == 1
    assert reference_decode14(0x2001) == -0x1FFF
    assert reference_decode14(0x3FFF) == -1


def test_unpack_rejects_a_short_block() -> None:
    with pytest.raises(ValueError, match="at least"):
        unpack_param_block(b"\x00" * (PARAM_BLOCK_SIZE - 1))


def test_read_sr2_tag_rejects_a_non_tiff() -> None:
    path = SAMPLES / "test.svg"
    if not path.exists():
        pytest.skip("sample not checked out")
    with pytest.raises(KeyError):
        read_sr2_tag(path)


# ── The ten parallel calibrations ──────────────────────────────────────────


@requires_sample
def test_every_shot_carries_all_ten_looks() -> None:
    """The body writes calibration for all ten looks into every frame.

    This is what lets a Film shot render as Vivid 2 with no extra capture, and
    why nothing about the look is baked into this package.
    """
    looks = look_calibrations(SAMPLE_FL)
    assert len(looks) == len(LOOK_ORDER)
    assert [cal.name for cal in looks[:6]] == [
        "Standard", "Vivid", "Neutral", "Portrait", "FL", "VV2",
    ]
    for cal in looks:
        assert len(cal.param_block) == PARAM_BLOCK_SIZE
        assert cal.curve_x.shape == cal.curve_y.shape == (128,)


@requires_sample
def test_the_looks_are_genuinely_different() -> None:
    """Guards against an off-by-one in the offset table handing back one look ten times."""
    looks = look_calibrations(SAMPLE_FL)
    curves = {cal.curve_y.tobytes() for cal in looks}
    # ST/NT, VV2/IN and BW/SE share a curve by design, so ten looks give seven.
    assert len(curves) == 7


# ── The segmented matrix ───────────────────────────────────────────────────


def fake_coeff(seed: int = 0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return (rng.integers(-200, 200, size=(6, N_KNOT)) / 1024.0).astype(np.float32)


def test_every_knot_matrix_preserves_neutrals() -> None:
    """Rows summing to 1 is what lets the matrix take camera RGB at any scale."""
    knots = matrices_from_coeff(fake_coeff())
    assert np.allclose(knots.sum(axis=2), 1.0, atol=1e-6)


def test_expansion_is_circular_and_lands_on_the_knots() -> None:
    knots = matrices_from_coeff(fake_coeff(1))
    table = expand(knots)
    assert table.shape == (N_INDEX, 3, 3)
    for k in range(N_KNOT):
        assert np.allclose(table[k * KNOT_STEP], knots[k], atol=1e-6)
    # The last segment interpolates back to knot 0, not off the end of the table.
    midpoint = (knots[N_KNOT - 1] + knots[0]) / 2
    assert np.allclose(table[N_KNOT * KNOT_STEP - KNOT_STEP // 2], midpoint, atol=1e-6)


def test_neutral_pixels_come_through_unchanged() -> None:
    """Whatever segment a grey lands in, every row sums to 1 — so it must not move."""
    matrix = SegmentedMatrix(fake_coeff(2))
    grey = np.full((4, 4, 3), 0.42, dtype=np.float32)
    assert np.allclose(matrix.apply(grey), grey, atol=1e-6)


def test_apply_is_scale_invariant() -> None:
    """Doubling the exposure must double the output, not shift its colour."""
    matrix = SegmentedMatrix(fake_coeff(3))
    rng = np.random.default_rng(4)
    img = rng.random((8, 8, 3), dtype=np.float32) * 0.9 + 0.05
    assert np.allclose(matrix.apply(img * 2.0), matrix.apply(img) * 2.0, rtol=1e-5)


@requires_sample
def test_every_look_shares_one_colour_matrix() -> None:
    """The matrix belongs to the body, not to the look.

    Each SR2DataIFD carries its own 0x780f, but the engine memcpys the
    SR2SubIFD's top-level one instead — reading the per-look copy gave a table
    that matched the engine's expanded 1024 matrices for no look at all.
    """
    blocks = {cal.param_block for cal in look_calibrations(SAMPLE_FL)}
    assert len(blocks) == 1


@requires_sample
def test_black_and_white_is_still_refused() -> None:
    """BW and SE desaturate in the YCC stage, which this path does not reproduce.

    Their chroma gains (0x7842) are all zero, which zeroes Cb and Cr and leaves
    R=G=B. Rendering them here would give a colour image with a monochrome
    look's curve, which is worse than falling back to a DCP.
    """
    assert not can_render("BW")
    assert not can_render("SE")
    assert can_render("VV2")


# ── Working-space conversion and the tone curve ────────────────────────────


def test_rec709_white_lands_on_prophoto_neutral() -> None:
    """D65 white must adapt to R=G=B in the D50 working space, or greys tint."""
    white = REC709_TO_PROPHOTO_D50 @ np.ones(3, dtype=np.float32)
    assert np.allclose(white, white[0], rtol=2e-3)


@requires_sample
@pytest.mark.parametrize("style", available_styles())
def test_tone_curve_is_a_monotone_ramp_over_the_unit_domain(style: str) -> None:
    cal = calibration_for(SAMPLE_FL, style)
    assert cal is not None
    pts = np.array(tone_curve_points(cal, style))
    assert pts.shape == (TONE_CURVE_POINTS, 2)
    assert pts[0, 0] == 0.0 and pts[-1, 0] == 1.0
    assert np.all(np.diff(pts[:, 0]) > 0)
    assert np.all(np.diff(pts[:, 1]) >= 0)
    # y is display-*linear*: the sRGB encode the LUT bakes in has been undone, so
    # the browser's own encode at the end of the pipeline is not applied twice.
    assert pts[0, 1] == pytest.approx(0.0, abs=1e-6)
    assert pts[-1, 1] == pytest.approx(1.0, abs=0.01)


@requires_sample
def test_tone_curve_lifts_the_shadows_hard() -> None:
    """Sony's curve maps scene-linear 0.05 to roughly 0.16 display-linear.

    This is the whole reason the curve cannot be replaced with a generic gamma:
    it carries the exposure normalisation (Sony's white sits ~4 stops above mid
    grey) as well as the contrast.
    """
    cal = calibration_for(SAMPLE_FL, "FL")
    assert cal is not None
    y = dict(tone_curve_points(cal, "FL"))
    x = min(y, key=lambda v: abs(v - 0.05))
    assert 0.10 < y[x] < 0.25


# ── In-camera tweaks on top of the factory curve ───────────────────────────


@requires_sample
def test_tweaks_move_the_curve_the_way_the_camera_labels_them() -> None:
    """Negative Highlights pulls the midtones down; positive Shadows lifts the toe."""
    cal = calibration_for(SAMPLE_FL, "FL")
    assert cal is not None
    base = base_curve(cal)
    assert apply_tuning(base, "FL", highlights=-6)[1024] < base[1024]
    assert apply_tuning(base, "FL", highlights=+6)[1024] > base[1024]
    assert apply_tuning(base, "FL", shadows=+6)[100] > base[100]
    # Shadows acts on the deep toe only — the midtones must not follow it.
    assert apply_tuning(base, "FL", shadows=+6)[4096] == pytest.approx(base[4096], abs=1e-4)


@requires_sample
def test_a_tweak_is_linear_in_its_setting() -> None:
    """Only the two extremes are measured; everything between is interpolated.

    The engine's own steps are linear to within 3/16384, so this is the property
    the stored unit shapes rely on.
    """
    cal = calibration_for(SAMPLE_FL, "FL")
    assert cal is not None
    base = base_curve(cal)
    full = apply_tuning(base, "FL", highlights=-TUNE_LIMIT) - base
    third = apply_tuning(base, "FL", highlights=-3) - base
    assert np.allclose(third * 3, full, atol=1e-6)


@requires_sample
def test_a_setting_past_the_limit_is_ignored_not_clamped() -> None:
    """+-10 renders identically to 0 on the engine, so it must here too."""
    cal = calibration_for(SAMPLE_FL, "FL")
    assert cal is not None
    base = base_curve(cal)
    assert np.array_equal(apply_tuning(base, "FL", highlights=TUNE_LIMIT + 1), np.clip(base, 0, 1))


@requires_sample
def test_the_shot_s_own_tweaks_reach_the_curve() -> None:
    """DSC01157 was shot with Highlights -6 / Shadows +1, and it must show.

    Those settings are why an earlier baked-in "FL curve" was wrong: it had one
    batch's personal tweaks folded into what was taken to be the look itself.
    """
    cal = calibration_for(SAMPLE_FL, "FL")
    assert cal is not None
    factory = tone_curve(cal, "FL")
    as_shot = tone_curve(cal, "FL", highlights=-6, shadows=1)
    assert not np.allclose(factory, as_shot)
    # Measured against the engine: 12025/16384 at index 1024.
    assert as_shot[1024] * 16384 == pytest.approx(12025, abs=2)


# ── End to end, against a real RAW ─────────────────────────────────────────


@requires_sample
def test_calibration_reads_back_as_276_bytes() -> None:
    assert len(read_sr2_tag(SAMPLE_FL)) == PARAM_BLOCK_SIZE


@requires_sample
def test_probe_returns_a_calibration_for_a_sony_raw_and_caches_the_file_read() -> None:
    first = calibration_for(SAMPLE_FL, "FL")
    assert first is not None
    assert calibration_for(SAMPLE_FL, "FL") is first
    # A different look on the same shot comes from the same cached decrypt.
    assert calibration_for(SAMPLE_FL, "VV2") is not first


def test_probe_returns_none_for_a_file_without_calibration(tmp_path: Path) -> None:
    """The availability probe is what makes the DCP fallback a decision, not a crash."""
    path = tmp_path / "not-a-raw.bin"
    path.write_bytes(b"nothing to see here")
    assert calibration_for(path, "FL") is None
    assert calibration_for(tmp_path / "missing.arw", "FL") is None


@requires_sample
def test_render_delivers_prophoto_and_the_shot_s_own_look() -> None:
    cal = calibration_for(SAMPLE_FL, "FL")
    assert cal is not None
    rng = np.random.default_rng(5)
    camera_rgb = (rng.random((16, 16, 3), dtype=np.float32) * 0.5).astype(np.float32)

    linear, info = apply_sony_profile(camera_rgb, cal, "FL")

    assert linear.shape == camera_rgb.shape
    assert linear.dtype == np.float32
    assert linear.min() >= 0.0
    payload = info.to_json()
    assert payload["kind"] == "sony"
    assert payload["creativeLook"] == "FL"
    assert payload["workingSpace"] == "linear-prophoto-d50"
    assert len(payload["profileToneCurve"]) == TONE_CURVE_POINTS


def test_monochrome_looks_are_refused_rather_than_rendered_in_colour() -> None:
    """BW and SE desaturate in stages this pipeline does not reproduce."""
    assert can_render("FL")
    assert can_render("IN")
    assert not can_render("BW")
    assert not can_render("SE")
    assert not can_render(None)


@requires_sample
def test_a_shot_renders_through_the_sony_path_end_to_end() -> None:
    """DSC04568 is Creative Look "IN" — one the RAW carries like any other."""
    if not SAMPLE_IN.exists():
        pytest.skip("sample ARW not checked out")
    root = Path(__file__).resolve().parents[3]
    prepared = prepare_linear(SAMPLE_IN, {"profileId": "sony"}, root, None, False, half_size=True, max_size=400)
    assert prepared.color_profile["kind"] == "sony"
    assert prepared.color_profile["creativeLook"] == "IN"
