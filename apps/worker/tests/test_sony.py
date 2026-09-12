"""Sony Imaging Edge reproduction: the SR2 calibration decode and the render.

The reverse-engineering itself is verified in ../../../sony_repro against frida
dumps of Edit.exe's own memory. These tests pin the port: the bit-level decode,
the invariants the segment matrices must hold, and the frontend contract.
"""

import struct
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from llr_worker.cli import (
    DRO_VISIBLE_STOPS,
    _exif_int,
    daemon_look_profile,
    detect_exiftool,
    dro_from_exif,
    prepare_linear,
    read_exiftool_metadata,
    spica_from_exif,
)
from llr_worker.denoise import _CurveVST
from llr_worker.sony import (
    LookTweaks,
    apply_look_overrides,
    apply_sony_profile,
    available_styles,
    borrowed_looks,
    calibration_for,
    can_render,
    is_borrowed,
    looks_in_file,
)
from llr_worker.sony.chroma import (
    apply_chroma,
    blend_params,
    luma_gamma,
    luma_terms,
    rgb_to_ycc,
    saturation_factor,
    unpack_params,
    ycc_to_rgb,
)
from llr_worker.sony.clarity import (
    CLARITY_AMP,
    CLARITY_AMP_SCALE,
    CLARITY_CALIBRATION_BODY,
    CLARITY_MAX,
    clarity_amount,
)
from llr_worker.sony.dro import (
    DRO_BLEND,
    DRO_COARSE,
    DRO_FINE_X,
    DRO_FINE_Y,
    DRO_GRID_BINS,
    DRO_GRID_LUMA_WHITE,
    DRO_GRID_NX,
    DRO_GRID_NY,
    DRO_LUMA_WHITE,
    DRO_W_B,
    DRO_W_R,
    _fine_means,
    _grid_from_fine,
    _image_to_grid_uv,
    apply_dro,
    dro_gain_table,
    scale_dro_gain,
)
from llr_worker.sony.dro_presets import DRO_UI_LEVELS, dro_preset_curve
from llr_worker.sony.linear_matrix import (
    KNOT_STEP,
    N_INDEX,
    N_KNOT,
    SegmentedMatrix,
    expand,
    matrices_from_coeff,
)
from llr_worker.sony.profile import (
    REC709_TO_PROPHOTO_D50,
    TONE_CURVE_POINTS,
    TWEAK_RANGES,
    chroma_terms,
    look_render_info,
    sepia_toning,
    stops_to_panel,
    tone_curve_points,
)
from llr_worker.sony.rawnr import (
    DETAIL_GAIN_TAGS,
    DETAIL_GAIN_UNIT,
    DETAIL_LIMIT_TAGS,
    ENGINE_FULL_SCALE,
    STRENGTH_TAGS,
    detail_restore,
    noise_model,
)
from llr_worker.sony.sharpness import (
    SHARPNESS_CALIB_DEFAULT,
    SHARPNESS_DEFAULT,
    SHARPNESS_MAX,
    SHARPNESS_RANGE_BASE,
    SHARPNESS_RANGE_DEFAULT,
    SHARPNESS_RANGE_MAX,
    SHARPNESS_RANGE_STEP,
    SHARPNESS_RANGE_UNITY,
    sharpness_amount,
    sharpness_calibration,
)
from llr_worker.sony.spica import (
    SPICA_GAIN_SCALE,
    spica_amount,
    spica_gain_scale,
    spica_iso_gain,
    spica_off,
    spica_range_shift,
)
from llr_worker.sony.sr2 import (
    DRO_CURVE_POINTS,
    DRO_LOG_CEILING,
    PARAM_BLOCK_SIZE,
    decrypt,
    dro_curve,
    dro_strength,
    look_calibrations,
    read_sr2_scalars,
    read_sr2_tag,
    unpack_param_block,
)
from llr_worker.sony.tone import (
    LOOK_ORDER,
    TONE_OUTPUT_FULL,
    TUNE_LIMIT,
    TUNE_STOP_LIMIT,
    apply_tuning,
    base_curve,
    tone_curve,
)

SAMPLES = Path(__file__).resolve().parents[3] / "samples"
# Both shot on an ILCE-7CM2, Creative Look FL and IN respectively.
SAMPLE_FL = SAMPLES / "DSC01157.ARW"
SAMPLE_IN = SAMPLES / "DSC04568.ARW"
# ISO 1250 against SAMPLE_IN's 100 — the only pair here far enough apart in ISO
# to show the noise model moving.
SAMPLE_HIGH_ISO = SAMPLES / "fl_test.ARW"

requires_sample = pytest.mark.skipif(not SAMPLE_FL.exists(), reason="sample ARW not checked out")
requires_exiftool = pytest.mark.skipif(detect_exiftool() is None, reason="exiftool not installed")


@requires_sample
def test_dro_is_only_disclaimed_on_shots_that_used_it() -> None:
    """It is a per-shot stage, so an unconditional caveat would be wrong.

    Sony runs ZcTaskVatr only when the shot asked for DRO. That was how the
    stage was identified: the census of a DRO frame and a non-DRO one differ in
    exactly this one entry. Whether a given shot is one of those is decided in
    dro_from_exif, which is a harder question than it looks — see
    test_auto_is_not_the_same_question_as_applied.
    """
    cal = calibration_for(SAMPLE_FL, "FL")
    assert cal is not None
    rgb = np.zeros((2, 2, 3), dtype=np.float32)
    off = apply_sony_profile(rgb, cal, "FL")[1].limitations
    on = apply_sony_profile(rgb, cal, "FL", dro=True)[1].limitations
    assert not any("DRO" in line for line in off)
    assert any("DRO" in line for line in on)
    assert len(on) == len(off) + 1


@requires_sample
@requires_exiftool
def test_the_in_camera_look_tweaks_are_actually_read() -> None:
    """The tweaks reach the curve only if exiftool is *asked* for them.

    They were plumbed all the way through — parsed, carried on RawMetadata,
    passed to the profile — but the tag was missing from the exiftool argument
    list, so every shot rendered as if untweaked. Nothing caught it because the
    absent key parsed to a clean 0. 56 of 65 frames in the reference set carry
    a tweak, and both checked-in samples do, so this can assert rather than
    merely describe.
    """
    for sample, want in ((SAMPLE_FL, {"Highlights": -6, "Shadows": 1, "Fade": 0}),
                         (SAMPLE_IN, {"Highlights": -2, "Shadows": 0, "Fade": 3})):
        exif = read_exiftool_metadata(sample)
        got = {k: _exif_int(exif.get(k)) for k in want}
        assert got == want, f"{sample.name}: {got}"


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
def test_black_and_white_desaturates_itself() -> None:
    """BW needs no special case: all eight of its chroma values are zero.

    Zero gains zero Cb and Cr, and the BT.601 return leaves R = G = B = Y. That
    is the whole of Sony's monochrome rendering, and it is why BW no longer has
    to fall back to a DCP.
    """
    looks = look_calibrations(SAMPLE_FL)
    cross, gain = chroma_terms(looks[LOOK_ORDER.index("BW")])
    assert not cross.any()
    assert not gain.any()

    rng = np.random.default_rng(0)
    out = apply_chroma(rng.random((16, 16, 3), dtype=np.float32), cross, gain, *FADE0)
    assert np.allclose(out[..., 0], out[..., 1], atol=1e-6)
    assert np.allclose(out[..., 1], out[..., 2], atol=1e-6)


# ── RGB2YCC ────────────────────────────────────────────────────────────────

# What the engine's own interpolation produced for DSC03015 (VV2), read out of
# its buffer with Frida. Also what the RAW's 0x7842 holds, bit for bit.
VV2_CHROMA = np.array([-267, -226, -235, -172, 1110, 654, 952, 1126])
# YGamma's (pivot, contrast) for the same file at Fade 0 — the setting every
# frame in the sample corpus was shot at, where the pivot is zero and the stage
# degenerates to a gain. luma_terms reads both out of the RAW.
FADE0 = (0.0, 1.0546875)


def test_the_chroma_unpacking_is_not_plain_fixed_point() -> None:
    """9-bit signed at bit 2 for the cross terms, 8-bit unsigned at bit 3 /128.

    Reading these as /1024 gives gains below 1.0, which cannot produce the
    saturation the engine visibly adds — that mismatch is what found the layout.
    """
    cross, gain = unpack_params(VV2_CHROMA)
    assert cross == pytest.approx([-0.2617, -0.2227, -0.2305, -0.1680], abs=1e-4)
    assert gain == pytest.approx([1.0781, 0.6328, 0.9297, 1.0938], abs=1e-4)
    assert gain.max() > 1.0


def test_a_neutral_pixel_stays_neutral_but_gets_brighter() -> None:
    """Grey zeroes both differences, so every chroma branch collapses.

    It does not come back untouched, though: YGamma sits inside this stage and
    lifts Y. Chroma really is left alone — the engine's own two chroma planes
    come out bit-identical across it — so the channels stay equal.
    """
    cross, gain = unpack_params(VV2_CHROMA)
    grey = np.linspace(0.0, 1.0, 32, dtype=np.float32)[:, None].repeat(3, 1)
    out = apply_chroma(grey, cross, gain, *FADE0)
    assert out[..., 0] == pytest.approx(out[..., 1], abs=1e-6)
    assert out[..., 1] == pytest.approx(out[..., 2], abs=1e-6)
    assert out[..., 0] == pytest.approx(np.minimum(grey[..., 0] * FADE0[1], 1.0), abs=1e-6)


def test_ygamma_moves_luma_and_nothing_else() -> None:
    """It is a contrast about a pivot on Y, and the chroma planes never move.

    Verified against the engine 100.0000% bit-exactly over 2M pixels, at Fade 0
    and at Fade 5; what is approximated here is only its near-identity LUT,
    dropped because it departs from identity by at most 16 in 16383 and only
    below Y ~ 1024.
    """
    assert luma_gamma(np.float32(0.5), *FADE0) == pytest.approx(0.5 * FADE0[1])
    assert luma_gamma(np.float32(0.99), *FADE0) == pytest.approx(1.0), "the top clips"

    # Because it moves Y and only Y, it must shift all three channels by the
    # *same* amount. Applying the gain before the chroma maths instead would
    # scale Cb and Cr with it and pull the channels apart.
    cross, gain = unpack_params(VV2_CHROMA)
    rng = np.random.default_rng(7)
    img = rng.random((32, 32, 3), dtype=np.float32) * 0.4 + 0.1
    y, cb, cr = rgb_to_ycc(img, cross, gain)
    lo, hi = ycc_to_rgb(y, cb, cr), ycc_to_rgb(luma_gamma(y, *FADE0), cb, cr)
    # ycc_to_rgb clips, and these gains push saturated pixels past the ends, so
    # only unclipped ones can show the shift.
    free = ((lo > 0) & (lo < 1) & (hi > 0) & (hi < 1)).all(-1)
    assert free.sum() > 100
    shift = (hi - lo)[free]
    assert shift.min() > 0, "it is a lift, not a cut"
    assert np.ptp(shift, axis=-1).max() < 1e-6, "the same shift on R, G and B"


