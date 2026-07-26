"""Sony Imaging Edge reproduction: the SR2 calibration decode and the render.

The reverse-engineering itself is verified in ../../../sony_repro against frida
dumps of Edit.exe's own memory. These tests pin the port: the bit-level decode,
the invariants the segment matrices must hold, and the frontend contract.
"""

import struct
from pathlib import Path

import numpy as np
import pytest

from llr_worker.cli import _exif_int, detect_exiftool, prepare_linear, read_exiftool_metadata
from llr_worker.sony import apply_sony_profile, available_styles, calibration_for, can_render
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
    chroma_terms,
    sepia_toning,
    tone_curve_points,
)
from llr_worker.sony.sr2 import (
    PARAM_BLOCK_SIZE,
    decrypt,
    look_calibrations,
    read_sr2_tag,
    unpack_param_block,
)
from llr_worker.sony.tone import (
    LOOK_ORDER,
    TUNE_EXTRAPOLATION_LIMIT,
    TUNE_LIMIT,
    apply_tuning,
    base_curve,
    tone_curve,
)

SAMPLES = Path(__file__).resolve().parents[3] / "samples"
# Both shot on an ILCE-7CM2, Creative Look FL and IN respectively.
SAMPLE_FL = SAMPLES / "DSC01157.ARW"
SAMPLE_IN = SAMPLES / "DSC04568.ARW"

requires_sample = pytest.mark.skipif(not SAMPLE_FL.exists(), reason="sample ARW not checked out")
requires_exiftool = pytest.mark.skipif(detect_exiftool() is None, reason="exiftool not installed")


@requires_sample
def test_dro_is_only_disclaimed_on_shots_that_used_it() -> None:
    """It is a per-shot stage, so an unconditional caveat would be wrong.

    Sony runs ZcTaskVatr only when the shot asked for DRO — 2 frames in 65 of
    the reference set. That was how the stage was identified: the census of a
    DRO frame and a non-DRO one differ in exactly this one entry.
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
    assert saturation_factor(0) == 1.0
    assert saturation_factor(9) == pytest.approx(1.55)
    assert saturation_factor(-9) == pytest.approx(0.45)
    assert saturation_factor(2) == pytest.approx(1.20), "10 per step below 2"
    assert saturation_factor(3) == pytest.approx(1.25), "5 per step above it"
    assert saturation_factor(99) == saturation_factor(9), "clamped, not extrapolated"

    cross, gain = unpack_params(VV2_CHROMA)
    rng = np.random.default_rng(11)
    img = rng.random((64, 64, 3), dtype=np.float32) * 0.5 + 0.2
    plain = apply_chroma(img, cross, gain, *FADE0)
    # rgb_to_ycc does both halves itself, so it takes the look's own gains — the
    # divided ones are only for the shader, which can only do the multiply.
    for s in (3, 9, -3):
        got = apply_chroma(img, cross, gain, *FADE0, saturation=saturation_factor(s))
        moved = np.abs(got - plain)
        # Identical wherever the intermediate chroma stayed inside the clamp,
        # and different only where it did not.
        assert np.median(moved) < 1e-6
        assert moved.max() > 1e-3


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

    pivot5, contrast5 = luma_terms(cal, 5)
    assert pivot5 > 0.5
    assert contrast5 < 1.0
    # Monotone in the setting, and clamped rather than extrapolated past the end.
    contrasts = [luma_terms(cal, f)[1] for f in range(10)]
    assert contrasts == sorted(contrasts, reverse=True)
    assert luma_terms(cal, 99) == luma_terms(cal, 9)

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
        return [y for _, y in apply_sony_profile(rgb, cal, "FL", **kw)[1].tone_curve]

    plain = curve()
    for field in ("highlights", "shadows", "contrast"):
        assert curve(**{field: 5}) != plain, f"{field} never reached the curve"
    for field in ("fade", "saturation"):
        assert curve(**{field: 5}) == plain, f"{field} does not belong on the curve"
    sat = apply_sony_profile(rgb, cal, "FL", saturation=9)[1]
    assert sat.chroma_saturation == pytest.approx(1.55)
    assert sat.chroma_gain == pytest.approx([g / 1.55 for g in apply_sony_profile(
        rgb, cal, "FL")[1].chroma_gain])
    # Fade drives YGamma, not the curve — so the curve must NOT move, and the
    # two luma terms must.
    faded = apply_sony_profile(rgb, cal, "FL", fade=5)[1]
    assert [y for _, y in faded.tone_curve] == plain
    assert faded.luma_pivot > 0.5
    assert faded.luma_contrast < 1.0
    assert faded.to_json()["profileLumaPivot"] == faded.luma_pivot


@requires_sample
def test_contrast_is_linear_going_down_and_measured_going_up() -> None:
    """The one tweak whose two directions are not the same kind of thing.

    Negative Contrast behaves like Highlights and Shadows — one shape, scaled.
    Positive Contrast changes shape as well as size: rescaling the +9 shape down
    to +3 leaves 44/16384 against a total amplitude of 110, so every step is
    measured. Interpolation between them still has to be monotone and to land
    exactly on the measured steps at whole settings.
    """
    cal = calibration_for(SAMPLE_FL, "FL")
    assert cal is not None
    base = base_curve(cal)
    assert apply_tuning(base, "FL", contrast=+6)[1024] > base[1024]
    assert apply_tuning(base, "FL", contrast=-6)[1024] < base[1024]

    down_full = apply_tuning(base, "FL", contrast=-9) - base
    down_third = apply_tuning(base, "FL", contrast=-3) - base
    assert np.allclose(down_third * 3, down_full, atol=1e-6), "the negative side is linear"

    up_full = apply_tuning(base, "FL", contrast=+9) - base
    up_third = apply_tuning(base, "FL", contrast=+3) - base
    assert np.abs(up_third * 3 - up_full).max() > 1e-3, "the positive side is not"

    # Between measured steps, and monotone across the whole range.
    mids = [apply_tuning(base, "FL", contrast=c)[1024] for c in range(10)]
    assert mids == sorted(mids)
    half = apply_tuning(base, "FL", contrast=4)[1024]
    assert mids[3] <= half <= mids[5]


@requires_sample
def test_a_setting_past_the_camera_s_range_keeps_going() -> None:
    """Edit.exe refuses out-of-range values; this pipeline extrapolates them.

    The engine renders +-10 identically to 0, which is validation on a number
    the body cannot write rather than the curve running out. The response is
    linear, so the same unit shape carries on — and it is capped, so a wild
    setting cannot run away.
    """
    cal = calibration_for(SAMPLE_FL, "FL")
    assert cal is not None
    base = base_curve(cal)
    unit = apply_tuning(base, "FL", highlights=-1) - base

    beyond = apply_tuning(base, "FL", highlights=-(TUNE_LIMIT + 1)) - base
    assert not np.array_equal(beyond, np.zeros_like(beyond)), "no longer ignored"
    assert np.allclose(beyond, unit * (TUNE_LIMIT + 1), atol=1e-6)

    capped = apply_tuning(base, "FL", highlights=-(TUNE_EXTRAPOLATION_LIMIT + 50))
    assert np.array_equal(capped, apply_tuning(base, "FL", highlights=-TUNE_EXTRAPOLATION_LIMIT))
    assert capped.min() >= 0.0 and capped.max() <= 1.0


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