def test_saturation_is_applied_twice_and_nearly_cancels() -> None:
    """Sony's Saturation slider divides at RGB2YCC and multiplies back later.

    The two halves are the same factor, so the setting is close to a no-op — the
    clamp between them is its whole visible effect. Measured on the engine's own
    finished frames: x0.99 at +9, and x1.05 at -9, where the intermediate chroma
    is 2.3x larger and clips. Anything that applied only one half would be out
    by 55%, which is why both belong in one place.
    """
    # The argument is Edit's 饱和度 value, which is the engine's own number: the
    # camera's +9 is 55 there (stops_to_panel), and Edit's slider reaches 100.
    assert saturation_factor(0) == 1.0
    assert saturation_factor(55) == pytest.approx(1.55)
    assert saturation_factor(-55) == pytest.approx(0.45)
    assert saturation_factor(100) == pytest.approx(2.0)
    assert saturation_factor(-100) == 0.0, "the multiply-back is by zero: no chroma"
    assert saturation_factor(999) == saturation_factor(100), "clamped to the panel"
    assert stops_to_panel(saturation=2)["saturation"] == 20, "10 per step below 2"
    assert stops_to_panel(saturation=3)["saturation"] == 25, "5 per step above it"
    assert stops_to_panel(saturation=-9)["saturation"] == -55

    cross, gain = unpack_params(VV2_CHROMA)
    rng = np.random.default_rng(11)
    img = rng.random((64, 64, 3), dtype=np.float32) * 0.5 + 0.2
    plain = apply_chroma(img, cross, gain, *FADE0)
    # rgb_to_ycc does both halves itself, so it takes the look's own gains — the
    # divided ones are only for the shader, which can only do the multiply.
    for s in (25, 55, -25):
        got = apply_chroma(img, cross, gain, *FADE0, saturation=saturation_factor(s))
        moved = np.abs(got - plain)
        # Identical wherever the intermediate chroma stayed inside the clamp,
        # and different only where it did not.
        assert np.median(moved) < 1e-6
        assert moved.max() > 1e-3
    # -100 is the one value where nothing cancels: grey out, finite everywhere.
    grey = apply_chroma(img, cross, gain, *FADE0, saturation=0.0)
    assert np.isfinite(grey).all()
    assert np.abs(grey[..., 0] - grey[..., 1]).max() < 1e-6
    assert np.abs(grey[..., 2] - grey[..., 1]).max() < 1e-6


def test_the_pair_is_deliberately_not_an_identity() -> None:
    """If it were, the stage would do nothing and the look would have no colour.

    The forward transform carries all of it; the return trip is plain BT.601.
    """
    cross, gain = unpack_params(VV2_CHROMA)
    rng = np.random.default_rng(3)
    img = rng.random((64, 64, 3), dtype=np.float32)
    assert np.abs(apply_chroma(img, cross, gain, *FADE0) - img).max() > 0.05


@requires_sample
def test_fade_is_a_contrast_pull_toward_a_pivot() -> None:
    """Fade reads two ten-entry tables out of the RAW, one pivot and one contrast.

    Fade 0 is the degenerate case — pivot zero, so a plain gain — which is why
    the stage read as a constant for as long as every sample was shot at 0. From
    Fade 1 on the pivot jumps to a fixed value and the contrast falls, and the
    two together lift the shadows while pulling the highlights down.
    """
    cal = look_calibrations(SAMPLE_FL)[LOOK_ORDER.index("FL")]
    pivot0, contrast0 = luma_terms(cal, 0)
    assert pivot0 == 0.0
    assert contrast0 > 1.0, "Fade 0 still applies a gain, it is not an identity"

    # The argument is Edit's 褪色 value: ten units per camera stop.
    pivot5, contrast5 = luma_terms(cal, 50)
    assert pivot5 > 0.5
    assert contrast5 < 1.0
    # Monotone in the setting.
    contrasts = [luma_terms(cal, f)[1] for f in range(0, 100, 10)]
    assert contrasts == sorted(contrasts, reverse=True)
    # Between two stops the contrast is the blend of the entries either side
    # and the pivot is already the upper entry's — measured on the engine at
    # 褪色 5: pivot 10624, contrast (17280 + 15616) / 2 = 16448 on DSC02961.
    pivot_half, contrast_half = luma_terms(cal, 5)
    assert pivot_half == pivot5
    assert contrast_half == pytest.approx((contrast0 + luma_terms(cal, 10)[1]) / 2)
    # Past the last entry the contrast keeps going along the last pair rather
    # than holding: 褪色 100 asked the engine for entry 10 of ten and it answered
    # one step beyond entry 9.
    c80, c90, c100 = (luma_terms(cal, f)[1] for f in (80, 90, 100))
    assert c100 == pytest.approx(c90 + (c90 - c80))
    assert luma_terms(cal, 100)[0] == pivot5

    # Below the pivot it lifts, above it it cuts. That crossover is what makes it
    # a fade rather than a brightness change.
    assert luma_gamma(np.float32(0.2), pivot5, contrast5) > luma_gamma(np.float32(0.2), pivot0, contrast0)
    assert luma_gamma(np.float32(0.95), pivot5, contrast5) < luma_gamma(np.float32(0.95), pivot0, contrast0)


def test_illuminant_deltas_ride_on_the_base() -> None:
    """p = base + (sum delta[k] * w[k]) >> 10, with 1024 meaning 1.0."""
    base = np.zeros(8, dtype=np.int64)
    deltas = np.zeros((4, 8), dtype=np.int64)
    deltas[0] = 512
    assert np.array_equal(blend_params(base, deltas, [1024, 0, 0, 0]), np.full(8, 512))
    assert np.array_equal(blend_params(base, deltas, [512, 0, 0, 0]), np.full(8, 256))
    assert np.array_equal(blend_params(base, deltas, [0, 0, 0, 0]), base)


@requires_sample
def test_the_illuminant_weights_come_out_of_the_frame_not_a_constant() -> None:
    """They are a property of the shot's light, so all ten looks share them.

    Assuming (1024, 0, 0, 0) — using the base alone — is right for most frames
    but not all: of 65 measured, 19 blend two illuminants, and on those the
    rendered hue lands up to 11 degrees off. Both checked-in samples happen to
    be blends, which is why this can be asserted rather than merely described.
    """
    for sample in (SAMPLE_FL, SAMPLE_IN):
        looks = look_calibrations(sample)
        weights = looks[0].chroma_weights
        assert weights.shape == (4,)
        assert weights.sum() == 1024, "the four weights partition 1.0"
        assert weights.max() < 1024, f"{sample.name} was meant to be a blend"
        assert all(np.array_equal(cal.chroma_weights, weights) for cal in looks)


@requires_sample
def test_recomputing_the_blend_agrees_with_the_camera() -> None:
    """The as-shot look carries the camera's own answer; ours must match it.

    Only that one look has it (tag 0x7841 sits at the top of the SR2SubIFD,
    which holds the calibration for the look the shutter fired on), so the other
    nine are blended from base and deltas. This pins the two against each other
    on the one look where both exist — the engine itself prefers the camera's,
    taking the `calib+0x1c != 0` shortcut at 0x14036dce0.
    """
    for sample in (SAMPLE_FL, SAMPLE_IN):
        shot = [cal for cal in look_calibrations(sample) if cal.chroma_final is not None]
        assert len(shot) == 1, "exactly one look was the as-shot one"
        cal = shot[0]
        ours = blend_params(cal.chroma_base, cal.chroma_deltas, cal.chroma_weights)
        # Within one unit: the camera rounds the four gains slightly differently,
        # and one unit is usually invisible after the gain's `>> 3`.
        assert np.abs(ours - cal.chroma_final).max() <= 1
        # chroma_terms must hand back the camera's value, not the recomputed one.
        for got, want in zip(chroma_terms(cal), unpack_params(cal.chroma_final), strict=True):
            assert got == pytest.approx(want)


@requires_sample
def test_sepias_colour_is_a_separate_stage_not_its_chroma() -> None:
    """SE's eight chroma values are Standard's, which is the whole clue.

    A look whose chroma matches Standard's cannot be what makes it sepia, so the
    toning has to live somewhere else — it is ZcTaskEffect, which the engine
    runs right after YCC2RGB and only for this look. sepia_toning carries the
    table measured off that stage; every other look gets None.
    """
    looks = look_calibrations(SAMPLE_FL)
    se = chroma_terms(looks[LOOK_ORDER.index("SE")])
    st = chroma_terms(looks[LOOK_ORDER.index("ST")])
    assert np.array_equal(se[0], st[0])
    assert np.array_equal(se[1], st[1])

    assert sepia_toning("ST") is None
    assert sepia_toning("BW") is None
    tone = sepia_toning("SE")
    assert tone is not None
    assert len(tone["weights"]) == 3
    assert sum(tone["weights"]) == pytest.approx(1.0, abs=1e-3)
    lut = tone["lut"]
    assert len(lut) >= 129
    # Warm: red above the input, blue below, and monotone all the way up.
    mid = lut[len(lut) // 2]
    assert mid[0] > mid[1] > mid[2]
    for k in range(3):
        column = [row[k] for row in lut]
        assert column == sorted(column)
    assert lut[0][0] < 0.02 and lut[-1][2] > 0.98


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
    pts = np.array(tone_curve_points(cal))
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
    y = dict(tone_curve_points(cal))
    x = min(y, key=lambda v: abs(v - 0.05))
    assert 0.10 < y[x] < 0.25


# ── In-camera tweaks on top of the factory curve ───────────────────────────


@requires_sample
def test_tweaks_move_the_curve_the_way_the_camera_labels_them() -> None:
    """Negative Highlights pulls the midtones down; positive Shadows lifts the toe."""
    cal = calibration_for(SAMPLE_FL, "FL")
    assert cal is not None
    base = base_curve(cal)
    assert apply_tuning(base, highlights=-6)[1024] < base[1024]
    assert apply_tuning(base, highlights=+6)[1024] > base[1024]
    assert apply_tuning(base, shadows=+6)[100] > base[100]
    # Shadows acts on the deep toe only — the midtones must not follow it.
    assert apply_tuning(base, shadows=+6)[4096] == pytest.approx(base[4096], abs=1e-4)


@requires_sample
def test_a_tweak_is_near_linear_but_not_linear() -> None:
    """A setting picks a curve out of a family whose spacing is uneven.

    An earlier model stored one shape per direction and scaled it, which made
    the response linear by construction and hid this. The engine steps along 37
    static curves instead, and their spacing is not uniform, so tripling the
    delta at -3 does not land on the delta at -9. It lands close — which is why
    the linear model survived as long as it did — but not on it.
    """
    cal = calibration_for(SAMPLE_FL, "FL")
    assert cal is not None
    base = base_curve(cal)
    full = apply_tuning(base, highlights=-TUNE_LIMIT) - base
    third = apply_tuning(base, highlights=-3) - base

    residual = np.abs(third * 3 - full).max() * TONE_OUTPUT_FULL
    assert residual > 1.0, "linear scaling would be exact; the family is not evenly spaced"
    assert residual < 0.05 * np.abs(full).max() * TONE_OUTPUT_FULL, "but it is close"


@requires_sample
def test_every_in_camera_tweak_reaches_the_shipped_curve() -> None:
    """The tweaks are only worth measuring if they survive the trip out.

    Each one has to change the curve that actually ships, and the two YGamma
    terms have to arrive as themselves. This is the layer where a wrong argument
    is invisible: the render still looks plausible, just not like the engine's.
    """
    cal = calibration_for(SAMPLE_FL, "FL")
    assert cal is not None
    rgb = np.full((2, 2, 3), 0.3, dtype=np.float32)

    def curve(**kw: int) -> list[float]:
        info = apply_sony_profile(rgb, cal, "FL", LookTweaks(**kw))[1]
        return [y for _, y in info.tone_curve]

    plain = curve()
    for field in ("highlights", "shadows", "contrast"):
        assert curve(**{field: 25}) != plain, f"{field} never reached the curve"
    for field in ("fade", "saturation", "white", "black", "hue"):
        assert curve(**{field: 25}) == plain, f"{field} does not belong on the curve"
    sat = apply_sony_profile(rgb, cal, "FL", LookTweaks(saturation=55))[1]
    assert sat.chroma_saturation == pytest.approx(1.55)
    assert sat.chroma_gain == pytest.approx([g / 1.55 for g in apply_sony_profile(
        rgb, cal, "FL")[1].chroma_gain])
    # Fade drives YGamma, not the curve — so the curve must NOT move, and the
    # two luma terms must.
    faded = apply_sony_profile(rgb, cal, "FL", LookTweaks(fade=50))[1]
    assert [y for _, y in faded.tone_curve] == plain
    assert faded.luma_pivot > 0.5
    assert faded.luma_contrast < 1.0
    assert faded.to_json()["profileLumaPivot"] == faded.luma_pivot
    # 白色/黑色 are YGamma's level pair and 色相 a rotation; each has to arrive
    # as itself, and the shot's own numbers are the identity.
    base_info = apply_sony_profile(rgb, cal, "FL")[1]
    assert (base_info.luma_black, base_info.luma_scale, base_info.chroma_hue) == (0.0, 1.0, 0.0)
    levels = apply_sony_profile(rgb, cal, "FL", LookTweaks(white=50, black=-20))[1]
    assert levels.luma_black > 0.0 and levels.luma_scale > 1.0
    j = levels.to_json()
    assert (j["profileLumaBlack"], j["profileLumaScale"]) == (levels.luma_black, levels.luma_scale)
    hued = apply_sony_profile(rgb, cal, "FL", LookTweaks(hue=-50))[1]
    assert hued.chroma_hue == pytest.approx(-17.5)
    assert hued.to_json()["profileChromaHue"] == hued.chroma_hue


def test_an_override_only_replaces_the_fields_it_names() -> None:
    """One moved slider must not silently zero the other four.

    The frontend sends the whole set today, but the contract is per-field: a
    request that names Highlights alone leaves the shot's own Fade and
    Saturation in place. Values outside Edit's panel are clamped to it.
    """
    as_shot = LookTweaks(highlights=-30, shadows=5, fade=30, saturation=20)
    assert as_shot.merged({"highlights": 20}) == LookTweaks(
        highlights=20, shadows=5, fade=30, saturation=20)
    assert as_shot.merged({"shadows": None}) == as_shot
    assert as_shot.merged(None) == as_shot
    assert as_shot.merged({"fade": -5, "contrast": 999}).fade == 0
    assert as_shot.merged({"contrast": 999}).contrast == 100
    # Clarity's floor is zero for the same reason Fade's is: below it the engine
    # renders nothing different, so there is nothing there to set.
    assert as_shot.merged({"clarity": -4}).clarity == 0
    assert as_shot.merged({"white": -150, "black": 150, "hue": 7}) == LookTweaks(
        highlights=-30, shadows=5, fade=30, saturation=20, white=-100, black=100, hue=7)
    assert LookTweaks.from_json({"highlights": "-30"}) == LookTweaks(highlights=-30)
    assert LookTweaks.from_json("nonsense") == LookTweaks()


def test_the_camera_s_stops_land_where_edit_shows_them() -> None:
    """MakerNotes -> Edit's panel, exactly as Edit fills its sliders in.

    Read off Edit's own panel with DSC02961 open (Highlights -6, Shadows +1,
    Clarity +1 in the file): 高光 -30, 阴影 5, 清晰 10 — five units per stop on
    the three tone sliders, ten on 清晰 and 褪色, and 饱和度's stops are the
    engine's own ladder (SATURATION_STEPS). The three sliders the camera does
    not have are not in the answer at all, so merged() leaves them at zero.
    """
    assert stops_to_panel(highlights=-6, shadows=1, clarity=1) == {
        "highlights": -30, "shadows": 5, "contrast": 0, "fade": 0,
        "saturation": 0, "clarity": 10}
    assert stops_to_panel(contrast=9, fade=9, saturation=9, clarity=9) == {
        "highlights": 0, "shadows": 0, "contrast": 45, "fade": 90,
        "saturation": 55, "clarity": 90}
    got = LookTweaks().merged(stops_to_panel(highlights=-6))
    assert (got.highlights, got.white, got.black, got.hue) == (-30, 0, 0, 0)
    # The panel reaches further than the camera on every slider, so a stop is
    # never at the end of its slider's range.
    for lo, hi in TWEAK_RANGES.values():
        assert lo <= 0 < hi and hi == 100


@requires_sample
def test_a_look_override_rebuilds_the_profile_without_touching_the_pixels() -> None:
    """The whole reason the tweaks stay out of the linear cache key.

    Not one of the five reaches the matrix, so a cached decode has to be able to
    serve any setting: apply_look_overrides rebuilds the profile that rides with
    those pixels. It must rebase on what the body recorded rather than on
    whatever the last request asked for, or one client's slider would leak into
    the next one's render.
    """
    cal = calibration_for(SAMPLE_FL, "FL")
    assert cal is not None
    rgb = np.full((2, 2, 3), 0.3, dtype=np.float32)
    as_shot = LookTweaks(highlights=-6, shadows=1)
    # The gain table the way a decode supplies it, so the rebuild has the same
    # material to work from — it re-reads the curve rather than carrying it.
    linear, info = apply_sony_profile(rgb, cal, "FL", as_shot, dro=True,
                                      dro_gain=dro_gain_table(SAMPLE_FL))
    base = info.to_json()

    lifted = apply_look_overrides(base, SAMPLE_FL, {"highlights": 6})
    assert lifted["lookTweaks"] == {**as_shot.to_json(), "highlights": 6}
    assert lifted["lookAsShot"] == as_shot.to_json()
    # Every tweak's range rides along, since the panel builds its sliders from
    # these: one missing would silently drop a control, and one invented on the
    # frontend could offer a stop this side clamps straight back.
    assert lifted["lookRanges"] == {f: list(r) for f, r in TWEAK_RANGES.items()}
    assert lifted["profileToneCurve"] != base["profileToneCurve"]
    # DRO belongs to the shot, not to any tweak, so a tweak leaves it alone.
    assert lifted["limitations"] == base["limitations"]
    assert lifted["droStrength"] == base["droStrength"] == 1.0
    assert lifted["profileDroGain"] == base["profileDroGain"]

    # Rebased on lookAsShot, so overriding an override still lands on the same
    # answer as overriding the original — and dropping it returns to as shot.
    assert apply_look_overrides(lifted, SAMPLE_FL, {"highlights": 6}) == lifted
    assert apply_look_overrides(lifted, SAMPLE_FL, as_shot.to_json()) == base
    # A profile this path did not produce is none of its business.
    dcp = {"kind": "dcp", "profileToneCurve": [[0.0, 0.0]]}
    assert apply_look_overrides(dcp, SAMPLE_FL, {"highlights": 6}) is dcp
    assert linear.shape == rgb.shape


@requires_sample
@requires_exiftool
def test_the_look_profile_command_answers_without_decoding_anything() -> None:
    """What a moved slider costs: a curve and eight chroma terms, no pixels.

    The frontend already holds the decoded frame, so re-running /render-linear
    for a slider drag would send tens of megabytes back to redraw pixels that
    never changed. This command is the whole reason the split in
    look_render_info exists, so it has to agree with the render path exactly.
    """
    request = {"input": str(SAMPLE_FL), "look": {"highlights": 20}}
    profile = daemon_look_profile(request, SAMPLES)["colorProfile"]
    assert profile is not None
    assert profile["creativeLook"] == "FL"
    # DSC01157 was shot at Highlights -6 / Shadows +1, and the panel is built
    # from this: as-shot is what it starts at and resets to.
    assert profile["lookAsShot"]["highlights"] == -30, "the camera's -6, on Edit's scale"
    assert profile["lookTweaks"] == {**profile["lookAsShot"], "highlights": 20}

    cal = calibration_for(SAMPLE_FL, "FL")
    assert cal is not None
    direct = look_render_info(cal, "FL", LookTweaks(highlights=20, shadows=5)).to_json()
    assert profile["profileToneCurve"] == direct["profileToneCurve"]
    assert profile["profileChromaGain"] == direct["profileChromaGain"]


@requires_sample
def test_contrast_bends_only_where_the_family_changes_pitch() -> None:
    """Whether Contrast is linear in its setting depends on where it starts.

    The family is spaced about four times tighter above neutral (18) than below,
    so a run of Contrast that stays on one side is near-linear and one that
    crosses is not. Which one happens is decided by the shot's own Highlights and
    Shadows, because all three settings are summed into the same gain.

    That is the whole of a long-standing puzzle. Positive Contrast measured as
    nonlinear here and as a different set of steps on another body, and it was
    one effect both times: the two files were shot with different Highlights, so
    the same nine steps started from different places in the family.
    """
    cal = calibration_for(SAMPLE_FL, "FL")
    assert cal is not None
    base = base_curve(cal)
    assert apply_tuning(base, contrast=+6)[1024] > base[1024]
    assert apply_tuning(base, contrast=-6)[1024] < base[1024]

    def delta(contrast: int, highlights: int) -> np.ndarray:
        anchor = apply_tuning(base, highlights=highlights)
        return apply_tuning(base, highlights=highlights, contrast=contrast) - anchor

    def bend(highlights: int) -> float:
        return float(np.abs(3 * delta(3, highlights) - delta(9, highlights)).max())

    # From neutral the run stays above 18; from -6 it crosses.
    assert bend(0) * TONE_OUTPUT_FULL < 5.0
    assert bend(-6) > 10 * bend(0)

    # Monotone across the whole range, and interpolating between whole settings.
    mids = [apply_tuning(base, contrast=c)[1024] for c in range(10)]
    assert mids == sorted(mids)
    half = apply_tuning(base, contrast=4)[1024]
    assert mids[3] <= half <= mids[5]


@requires_sample
def test_a_borrowed_look_s_tweaks_are_not_silently_dead() -> None:
    """FL2 and FL3 are missing from older RAWs, and their sliders did nothing.

    The tweak shapes used to be stored one set per look, so a look with no entry
    kept its baseline without complaining — Contrast, Highlights and Shadows all
    moved in the UI and nowhere else. The engine builds one table over the
    curve's output from the three settings alone, so it cannot depend on a look
    even in principle.

    So a borrowed look must respond, and by exactly as much as a look the body
    does ship. Checked against Edit.exe itself rendering FL2 and FL3 on a donor
    body: Highlights and Shadows land within 1.6/16384 of it.
    """
    reference = calibration_for(SAMPLE_FL, "FL")
    assert reference is not None
    ref_base = base_curve(reference)
    borrowed = borrowed_looks()
    assert borrowed, "nothing borrowed means this test proves nothing"

    for look in borrowed:
        cal = calibration_for(SAMPLE_FL, look)
        assert cal is not None, look
        base = base_curve(cal)
        for field in ("contrast", "highlights", "shadows"):
            for value in (-TUNE_LIMIT, TUNE_LIMIT):
                moved = np.abs(apply_tuning(base, **{field: value}) - base).max()
                assert moved > 1e-3, f"{look} {field}{value:+d} did nothing"
                ref = np.abs(apply_tuning(ref_base, **{field: value}) - ref_base).max()
                assert moved == pytest.approx(ref, abs=1e-4), f"{look} {field}{value:+d}"


@requires_sample
def test_a_setting_past_the_camera_s_range_keeps_going() -> None:
    """Edit's panel reaches +-20 stops, and the engine follows it all the way.

    The camera stops at +-9 but Edit's slider does not, and the engine does not
    clamp the gain at the family's ends either: it continues the last two rows
    as a line. Measured on the running engine at 高光/阴影/对比度 +-100 (gains
    -2 and 38) and at 对比度 +100 with 高光 +100 (gain 58): zero difference
    against the extrapolation, 179..311/16384 against a clamp. The settings are
    summed before any of that, so capping each of them first would let a large
    pair cancel back into no tweak at all.
    """
    cal = calibration_for(SAMPLE_FL, "FL")
    assert cal is not None
    base = base_curve(cal)

    at_limit = apply_tuning(base, highlights=-TUNE_LIMIT)
    beyond = apply_tuning(base, highlights=-(TUNE_LIMIT + 1))
    assert np.abs(beyond - base).max() > np.abs(at_limit - base).max(), "no longer ignored"

    # Past the family's end the curve keeps moving, in the same direction, and
    # stays inside [0, 1]: a fifth of a stop is a real input now.
    end = apply_tuning(base, highlights=-18)
    past = apply_tuning(base, highlights=-TUNE_STOP_LIMIT)
    further = apply_tuning(base, highlights=-20.2)
    assert not np.array_equal(past, end)
    assert not np.array_equal(further, past)
    assert (past[1024] < end[1024]) and (further[1024] < past[1024])
    for curve in (past, further, apply_tuning(base, highlights=TUNE_STOP_LIMIT, contrast=TUNE_STOP_LIMIT)):
        assert curve.min() >= 0.0 and curve.max() <= 1.0

    # A per-field cap would make this pair cancel; the sum has to survive.
    assert not np.array_equal(apply_tuning(base, contrast=100, shadows=50), base)


@requires_sample
def test_the_shot_s_own_tweaks_reach_the_curve() -> None:
    """DSC01157 was shot with Highlights -6 / Shadows +1, and it must show.

    Those settings are why an earlier baked-in "FL curve" was wrong: it had one
    batch's personal tweaks folded into what was taken to be the look itself.
    """
    cal = calibration_for(SAMPLE_FL, "FL")
    assert cal is not None
    factory = tone_curve(cal)
    as_shot = tone_curve(cal, highlights=-6, shadows=1)
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


def test_all_ten_looks_render_now_that_sepia_tones() -> None:
    """Sepia was the last hold-out, and it was a missing stage rather than data."""
    for style in ("FL", "IN", "BW", "SE"):
        assert can_render(style)
    assert not can_render(None)
    assert not can_render("nonsense")


@requires_sample
def test_a_shot_renders_through_the_sony_path_end_to_end() -> None:
    """DSC04568 is Creative Look "IN" — one the RAW carries like any other."""
    if not SAMPLE_IN.exists():
        pytest.skip("sample ARW not checked out")
    root = Path(__file__).resolve().parents[3]
    prepared = prepare_linear(SAMPLE_IN, {"profileId": "sony"}, root, None, False, half_size=True, max_size=400)
    assert prepared.color_profile["kind"] == "sony"
    assert prepared.color_profile["creativeLook"] == "IN"


# ── Switching the Creative Look ────────────────────────────────────────────
#
# The whole feature rests on one measured fact: the looks share the body's one
# hue-segmented matrix, so switching changes no pixel. That is what makes it a
# ~75 kB profile refetch instead of a re-decode, so it is pinned first.


@requires_sample
def test_switching_look_changes_no_pixel() -> None:
    """Every look puts the same param_block through the matrix, so linear is equal.

    If this ever fails, the /look-profile route is no longer a valid way to
    switch looks — the frontend would be showing one look's curve over another
    look's pixels — and the switch has to go back through a full decode.
    """
    rng = np.random.default_rng(11)
    camera_rgb = rng.random((24, 24, 3), dtype=np.float32).astype(np.float32)

    base = None
    for style in looks_in_file(SAMPLE_FL):
        cal = calibration_for(SAMPLE_FL, style)
        assert cal is not None, style
        linear, _ = apply_sony_profile(camera_rgb, cal, style)
        if base is None:
            base = linear
        else:
            assert np.array_equal(linear, base), f"{style} moved the pixels"


@requires_sample
def test_the_file_lists_its_own_looks() -> None:
    """Read from the RAW, not from LOOK_ORDER — that is what carries FL2/FL3."""
    present = looks_in_file(SAMPLE_FL)
    # This body's own ten, in Sony's order, before any borrowed ones.
    assert present[:10] == ["ST", "VV", "NT", "PT", "FL", "VV2", "IN", "SH", "BW", "SE"]
    # can_render given a file answers from that list rather than the constant.
    assert can_render("VV2", SAMPLE_FL)
    assert not can_render("XX", SAMPLE_FL)
    assert looks_in_file(Path("/nonexistent.arw")) == []


@requires_sample
def test_look_profile_renders_the_requested_look() -> None:
    root = Path(__file__).resolve().parents[3]
    rel = str(SAMPLE_FL.relative_to(root))

    as_shot = daemon_look_profile({"input": rel}, root)["colorProfile"]
    assert as_shot["creativeLook"] == "FL"
    assert as_shot["lookAsShotStyle"] == "FL"
    assert as_shot["availableLooks"][:2] == ["ST", "VV"]

    switched = daemon_look_profile({"input": rel, "style": "IN"}, root)["colorProfile"]
    assert switched["creativeLook"] == "IN"
    # The reset target still names what the body chose, not the pick.
    assert switched["lookAsShotStyle"] == "FL"
    assert switched["profileToneCurve"] != as_shot["profileToneCurve"]

    # Black & White zeroes the chroma gains outright — its eight parameters are
    # zero, so this is the whole of its desaturation.
    bw = daemon_look_profile({"input": rel, "style": "BW"}, root)["colorProfile"]
    assert bw["profileChromaGain"] == [0.0, 0.0, 0.0, 0.0]
    # Sepia is the one look that also needs a toning stage.
    se = daemon_look_profile({"input": rel, "style": "SE"}, root)["colorProfile"]
    assert se["profileSepia"] is not None
    assert switched["profileSepia"] is None


@requires_sample
def test_an_unknown_look_falls_back_to_the_shot_s_own() -> None:
    """A session restored onto a body without that look must not stick.

    Returning null here would read as "no Sony rendering" to the client, which
    answers by keeping whatever profile is already on screen — the wrong look.
    """
    root = Path(__file__).resolve().parents[3]
    rel = str(SAMPLE_FL.relative_to(root))
    # A code neither the file nor the donor table has — FL2/FL3 do resolve now.
    got = daemon_look_profile({"input": rel, "style": "FL9"}, root)["colorProfile"]
    assert got is not None
    assert got["creativeLook"] == "FL"


@requires_sample
def test_overrides_can_switch_the_look_on_a_cached_decode() -> None:
    """The cached-linear path re-profiles for a look, the same way it does tweaks."""
    cal = calibration_for(SAMPLE_FL, "FL")
    assert cal is not None
    rng = np.random.default_rng(7)
    _, info = apply_sony_profile(rng.random((8, 8, 3), dtype=np.float32), cal, "FL")
    cached = info.to_json()

    switched = apply_look_overrides(cached, SAMPLE_FL, None, "SH")
    assert switched["creativeLook"] == "SH"
    assert switched["profileToneCurve"] != cached["profileToneCurve"]
    # The shot's own tweaks still ride along; only the calibration changed.
    assert switched["lookAsShot"] == cached["lookAsShot"]

    # No style and no overrides is a no-op, not a rebuild.
    assert apply_look_overrides(cached, SAMPLE_FL, None) is cached


# ── Looks the body predates (FL2 / FL3) ────────────────────────────────────
#
# Sony added these after this body shipped, so its RAWs carry no calibration for
# them. They are supplied from a donor table instead — defensible only because
# the tone curve was measured to be byte-identical across bodies while the
# chroma is not, which is why the render says so in its limitations.


@requires_sample
def test_borrowed_looks_are_offered_and_flagged() -> None:
    present = looks_in_file(SAMPLE_FL)
    assert borrowed_looks() == ["FL2", "FL3"]
    # Appended after the file's own ten, not mixed in.
    assert present[-2:] == ["FL2", "FL3"]
    assert is_borrowed(SAMPLE_FL, "FL2")
    assert not is_borrowed(SAMPLE_FL, "FL")


@requires_sample
def test_a_borrowed_look_still_uses_this_shot_s_own_matrix() -> None:
    """The invariant that makes borrowing safe at all.

    Only the curve and the chroma come from the donor. The hue-segmented matrix
    is per-shot calibration from this file's own top level, so a borrowed look
    must leave the linear pixels exactly where every other look leaves them.
    """
    rng = np.random.default_rng(3)
    camera_rgb = rng.random((16, 16, 3), dtype=np.float32).astype(np.float32)
    own = calibration_for(SAMPLE_FL, "FL")
    borrowed = calibration_for(SAMPLE_FL, "FL2")
    assert own is not None and borrowed is not None
    assert borrowed.param_block == own.param_block
    # Shot-level fields stay the shot's; only the look's own tables differ.
    assert np.array_equal(borrowed.chroma_weights, own.chroma_weights)
    assert borrowed.chroma_final is None
    assert not np.array_equal(borrowed.curve_y, own.curve_y)

    a, _ = apply_sony_profile(camera_rgb, own, "FL")
    b, _ = apply_sony_profile(camera_rgb, borrowed, "FL2")
    assert np.array_equal(a, b)


@requires_sample
def test_a_borrowed_look_declares_itself() -> None:
    root = Path(__file__).resolve().parents[3]
    rel = str(SAMPLE_FL.relative_to(root))

    own = daemon_look_profile({"input": rel, "style": "FL"}, root)["colorProfile"]
    assert own["lookBorrowed"] is False
    assert not any("not in the RAW" in m for m in own["limitations"])

    fl2 = daemon_look_profile({"input": rel, "style": "FL2"}, root)["colorProfile"]
    assert fl2["lookBorrowed"] is True
    assert any("not in the RAW" in m for m in fl2["limitations"])
    # A borrowed look is a real look, not a copy of the nearest one.
    assert fl2["profileToneCurve"] != own["profileToneCurve"]

    # Switching off a borrowed look drops the note again; switching onto one
    # through the cached-decode path adds it.
    back = apply_look_overrides(fl2, SAMPLE_FL, None, "FL")
    assert back["lookBorrowed"] is False
    assert not any("not in the RAW" in m for m in back["limitations"])
    onto = apply_look_overrides(own, SAMPLE_FL, None, "FL3")
    assert onto["lookBorrowed"] is True
    assert any("not in the RAW" in m for m in onto["limitations"])


# --- DRO: what the body armed vs what it actually did -----------------------


@requires_sample
def test_the_dro_curve_comes_out_of_the_raw() -> None:
    """The camera writes its Auto decision as a curve, so nothing is guessed.

    Rebuilt from tags 0x781b/0x781c this matched the engine's own 104-float
    buffer on all 16 frames checked, to 3e-6. Here we can only check the shape,
    but the shape is what carries the claim: log2 in, log2 out, monotone, and
    pinned to identity at both ends because DRO reshapes the middle.
    """
    curve = dro_curve(SAMPLE_IN)
    assert curve is not None
    assert curve.shape == (DRO_CURVE_POINTS,)
    x = np.arange(DRO_CURVE_POINTS) * (DRO_LOG_CEILING / DRO_CURVE_POINTS)
    assert np.all(np.diff(curve) > 0)
    assert abs(curve[0] - x[0]) < 0.05
    assert abs(curve[-1] - x[-1]) < 0.05
    # It lifts the shadows: the departure is positive and in the lower middle.
    assert curve[np.argmax(curve - x)] > x[np.argmax(curve - x)]
    assert dro_strength(SAMPLE_IN) == pytest.approx(float(np.abs(curve - x).max()))


@requires_sample
@requires_exiftool
def test_auto_is_not_the_same_question_as_applied() -> None:
    """Both samples say Auto; only one of them got any DRO.

    This is the whole reason dro_from_exif reads the file rather than trusting
    the tag. Three of 22 Auto frames measured graded out flat, and a caveat
    about an effect that is not in the picture is just noise.
    """
    for sample in (SAMPLE_FL, SAMPLE_IN):
        assert read_exiftool_metadata(sample).get("DynamicRangeOptimizer") == "Auto"

    assert dro_strength(SAMPLE_FL) < DRO_VISIBLE_STOPS
    assert dro_strength(SAMPLE_IN) > DRO_VISIBLE_STOPS

    assert not dro_from_exif(read_exiftool_metadata(SAMPLE_FL), SAMPLE_FL)
    assert dro_from_exif(read_exiftool_metadata(SAMPLE_IN), SAMPLE_IN)
    # Without the file there is nothing better than the tag, so it stays trusted.
    assert dro_from_exif(read_exiftool_metadata(SAMPLE_FL))
    # And "Off" is still off no matter what the curve says.
    assert not dro_from_exif({"DynamicRangeOptimizer": "Off"}, SAMPLE_IN)


def test_a_file_with_no_curve_reads_as_no_curve(tmp_path: Path) -> None:
    """Absent is None, not zero — the engine falls back to a preset there."""
    blank = tmp_path / "not-a-raw.arw"
    blank.write_bytes(b"II*\x00" + b"\x00" * 64)
    assert dro_curve(blank) is None
    assert dro_strength(blank) is None
    assert dro_gain_table(blank) is None
    # None must not read as "no DRO": the stage still runs, off a preset table.
    assert dro_from_exif({"DynamicRangeOptimizer": "Auto"}, blank)


@requires_sample
def test_dro_strength_scales_in_the_log_domain() -> None:
    """Doubling the strength squares the gain, and zero is exactly identity.

    The curve family is 98.8% one principal component across the frames
    measured, so a shot's DRO really is one scalar times a fixed shape and
    scaling the departure stays on that family instead of leaving it.
    """
    table = dro_gain_table(SAMPLE_IN)
    assert table is not None
    assert max(table) > 1.05          # this frame has real DRO to scale
    off = scale_dro_gain(table, 0.0)
    assert off == [1.0] * len(table)
    assert scale_dro_gain(table, 1.0) == table
    doubled = scale_dro_gain(table, 2.0)
    for a, b in zip(table, doubled, strict=True):
        assert b == pytest.approx(a * a, rel=1e-12)
    # Clamped, so a client cannot ask for an arbitrary power.
    assert scale_dro_gain(table, 99.0) == scale_dro_gain(table, 2.0)
    assert scale_dro_gain(table, -1.0) == off


@requires_sample
def test_dro_lifts_shadows_and_leaves_white_alone() -> None:
    """What the control does to pixels, at the two ends that matter.

    White has to stay put: the table stops just below normalised white, and
    extrapolating the engine's own out-of-table rule there would dim every
    clipped highlight — see sony/dro.py.
    """
    dark = np.full((2, 2, 3), 8.0 / DRO_LUMA_WHITE, dtype=np.float64)
    white = np.ones((2, 2, 3), dtype=np.float64)
    assert apply_dro(dark, SAMPLE_IN, 1.0).mean() > dark.mean() * 1.05
    assert apply_dro(white, SAMPLE_IN, 1.0).mean() == pytest.approx(1.0, abs=0.01)
    # Neutral in, neutral out: one gain for all three channels is why DRO never
    # shifts colour, and it is the reason the shader can apply it after the
    # matrix instead of before.
    lifted = apply_dro(dark, SAMPLE_IN, 1.0)
    assert lifted[..., 0] == pytest.approx(lifted[..., 2])
    assert np.array_equal(apply_dro(dark, SAMPLE_IN, 0.0), dark)


def test_dro_puts_white_one_stop_below_the_curves_ceiling_on_both_paths() -> None:
    """Sensor white enters the curve at log2(4096) = 12.0, on the grid path too.

    The stage reads the engine's demosaic output (white 8192) halved, so its
    Ylog for normalised white is 12.0, not the top of the axis. Measured on
    three ILCE-7CM2 exports: the stage's input plane is 4064..4153x our linear
    luma in every band, and its per-pixel gain is reproduced to 0.009..0.020
    RMS (log2) with this constant against 0.20..0.27 with the 16383 the grid
    path used to assume — which lifted DSC06263's midtones x1.40 too little.
    Two names only because the wire carries both; they must not drift apart,
    or the grid path indexes the curve on a different axis from the mean it
    slices out of the grid.
    """
    assert np.log2(DRO_LUMA_WHITE) == 12.0
    assert DRO_GRID_LUMA_WHITE == DRO_LUMA_WHITE


def test_the_dro_grid_builder_weights_green_like_the_engine_not_like_luma() -> None:
    """The grid's luma is not BT.601, and that is deliberate.

    ZcTaskVatr's grid builder multiplies its averaged green by vatr+0x50 rather
    than vatr+0x4c, so red outweighs green and the weights sum to 0.2635 instead
    of 0.5. It reads like a slip in Sony's code and it is very tempting to
    "correct" — the decompiler even shows the offset twice, which looks like an
    artefact. It is not: using the BT.601 green shifts the whole grid by about
    1.35 stops. This pins the engine's arithmetic so that correction fails loudly.
    """
    black = 512
    rows, cols = DRO_FINE_Y * 15, DRO_FINE_X * 16
    bayer = np.zeros((rows * 4 + 40, cols * 4 + 2), dtype=np.uint16)
    # RGGB with R at the quad's top-left, which is the slot the engine reads.
    bayer[0::2, 0::2] = black + 1000   # R
    bayer[0::2, 1::2] = black + 4000   # G
    bayer[1::2, 0::2] = black + 4000   # G
    bayer[1::2, 1::2] = black + 2000   # B

    fine = _fine_means(bayer, black)
    assert fine is not None and fine.shape == (DRO_FINE_Y, DRO_FINE_X)
    expected = np.log2(1000 * DRO_W_R + 4000 * DRO_W_B + 2000 * DRO_W_B)
    assert fine == pytest.approx(expected, abs=2e-4)
    # BT.601 over the same quad would land a long way off, which is the whole point.
    assert abs(np.log2(1000 * 0.299 + 4000 * 0.587 + 2000 * 0.114) - expected) > 1.3


def test_the_dro_grid_reduces_to_the_local_mean_where_it_should() -> None:
    """A flat field pins the grid's arithmetic end to end.

    With every fine cell at the same log luminance the whole chain collapses to
    `M[j] = A[j]*j + (1 - A[j])*b`, where b is the bin the field falls in: the
    kernel cancels between num and den, and the four-neighbour smoothing is a
    no-op because every block is identical. So the midtone bins, where A is
    zero, must come back as exactly b — the local mean and nothing else — while
    the two ends, where A is one, must ignore the field entirely and return the
    bin index. That is what makes DRO local in the middle and inert at the edges.
    """
    level = 6.5
    fine = np.full((DRO_FINE_Y, DRO_FINE_X), level)
    num, den = _grid_from_fine(fine)
    assert num.shape == (DRO_GRID_NY, DRO_GRID_NX, DRO_GRID_BINS)
    assert np.all(den > 0)

    bin_of = int(level / DRO_LOG_CEILING * DRO_GRID_BINS)
    j = np.arange(DRO_GRID_BINS)
    expected = DRO_BLEND * j + (1.0 - DRO_BLEND) * bin_of
    mean = num / den
    for row in range(DRO_GRID_NY):
        for col in range(DRO_GRID_NX):
            assert mean[row, col] == pytest.approx(expected, abs=1e-9)
    # Spelled out for the two states that matter, so the intent survives a
    # refactor of the line above.
    assert mean[0, 0, 5] == pytest.approx(bin_of)      # A = 0: purely local
    assert mean[0, 0, 0] == pytest.approx(0.0)         # A = 1: purely the bin
    assert mean[0, 0, DRO_GRID_BINS - 1] == pytest.approx(DRO_GRID_BINS - 1.0)


def test_the_dro_grid_smoothing_borrows_from_neighbours() -> None:
    """One bright block must lift its neighbours and nothing further.

    The engine smooths each block's histogram with its four edge neighbours —
    not a 3x3 — at half itself and half the neighbours. So a lone bright block
    has to move the four blocks orthogonally adjacent to it and leave the
    diagonal ones alone; catching a diagonal here is what would tell you the
    smoothing had been widened by mistake.
    """
    fine = np.full((DRO_FINE_Y, DRO_FINE_X), 3.0)
    row, col = 2, 3
    fine[row * DRO_COARSE:(row + 1) * DRO_COARSE,
         col * DRO_COARSE:(col + 1) * DRO_COARSE] = 10.0
    mean = np.divide(*_grid_from_fine(fine))
    flat = mean[0, 0]

    def moved(r: int, c: int) -> bool:
        return not np.allclose(mean[r, c], flat, atol=1e-9)

    assert moved(row, col)
    assert all(moved(r, c) for r, c in
               ((row - 1, col), (row + 1, col), (row, col - 1), (row, col + 1)))
    assert not any(moved(r, c) for r, c in
                   ((row - 1, col - 1), (row - 1, col + 1),
                    (row + 1, col - 1), (row + 1, col + 1)))


def test_the_dro_grid_map_follows_the_camera_flip() -> None:
    """The grid is addressed in sensor space; the render is flipped and cropped.

    The affine map is the only thing bridging the two, and getting a flip's sign
    wrong there does not fail loudly — it lands DRO's local means on the wrong
    part of the picture, which reads as a vague haze rather than as a bug. So
    the corners are checked directly: for an upright frame the image's top-left
    is the sensor's, and for the two 90-degree flips the u and v axes swap, so
    u must stop depending on image x and start depending on image y.
    """
    class _Sizes:
        def __init__(self, flip: int) -> None:
            self.width, self.height = 7028, 4688
            self.left_margin, self.top_margin, self.flip = 0, 0, flip

    class _Raw:
        def __init__(self, flip: int) -> None:
            self.sizes = _Sizes(flip)
            self.raw_image = np.zeros((5120, 7168), dtype=np.uint16)

    span_x, span_y = DRO_COARSE * 64, DRO_COARSE * 60

    upright = _image_to_grid_uv(_Raw(0), None)
    assert upright is not None
    a0, a1, a2, b0, b1, b2 = upright
    # Sensor origin, offset by half a cell because the map is to cell centres.
    assert a0 == pytest.approx(-0.5)
    assert b0 == pytest.approx((-40 - span_y / 2) / span_y)
    assert a1 == pytest.approx(7028 / span_x) and b2 == pytest.approx(4688 / span_y)
    assert a2 == 0.0 and b1 == 0.0   # no transpose, so no cross terms

    for flip in (5, 6):
        _, ra1, ra2, _, rb1, rb2 = _image_to_grid_uv(_Raw(flip), None) or ()
        assert ra1 == 0.0 and rb2 == 0.0, "a 90-degree flip must transpose the axes"
        assert ra2 != 0.0 and rb1 != 0.0
        # Same patch of sensor either way: the spans are unchanged, it is only
        # which image axis carries them that swaps. The two flips differ in sign.
        assert abs(ra2) == pytest.approx(a1) and abs(rb1) == pytest.approx(b2)
    assert _image_to_grid_uv(_Raw(5), None)[2] == -_image_to_grid_uv(_Raw(6), None)[2]

    # 180 degrees keeps the axes but reverses both, so the spans go negative.
    half_turn = _image_to_grid_uv(_Raw(3), None)
    assert half_turn is not None
    assert half_turn[1] == pytest.approx(-7028 / span_x)
    assert half_turn[5] == pytest.approx(-4688 / span_y)


@requires_sample
@requires_exiftool
def test_the_dro_control_rebuilds_the_profile_without_a_decode() -> None:
    """Moving DRO is a profile rebuild, like the look and the five tweaks.

    A per-pixel scalar gain commutes with the colour matrix, so the shader can
    apply it to pixels the matrix already went through. That is what keeps the
    strength off the linear cache key.
    """
    root = Path(__file__).resolve().parents[3]
    rel = str(SAMPLE_IN.relative_to(root))

    shot = daemon_look_profile({"input": rel}, root)["colorProfile"]
    assert shot["droAvailable"] is True
    assert shot["droAsShot"] == 1.0 and shot["droStrength"] == 1.0
    assert shot["profileDroGain"] is not None
    # This sample is a Bayer ARW, so the grid builds and DRO runs as the local
    # operator it actually is — which is precisely when the approximation note
    # is not earned. A shot that falls back to the global path still gets it.
    grid = shot["profileDroGrid"]
    assert grid is not None
    assert len(grid["num"]) == grid["nx"] * grid["ny"] * grid["bins"]
    assert len(grid["den"]) == len(grid["num"]) and len(grid["uv"]) == 6
    assert not any("DRO uses this shot's own curve" in m for m in shot["limitations"])

    off = daemon_look_profile({"input": rel, "dro": 0}, root)["colorProfile"]
    assert off["droStrength"] == 0.0
    # Nothing to apply, so nothing is sent and nothing is disclaimed.
    assert off["profileDroGain"] is None
    assert not any("DRO" in m for m in off["limitations"])
    # Turning it off must not touch anything else about the render.
    assert off["profileToneCurve"] == shot["profileToneCurve"]
    assert off["droAsShot"] == 1.0

    up = daemon_look_profile({"input": rel, "dro": 2}, root)["colorProfile"]
    assert up["droStrength"] == 2.0
    assert max(up["profileDroGain"]) > max(shot["profileDroGain"])

    # And the same through the cached-decode path the client actually uses.
    assert apply_look_overrides(shot, SAMPLE_IN, None, None, 0.0)["profileDroGain"] is None
    back = apply_look_overrides(off, SAMPLE_IN, None, None, 1.0)
    assert back["profileDroGain"] == shot["profileDroGain"]
    assert back["limitations"] == shot["limitations"]


def test_the_dro_presets_reproduce_the_engines_own_ladder() -> None:
    """The baked preset table, checked against what the live engine hands out.

    These ten curves were read out of Edit.exe's memory rather than derived, so
    nothing else in this repo can catch a transcription slip in them. The lifts
    below are the measurements from that dump (sony_repro/tools/dro_presets.py);
    if a digit in the table moves, one of them moves with it.

    The ladder is also not a straight line — 0.175 a step for the first six and
    accelerating after — so a table quietly replaced by an interpolation of its
    endpoints would fail here too.
    """
    x = np.arange(DRO_CURVE_POINTS) * (DRO_LOG_CEILING / DRO_CURVE_POINTS)
    measured = [0.250, 0.425, 0.600, 0.775, 0.950, 1.125, 1.346, 1.624, 1.919, 2.222]
    for i, want in enumerate(measured):
        lift = float(np.abs(dro_preset_curve(i * 10) - x).max())
        assert lift == pytest.approx(want, abs=1e-3), f"preset {i}"

    # Every tenth level lands on a stored preset; the nine between interpolate.
    # Level 5 is the one the engine itself selects when the body says Auto and
    # the RAW carries no curve, so it has to land between P0 and P1 — but not at
    # their midpoint, and asserting that would be wrong. The engine blends the
    # knots and control points and *then* samples, and sampling is nonlinear in
    # the knots: the segment lookup and the u it normalises against both move.
    # Betweenness is the invariant that survives, so it is the one pinned here.
    low, half, high = (dro_preset_curve(n) for n in (0, 5, 10))
    assert np.all(half >= low - 1e-9) and np.all(half <= high + 1e-9)
    assert float(np.abs(half - x).max()) == pytest.approx(0.337, abs=2e-3)
    # Levels past the top pin to the last preset rather than running off the end.
    assert np.allclose(dro_preset_curve(99), dro_preset_curve(90), atol=2e-3)


def test_a_manual_dro_level_ignores_the_shots_own_curve_but_off_still_wins() -> None:
    """The three DRO states, and which one takes precedence.

    A level has to be independent of the frame — that is the whole point of the
    presets, and it is what lets DRO work on a shot the body wrote no curve for.
    Off has to beat a level that is merely still selected, or turning DRO off
    would silently do nothing whenever someone had picked a level earlier.
    """
    root = Path(__file__).resolve().parents[3]
    rel = str(SAMPLE_IN.relative_to(root))

    def lift(profile: dict[str, Any]) -> float:
        return float(np.abs(np.log2(np.array(profile["profileDroGain"]))).max())

    auto = daemon_look_profile({"input": rel}, root)["colorProfile"]
    assert auto["droLevel"] == -1
    assert auto["droLevels"] == list(DRO_UI_LEVELS)

    for level, want in zip(DRO_UI_LEVELS, (0.425, 0.775, 1.125, 1.624, 2.222), strict=True):
        got = daemon_look_profile({"input": rel, "droLevel": level}, root)["colorProfile"]
        assert got["droLevel"] == level
        # The preset's own lift, not this shot's 0.26 — the frame is not
        # consulted. Loose against the curve's own figure because the wire table
        # is 512 samples of it and the peak falls between two of them.
        assert lift(got) == pytest.approx(want, abs=1e-2)
        # A level is a different curve, not a scaled one: the grid it is indexed
        # by belongs to the file and has to survive the switch untouched.
        assert got["profileDroGrid"] == auto["profileDroGrid"]

    off = daemon_look_profile({"input": rel, "droLevel": 90, "dro": 0}, root)["colorProfile"]
    assert off["profileDroGain"] is None

    # And the level survives a look change through the no-decode path, which is
    # the only way the client can keep it while switching Creative Looks.
    picked = daemon_look_profile({"input": rel, "droLevel": 50}, root)["colorProfile"]
    moved = apply_look_overrides(picked, SAMPLE_IN, None, "VV")
    assert moved["droLevel"] == 50
    assert moved["profileDroGain"] == picked["profileDroGain"]


def test_clarity_follows_the_engines_own_ladder_and_offers_no_inert_stop() -> None:
    """The setting -> detail-gain lookup, as ZcTaskSIMDMarble does it.

    `clr = max(0, 10 * clarity)` indexes a ten-entry calibration table, so the
    positive half walks the table one entry per step and the *whole* negative
    half renders exactly like zero — Clarity does not soften, it only stops.
    Which is why the offered range starts at zero: a slider reaching further
    left would be handing the user nine stops that all render identically.
    """
    # The argument is Edit's 清晰 value, which is the engine's `clr`: the camera's
    # stops are its multiples of ten and land exactly on the table.
    for stop, amp in enumerate(CLARITY_AMP):
        assert clarity_amount(stop * 10) == amp / CLARITY_AMP_SCALE
    # Between them the engine blends the two entries either side.
    assert clarity_amount(15) == pytest.approx((CLARITY_AMP[1] + CLARITY_AMP[2]) / 2 / CLARITY_AMP_SCALE)
    # Monotone over the offered range, strictly so up to the table's last entry
    # (90); past it the amount holds, the engine's own answer there not having
    # been measured.
    assert TWEAK_RANGES["clarity"] == (0, CLARITY_MAX) == (0, 100)
    gains = [clarity_amount(n) for n in range(CLARITY_MAX + 1)]
    assert gains == sorted(gains)
    assert len(set(gains[:91])) == 91
    assert clarity_amount(100) == clarity_amount(90)
    # Outside it the value clamps rather than running off the table, which is
    # what stops a hand-written request from raising IndexError. A negative one
    # still lands on zero, i.e. on what the engine would have rendered it as.
    for setting in range(-20, 0):
        assert clarity_amount(setting) == 0.0
    assert clarity_amount(999) == clarity_amount(CLARITY_MAX)


def test_the_profile_carries_clarity_as_the_shaders_own_constants() -> None:
    """Clarity reaches the browser as a gain plus the blur chain's geometry.

    It is the one Creative Look tweak that is spatial, so unlike the other five
    it cannot be folded into the tone curve — the shader has to rebuild the
    engine's base layer, and these are the numbers it needs. They come from the
    body's calibration, so they stay put while only the gain tracks the slider.
    """
    root = Path(__file__).resolve().parents[3]
    rel = str(SAMPLE_IN.relative_to(root))

    base = daemon_look_profile({"input": rel}, root)["colorProfile"]
    shot = base["lookAsShot"]["clarity"]
    assert base["profileClarity"]["gain"] == pytest.approx(clarity_amount(shot))

    geometry = {k: v for k, v in base["profileClarity"].items() if k != "gain"}
    for setting in (-9, 0, 1, 9):
        got = daemon_look_profile({"input": rel, "look": {"clarity": setting}},
                                  root)["colorProfile"]
        # A negative comes back as the zero it renders as, rather than being
        # echoed: what the client is told the setting is has to be a setting the
        # panel can show, and the ranges it builds those sliders from say 0..9.
        assert got["lookTweaks"]["clarity"] == max(0, setting)
        assert got["lookRanges"]["clarity"] == [0, CLARITY_MAX]
        # Exact, not approx, at the bottom: zero gain is how the renderer is told
        # to skip the chain outright, so the bottom of the range must not come
        # out as a small positive.
        gain = got["profileClarity"]["gain"]
        assert gain == (0.0 if setting <= 0 else pytest.approx(clarity_amount(setting)))
        # Only the gain moves: the downsample factor, the edge threshold and the
        # rolloff knee are the camera's, not the setting's.
        assert {k: v for k, v in got["profileClarity"].items() if k != "gain"} == geometry
        # And a shot that is actually getting Clarity says whose calibration it
        # is using, since there is no way to read this body's own.
        assert (CLARITY_CALIBRATION_BODY in " ".join(got["limitations"])) == (gain > 0)


def test_sharpening_walks_two_ladders_that_disagree_about_invalid_values() -> None:
    """Both settings map to the amplitude, and they fail differently.

    `lvl = 10*(sharpness - 4)` lands on a table index, so an impossible value
    falls back to the camera's default. `rng = 10*range + 20` is a *factor*, so
    an impossible one leaves it at zero and multiplies the whole stage out. That
    asymmetry is measured (tools/sharp_sweep.py), not assumed, and it is the
    easiest thing here to get backwards.
    """
    # The camera's own defaults with this body's calibration are the constant
    # Edit.exe's tiles were reproduced against.
    assert sharpness_amount(SHARPNESS_DEFAULT, SHARPNESS_RANGE_DEFAULT, 1.7) == 85 / 1024
    # Sharpness is linear in the setting and strictly increasing.
    gains = [sharpness_amount(n, SHARPNESS_RANGE_DEFAULT, 1.7) for n in range(SHARPNESS_MAX + 1)]
    assert gains == sorted(gains)
    assert len(set(gains)) == len(gains)
    assert gains[0] > 0        # even +0 sharpens: lvl = -40 is a weaker gain, not off
    # Out of range: the fallback, not a clamp to the nearest end.
    for bad in (-1, -9, 10, 99):
        assert sharpness_amount(bad, SHARPNESS_RANGE_DEFAULT, 1.7) == gains[SHARPNESS_DEFAULT]
    # Range, by contrast, switches the stage off outright when it is invalid.
    for bad in (-1, -5, 6, 99):
        assert sharpness_amount(SHARPNESS_DEFAULT, bad, 1.7) == 0.0
    # Its ladder lands on unity at the camera's default, which is why that
    # default neither boosts nor cuts what the Sharpness ladder asked for.
    # Stated against the ladder's own constants, so that moving one of them
    # without the other is what fails.
    assert (SHARPNESS_RANGE_STEP * SHARPNESS_RANGE_DEFAULT
            + SHARPNESS_RANGE_BASE) == SHARPNESS_RANGE_UNITY
    ratios = [sharpness_amount(SHARPNESS_DEFAULT, r, 1.7) / (85 / 1024)
              for r in range(SHARPNESS_RANGE_MAX + 1)]
    assert ratios == sorted(ratios)


def test_the_sharpen_scale_comes_out_of_the_raw_not_out_of_a_table() -> None:
    """`calib[0x1060]` is tag 0x78cd, which is what makes this stage offline.

    Clarity's amplitudes had to be dumped from a live body; this one is in the
    file, per shot. A reader that silently returned the default instead would
    look right — every frame would still sharpen — while being wrong by up to
    70%, so the value is pinned against the sample rather than just checked for
    plausibility.
    """
    assert sharpness_calibration(SAMPLE_IN) == pytest.approx(1.7)
    # Anything that is not a readable Sony RAW is the engine's initialised 1.0,
    # not an exception: callers asked what the body did, and "no adjustment" is
    # a real answer.
    assert sharpness_calibration(Path(__file__)) == SHARPNESS_CALIB_DEFAULT
    assert sharpness_calibration(SAMPLE_IN.with_name("no-such-file.ARW")) == SHARPNESS_CALIB_DEFAULT


def test_the_profile_carries_sharpening_and_it_survives_a_look_change() -> None:
    """Sharpening reaches the browser as one amplitude, and stays put.

    It is a camera setting rather than a Creative Look tweak, so no slider the
    client can move may disturb it — but a profile rebuild goes back through
    look_render_info, which is exactly where it could get quietly reset to a
    default. Carrying the block forward, the way the DRO grid is, is what stops
    that; rebuilding it would mean re-reading the RAW for a number that cannot
    have moved.
    """
    root = Path(__file__).resolve().parents[3]
    rel = str(SAMPLE_IN.relative_to(root))

    base = daemon_look_profile({"input": rel}, root)["colorProfile"]
    block = base["profileSharpness"]
    # The sample is a +4 / +3 frame, so its amplitude is the ladder's default
    # scaled by the calibration read out of this very file — which is the whole
    # point of the stage being computable offline.
    assert block["amount"] == pytest.approx(
        sharpness_amount(SHARPNESS_DEFAULT, SHARPNESS_RANGE_DEFAULT,
                         sharpness_calibration(SAMPLE_IN)))
    assert block["amount"] > 0

    # A shot that is sharpening says what it cannot match; one that is not has
    # nothing to qualify. Both halves are running on this frame, so both notes
    # are there — and neither may still claim Spica is unreproduced, which is
    # what this list said before spica.py existed.
    notes = " ".join(base["limitations"])
    assert "full resolution" in notes
    assert "not reproduced" not in notes.replace("ITP stage is not reproduced", "")

    # The panel reads these; the shader does not. A block that carried only the
    # amplitude would render identically and show nothing.
    assert block["level"] == SHARPNESS_DEFAULT
    assert block["range"] == SHARPNESS_RANGE_DEFAULT

    # Neither a look change nor a moved tweak may touch either block.
    spica = base["profileSpica"]
    assert spica["amount"] == pytest.approx(spica_amount(SHARPNESS_DEFAULT, SHARPNESS_RANGE_DEFAULT))
    for overrides, style in (({"contrast": 3}, None), (None, "VV"), ({"clarity": 9}, "ST")):
        moved = apply_look_overrides(base, SAMPLE_IN, overrides, style)
        assert moved["profileSharpness"] == block
        assert moved["profileSpica"] == spica


@requires_sample
def test_the_profile_carries_chroma_suppres_and_it_survives_a_look_change() -> None:
    """ChromaSuppres reaches the browser as four ints, and stays put.

    Its terms come out of four SR2 calibration tags — the body's own rolloff, not
    a setting — so no slider here can move them and a profile rebuild must carry
    them rather than quietly drop them. Dropping them would render the
    highlights with their full chroma, which is what this path did before
    sony/chromasuppres.py existed.
    """
    cal = calibration_for(SAMPLE_FL, "FL")
    assert cal is not None
    block = {"hiY": 15360, "loY": 0, "slopeHi": 512, "slopeLo": 512}

    payload = look_render_info(cal, "FL", chroma_suppres=block).to_json()
    assert payload["profileChromaSuppres"] == block
    # A file whose four tags could not be read renders with the stage off, and
    # the key still has to be on the wire for the consumer to branch on.
    assert look_render_info(cal, "FL").to_json()["profileChromaSuppres"] is None

    for overrides, style in (({"contrast": 3}, None), (None, "VV")):
        moved = apply_look_overrides(payload, SAMPLE_FL, overrides, style)
        assert moved["profileChromaSuppres"] == block


def test_spica_is_weighted_against_sharpening_rather_than_beside_it() -> None:
    """One control, split across two stages — so the two move in opposition.

    The easy mistake is to treat this as a second independent strength. It is
    not: `(100 - st[0x2c4])` is the same ladder sharpening uses, read the other
    way, and the two weights are what decides how much of the effect lands on
    each end. A shot at the camera's defaults puts exactly half here.
    """
    assert spica_amount(SHARPNESS_DEFAULT, SHARPNESS_RANGE_DEFAULT) == 0.5

    # Range moves the split: up sends the effect to the coarse stage, so this
    # one falls as sharpening's rises. Strictly, at every step.
    fine = [spica_amount(SHARPNESS_DEFAULT, r) for r in range(SHARPNESS_RANGE_MAX + 1)]
    coarse = [sharpness_amount(SHARPNESS_DEFAULT, r, 1.7) for r in range(SHARPNESS_RANGE_MAX + 1)]
    assert fine == sorted(fine, reverse=True)
    assert coarse == sorted(coarse)
    # Never off at either end of the ladder: the split moves the effect between
    # the two, it does not switch one of them out.
    assert min(fine) > 0

    # Sharpness itself is the shared strength, so it raises both.
    levels = [spica_amount(n, SHARPNESS_RANGE_DEFAULT) for n in range(SHARPNESS_MAX + 1)]
    assert levels == sorted(levels)
    assert len(set(levels)) == len(levels)

    # An unreadable range is where the two stages genuinely disagree. It zeroes
    # sharpening's amplitude, so the same tag has to send *more* here, not zero
    # — the term is `100 - opts[0x2c4]` and the engine leaves that field at 0.
    # spica_off is what a caller with nothing to read should use instead.
    assert sharpness_amount(SHARPNESS_DEFAULT, -1, 1.7) == 0.0
    assert spica_amount(SHARPNESS_DEFAULT, -1) > spica_amount(SHARPNESS_DEFAULT,
                                                              SHARPNESS_RANGE_DEFAULT)
    assert spica_off()["amount"] == 0.0


@requires_sample
def test_the_iso_actually_reaches_spica() -> None:
    """The gain curve was right and nothing ever fed it an ISO.

    `spica_iso_gain` had tests; the wiring did not, so the exiftool call simply
    never asked for -ISO and every frame got the un-attenuated fine-detail
    boost. Reading the tag is what this asserts — testing the pure function
    again would not have caught it.
    """
    exif = read_exiftool_metadata(SAMPLE_FL)
    assert exif.get("ISO"), "ISO missing from the exif read"
    block = spica_from_exif(exif)
    assert block is not None
    assert block["isoGain"] == pytest.approx(spica_iso_gain(int(exif["ISO"])))
    assert block["gainScale"] == pytest.approx(spica_gain_scale(int(exif["ISO"])))
    assert block["rangeShift"] == pytest.approx(spica_range_shift(int(exif["ISO"])))


def test_spica_iso_gain_bends_only_above_the_knee() -> None:
    """Three breakpoints, of which the first segment is flat.

    Read two ways that had to agree: out of the preset's config block
    (400 / 1600 / 25600 -> 1 / 1 / 0.75) and solved from the engine's own pixels
    across twelve ISOs. The one measured point on the ramp is ISO 4000 at
    0.975, which is what pins the slope — a curve that started bending at 400
    instead would still be 1.0 at 100 and 0.75 at 25600.
    """
    for iso in (50, 100, 400, 800, 1600):
        assert spica_iso_gain(iso) == 1.0
    assert spica_iso_gain(4000) == pytest.approx(0.975)
    assert spica_iso_gain(25600) == pytest.approx(0.75)
    # Flat past the top rather than continuing down through zero.
    assert spica_iso_gain(51200) == pytest.approx(0.75)
    assert spica_iso_gain(409600) == pytest.approx(0.75)
    # Monotone in between, and never outside the two endpoints.
    ramp = [spica_iso_gain(i) for i in range(1600, 25601, 800)]
    assert ramp == sorted(ramp, reverse=True)
    assert min(ramp) >= 0.75 and max(ramp) <= 1.0
    # An unreadable ISO renders at what most frames get, not at a neutral
    # invented for the occasion.
    assert spica_iso_gain(None) == 1.0


# ── The camera's own noise model ───────────────────────────────────────────


@requires_sample
def test_the_noise_threshold_rises_with_iso() -> None:
    """The tags are a real per-shot measurement, not a constant riding along.

    ISO 100 against ISO 1250 on the same body: every level has to be allowed a
    larger deviation on the noisier frame. A model read out of the wrong tags,
    or one that ignored the strength scaling, would tie rather than separate.
    """
    if not SAMPLE_HIGH_ISO.exists():
        pytest.skip("high-ISO sample not checked out")
    base = noise_model(SAMPLE_IN)
    high = noise_model(SAMPLE_HIGH_ISO)
    assert base is not None and high is not None

    levels = np.arange(0, ENGINE_FULL_SCALE, 64)
    assert np.all(high.threshold(levels) > base.threshold(levels))
    # And it is a threshold, not a fraction: single digits at base ISO on a
    # 14-bit scale, which is why the stage is nearly a no-op there.
    assert base.threshold(0) < 8


@requires_sample
def test_the_threshold_stops_climbing_in_the_highlights() -> None:
    """Flat, then a ramp, then flat again — not a shot-noise square root.

    The upper knee is the load-bearing part: it sits at an eighth of full scale,
    so across almost the whole tonal range Sony holds the threshold constant
    rather than letting it grow with the signal. A denoiser that assumed
    sqrt(level) would smooth highlights this model deliberately leaves alone.
    """
    model = noise_model(SAMPLE_FL)
    assert model is not None
    assert model.lo < model.hi < ENGINE_FULL_SCALE

    below = model.threshold(np.array([0, model.lo // 2, model.lo]))
    assert len(set(below.tolist())) == 1
    above = model.threshold(np.array([model.hi, model.hi + 1, ENGINE_FULL_SCALE]))
    assert len(set(above.tolist())) == 1
    assert above[0] > below[0]

    # Monotone in between.
    ramp = model.threshold(np.arange(model.lo, model.hi + 1))
    assert np.all(np.diff(ramp) >= 0)


@requires_sample
def test_the_normalised_curve_is_the_same_shape_in_unit_scale() -> None:
    """denoise works in [0, 1]; the conversion must not reshape the curve."""
    model = noise_model(SAMPLE_FL)
    assert model is not None
    levels = np.array([0, 512, 2048, 8192, ENGINE_FULL_SCALE])
    unit = model.noise_shape_at(levels / ENGINE_FULL_SCALE)
    assert unit == pytest.approx(model.threshold(levels) / ENGINE_FULL_SCALE)
    assert np.all(unit > 0.0) and np.all(unit < 1.0)


def test_a_file_without_the_tags_has_no_noise_model() -> None:
    """None rather than an exception: every caller's answer is the same fallback.

    The SR2 walk can fail at any step on a non-Sony file, not only at the
    missing-tag check, so this goes through a file that is not even a TIFF.
    """
    path = SAMPLES / "test.svg"
    if not path.exists():
        pytest.skip("sample not checked out")
    assert noise_model(path) is None


@requires_sample
def test_the_three_plane_strengths_agree() -> None:
    """The assumption `noise_model` rests on: one curve speaks for the frame.

    The engine keeps a strength per plane and this reads the first. That is only
    honest while all three match, which they do on every frame checked in — so
    assert it rather than leave it as a comment, and a body that ever splits
    them fails here instead of being silently read on plane 0 alone.
    """
    for path in (SAMPLE_FL, SAMPLE_HIGH_ISO):
        tags = read_sr2_scalars(path, STRENGTH_TAGS)
        assert set(tags) == set(STRENGTH_TAGS)
        assert len(set(tags.values())) == 1, f"{path.name}: strengths differ, {tags}"


@requires_sample
def test_the_three_plane_detail_pairs_agree() -> None:
    """`detail_restore` reads plane 0's gain and limit and speaks for the frame.

    Same contract as the strengths above, and worth its own assertion because a
    per-plane split would land on the colour differences specifically, which is
    where the remaining gap to Edit lives. Checked over 65 frames when this was
    written; the two samples here are the regression.
    """
    for path in (SAMPLE_FL, SAMPLE_HIGH_ISO):
        for group in (DETAIL_GAIN_TAGS, DETAIL_LIMIT_TAGS):
            tags = read_sr2_scalars(path, group)
            assert set(tags) == set(group)
            assert len(set(tags.values())) == 1, f"{path.name}: {group} differ, {tags}"


@requires_sample
def test_the_detail_gain_is_the_raw_tag_and_may_exceed_restore_everything() -> None:
    """Pins that the engine's own ``min(tag, 256)`` clamp is *not* applied.

    The clamp is real — probing RawNRSIMD's parameter block gives 256 for tags
    of 480 and 268 and the tag itself for 249 and 216. Applying it here was
    tried and measured worse: luma detail moved further below Edit's while the
    colour differences stayed put, because this gain drives a wavelet rather
    than Sony's sigma filter and above 256 it is compensating for the wavelet
    costing more detail. Asserting the raw value keeps a future reader from
    "fixing" it back on the strength of the engine parameter alone.
    """
    for path in (SAMPLE_FL, SAMPLE_HIGH_ISO):
        raw = read_sr2_scalars(path, (DETAIL_GAIN_TAGS[0],))[DETAIL_GAIN_TAGS[0]]
        restore = detail_restore(path)
        assert restore is not None
        assert restore.gain == raw
        assert restore.fraction == raw / DETAIL_GAIN_UNIT


@requires_sample
def test_the_camera_curve_stabilises_through_the_denoiser_it_feeds() -> None:
    """The two halves only meet in cli.py, so bind them somewhere tested.

    `NoiseModel` promises a `NoiseCurve`; this is the assertion that the real
    one — integer thresholds off a real frame, flat top and all — actually
    drives `_CurveVST` to equal noise at every brightness, which is the whole
    point of reading it.
    """
    model = noise_model(SAMPLE_HIGH_ISO)
    assert model is not None
    vst = _CurveVST(model)
    rng = np.random.default_rng(7)
    stds = []
    for level in (0.02, 0.1, 0.5, 0.95):
        sigma = float(model.noise_shape_at(np.array(level)))
        s = (level + rng.normal(0.0, sigma, 40000)).astype(np.float32)
        stds.append(float(vst.forward(s).std()))
    assert max(stds) / min(stds) < 1.15


@pytest.mark.parametrize("iso, gain, shift", [
    # cfg[0xc4] and the range trapezoid's `a`, read out of Edit.exe at 0x35a270
    # for five frames (sony_repro/tools/spica_gaincfg_*.json); gain / 2048 is
    # what the shader multiplies by, `a` - 128 is the shift.
    (100, 4096.0, 0.0), (320, 4096.0, 0.0), (1250, 2645.333, 90.667),
    (2000, 2030.933, 132.267), (4000, 1945.6, 153.6),
])
def test_spica_iso_ramps_reproduce_the_engines_config(iso, gain, shift) -> None:
    assert spica_gain_scale(iso) * 2048.0 == pytest.approx(gain, abs=0.01)
    assert spica_range_shift(iso) == pytest.approx(shift, abs=0.01)
    assert spica_gain_scale(None) == SPICA_GAIN_SCALE and spica_range_shift(None) == 0.0


def test_spica_off_carries_the_base_iso_ramps() -> None:
    off = spica_off()
    assert off["gainScale"] == SPICA_GAIN_SCALE and off["rangeShift"] == 0.0


# ── The M/S-size frames' white balance ─────────────────────────────────────


def _tiff_with_subifd(entries: list[tuple[int, int, int, bytes]], subifd_extra: bytes = b"") -> bytes:
    """A little-endian TIFF: IFD0 holding only SubIFDs (0x14a), pointing at one
    SubIFD built from `entries` = [(tag, type, count, value bytes)]; values longer
    than four bytes go out of line, as the real file writes 0x7039."""
    ifd0_pos = 8
    ifd0 = struct.pack("<H", 1) + struct.pack("<HHII", 0x014A, 4, 1, 0) + struct.pack("<I", 0)
    sub_pos = ifd0_pos + len(ifd0)
    n = len(entries)
    body_pos = sub_pos + 2 + 12 * n + 4
    dir_bytes, body = b"", b""
    for tag, typ, cnt, val in entries:
        if len(val) <= 4:
            dir_bytes += struct.pack("<HHI", tag, typ, cnt) + val.ljust(4, b"\0")
        else:
            dir_bytes += struct.pack("<HHII", tag, typ, cnt, body_pos + len(body))
            body += val
    sub = struct.pack("<H", n) + dir_bytes + struct.pack("<I", 0) + body
    ifd0 = struct.pack("<H", 1) + struct.pack("<HHII", 0x014A, 4, 1, sub_pos) + struct.pack("<I", 0)
    return b"II*\0" + struct.pack("<I", ifd0_pos) + ifd0 + sub + subifd_extra


def test_the_ycc_frame_s_wb_ratio_is_read_as_three_rationals(tmp_path: Path) -> None:
    """DSC00062's own numbers: WB_RGGBLevels over another set of levels."""
    from llr_worker.sony.sr2 import ycc_wb_scale

    ratio = struct.pack("<6I", 1823, 1497, 1024, 1024, 2721, 3322)
    path = tmp_path / "m.arw"
    path.write_bytes(_tiff_with_subifd([(0x7000, 4, 1, struct.pack("<I", 4)), (0x7039, 5, 3, ratio)]))
    got = ycc_wb_scale(path)
    assert got is not None
    assert got == pytest.approx((1823 / 1497, 1.0, 2721 / 3322))


def test_a_mosaic_frame_has_no_wb_ratio(tmp_path: Path) -> None:
    """The tag is only written beside the YCbCr image; absent means None, not 1."""
    from llr_worker.sony.sr2 import ycc_wb_scale

    path = tmp_path / "l.arw"
    path.write_bytes(_tiff_with_subifd([(0x7000, 4, 1, struct.pack("<I", 4))]))
    assert ycc_wb_scale(path) is None
    junk = tmp_path / "junk.bin"
    junk.write_bytes(b"not a tiff at all")
    assert ycc_wb_scale(junk) is None
    assert ycc_wb_scale(tmp_path / "missing.arw") is None


class _FakeRaw:
    def __init__(self) -> None:
        self.camera_whitebalance = [1823.0, 1024.0, 2721.0, 1024.0]


def test_the_ratio_multiplies_the_camera_s_multipliers() -> None:
    """Edit's WB for these frames is camera WB times the ratio, G2 riding with G;
    without a ratio (or a unit one) the decode is LibRaw's use_camera_wb exactly."""
    from llr_worker.fit_profile import libraw_wb_kwargs

    assert libraw_wb_kwargs(_FakeRaw(), None) == {"use_camera_wb": True}
    assert libraw_wb_kwargs(_FakeRaw(), (1.0, 1.0, 1.0)) == {"use_camera_wb": True}
    got = libraw_wb_kwargs(_FakeRaw(), (1.2, 1.0, 0.8))
    assert list(got) == ["user_wb"]
    assert got["user_wb"] == pytest.approx([1823 * 1.2, 1024.0, 2721 * 0.8, 1024.0])


# ── Creative Look "Off" ────────────────────────────────────────────────────


@requires_sample
def test_the_file_s_own_stamp_names_the_shot_s_look() -> None:
    """The top-level chroma block is a copy of the as-shot look's, so the file
    can say which look it was — the route an "Off" frame takes to Standard."""
    from llr_worker.sony import as_shot_look

    assert as_shot_look(SAMPLE_FL) == "FL"
    assert as_shot_look(SAMPLE_IN) == "IN"


def test_a_file_without_calibration_has_no_stamp(tmp_path: Path) -> None:
    from llr_worker.sony import as_shot_look

    path = tmp_path / "not-a-raw.bin"
    path.write_bytes(b"nothing to see here")
    assert as_shot_look(path) is None


@requires_sample
def test_off_resolves_to_the_look_the_file_stamps() -> None:
    """A look the file carries is taken at its word; "Off" is not one, so the
    stamp answers; nothing at all stays nothing."""
    from llr_worker.cli import resolve_as_shot_style

    assert resolve_as_shot_style("FL", SAMPLE_FL) == "FL"
    assert resolve_as_shot_style("VV2", SAMPLE_FL) == "VV2"
    assert resolve_as_shot_style("Off", SAMPLE_FL) == "FL"
    assert resolve_as_shot_style(None, SAMPLE_FL) is None


def test_off_stays_off_when_the_file_cannot_say(tmp_path: Path) -> None:
    """Keeps the exif code so the DCP selection downstream still sees it."""
    from llr_worker.cli import resolve_as_shot_style

    path = tmp_path / "not-a-raw.bin"
    path.write_bytes(b"nothing to see here")
    assert resolve_as_shot_style("Off", path) == "OFF"
