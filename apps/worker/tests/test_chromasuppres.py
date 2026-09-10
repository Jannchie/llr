"""ChromaSuppres, the engine's highlight chroma rolloff (sony/chromasuppres.py).

The stage was verified against Imaging Edge's own buffers before this port
existed — three full-resolution tiles captured at export, all three planes bit
exact. These tests pin the port to that: the integer form against a crop of one
of those tiles, the derivation against the engine's own interpolation, and the
float path the shader mirrors against the integer one it stands in for.
"""

from pathlib import Path

import numpy as np
import pytest

from llr_worker.sony.chroma import apply_chroma
from llr_worker.sony.chromasuppres import (
    CHROMA_CENTRE,
    Y_FULL_SCALE,
    ChromaSuppresTerms,
    apply_chroma_suppres_planes,
    chroma_suppres_from_file,
    chroma_suppres_gain,
    chroma_suppres_terms,
)

FIXTURE = Path(__file__).parent / "fixtures" / "chromasuppres_tile.npz"

# The ILCE-7CM2's four tags, in the order chroma_suppres_terms reads them:
# A[3] (0x787e), B[3] (0x787f), slopeLo (0x7880), ratio (0x7881). The same eight
# values came back on all five frames checked.
ANCHORS = [512, 112, 128, 15360, 15360, 15360, 512, 0]


def test_the_integer_stage_reproduces_the_engine_s_own_tile_bit_for_bit() -> None:
    """The claim the module rests on, checked on the engine's own pixels.

    The fixture is a 96x96 crop of a tile captured at export (DSC03036, ISO
    2000) with 1239 pixels above hiY, so both halves of the operator are
    exercised: the flat 255/256 the mid-tones get and the linear fade above the
    knee. Nothing here is approximate — every one of the three planes has to
    match exactly, because the engine's own output is what "correct" means.
    """
    data = np.load(FIXTURE)
    terms = ChromaSuppresTerms(int(data["hi_y"]), int(data["lo_y"]),
                               int(data["slope_hi"]), int(data["slope_lo"]))
    assert terms == ChromaSuppresTerms(15356, 0, 512, 512)

    got = apply_chroma_suppres_planes(data["tile_in"], terms)
    want = data["tile_out"]
    assert got.dtype == want.dtype
    for plane, name in enumerate(("Y", "Cr", "Cb")):
        assert np.array_equal(got[..., plane], want[..., plane]), name

    # The crop was chosen for this: a stage tested only on mid-tones would pass
    # with the highlight branch deleted.
    y = data["tile_in"][..., 0].astype(np.int16)
    assert int((y > terms.hi_y).sum()) == 1239


def test_the_terms_come_out_of_the_anchors_the_way_the_engine_derives_them() -> None:
    """RVA 0x36e920, on the axis the engine reads its ISO from.

    x = 0 is where every measured frame sits (ISO 2000 on this body), and there
    both interpolations collapse onto A0/B0 — so the tags are the terms. Moving
    down the axis is what the other two anchors are for, and the ramp that comes
    with them lifts the slope while pulling the knee down: at half a stop below
    the origin hiY has already dropped 600 luma units.
    """
    assert chroma_suppres_terms(ANCHORS) == ChromaSuppresTerms(15360, 0, 512, 512)

    # Halfway down the upper band: A interpolates A0 -> A1, and ramp = 0.5 adds
    # 75 to the slope and takes 600 off the knee.
    assert chroma_suppres_terms(ANCHORS, x=-0.5) == ChromaSuppresTerms(14760, 0, 387, 512)
    # Halfway down the lower band: A now interpolates A1 -> A2 instead, and the
    # ramp is measured from the other end, so the knee lands in the same place.
    assert chroma_suppres_terms(ANCHORS, x=-1.5) == ChromaSuppresTerms(14760, 0, 195, 512)
    # Past both bands nothing interpolates and the ramp is zero, so an ISO axis
    # the engine never reaches renders exactly as the origin does — not as an
    # extrapolation off the end of the anchors.
    assert chroma_suppres_terms(ANCHORS, x=-3.0) == chroma_suppres_terms(ANCHORS)


def test_the_ratio_tag_is_what_puts_a_knee_in_the_shadows() -> None:
    """loY is hiY scaled by the lo/hi ratio, and zero on every frame measured.

    Nothing in the corpus has a non-zero ratio, so the shadow branch has never
    run on a real file — which is exactly why it is pinned here rather than left
    to be discovered by the first frame that does.
    """
    anchors = [*ANCHORS[:7], 2048]
    terms = chroma_suppres_terms(anchors)
    assert terms.lo_y == 15360 * 2048 // 15360
    assert terms.hi_y == 15360

    # A B of zero is the degenerate case the engine guards against; the guard is
    # transcribed rather than reasoned about, so it gets a test.
    flat = chroma_suppres_terms([512, 112, 128, 0, 0, 0, 512, 2048])
    assert flat.hi_y == 0
    assert flat.lo_y == 0


def test_the_float_gain_tracks_the_integer_stage_within_a_level() -> None:
    """The shader's path, checked against the one that is bit-exact.

    The shader has no 14-bit planes: it carries normalised luma and multiplies
    the chroma by f/256 as a float. That has to land on the same answer as the
    engine's integers, and "the same" here means within one level of 16-bit
    chroma over every pixel of the fixture — the fade above hiY is where a
    misplaced floor would show up, and 1239 pixels are up there.
    """
    data = np.load(FIXTURE)
    terms = ChromaSuppresTerms(int(data["hi_y"]), int(data["lo_y"]),
                               int(data["slope_hi"]), int(data["slope_lo"]))
    tile_in, tile_out = data["tile_in"], data["tile_out"]

    y = tile_in[..., 0].astype(np.int16).astype(np.float64) / Y_FULL_SCALE
    f = chroma_suppres_gain(y, terms)
    assert f.dtype == np.float32

    for plane in (1, 2):
        centred = tile_in[..., plane].astype(np.float64) - CHROMA_CENTRE
        got = centred * f + CHROMA_CENTRE
        assert np.abs(got - tile_out[..., plane].astype(np.float64)).max() <= 1.0

    # Not an identity anywhere: the mid-tones keep 255/256 of their chroma, and
    # that flat 0.39% loss is the part of this stage that is easiest to miss.
    assert chroma_suppres_gain(np.array([0.5], np.float32), terms)[0] == pytest.approx(255 / 256)


def test_the_ycc_section_fades_highlight_chroma_and_leaves_luma_alone() -> None:
    """apply_chroma with `suppress`, which is where the stage joins the render.

    Two things have to hold at once: chroma comes down (that is the stage), and
    luma does not move at all — the engine runs this on the two chroma planes
    only, and Y goes through untouched. Both pixels are chosen to stay inside
    [0, 1] after YCC2RGB, so the clamp there cannot stand in for the effect.

    The gains are the engine's own interpolated values for DSC03015 (VV2), the
    same eight the shader's vitest mirror uses.
    """
    cross = np.array([-0.261719, -0.222656, -0.230469, -0.167969], np.float32)
    gain = np.array([1.078125, 0.632812, 0.929688, 1.09375], np.float32)
    terms = ChromaSuppresTerms(15356, 0, 512, 512)
    # BT.601's own weights, because YCC2RGB is BT.601: dotting the output with
    # these recovers the Y that went in, so it is what "luma did not move" is
    # measured with. RGB2YCC's forward weights are a different set and would
    # pick up a little of the chroma change instead.
    bt601 = np.array([0.299, 0.587, 0.114], np.float32)

    # A near-white pixel whose luma sits above hiY, and a mid-grey-ish one that
    # reaches neither knee.
    bright = np.array([[[0.97, 0.97, 0.85]]], np.float32)
    mid = np.array([[[0.45, 0.40, 0.35]]], np.float32)

    for rgb, want_f in ((bright, None), (mid, 255 / 256)):
        plain = apply_chroma(rgb, cross, gain)
        cut = apply_chroma(rgb, cross, gain, suppress=terms)
        y_plain, y_cut = float((plain @ bt601)[0, 0]), float((cut @ bt601)[0, 0])
        assert y_cut == pytest.approx(y_plain, abs=1e-4)

        f = float(chroma_suppres_gain(np.array([y_plain], np.float32), terms)[0])
        if want_f is not None:
            assert f == pytest.approx(want_f)
        else:
            assert f < 0.9  # the fade is doing real work on this pixel
        # Each channel's distance from luma *is* its chroma, scaled by BT.601's
        # own constants, so the ratio between the two renders is f exactly.
        for chan in range(3):
            d_plain = float(plain[0, 0, chan]) - y_plain
            d_cut = float(cut[0, 0, chan]) - y_cut
            assert d_cut == pytest.approx(d_plain * f, abs=2e-5)


def test_a_file_with_no_sr2_block_suppresses_nothing(tmp_path: Path) -> None:
    """The availability probe, which is what keeps the stage off a DCP render.

    A non-Sony file, a truncated one and a missing one all answer None rather
    than raising — the caller asked what this body suppresses, and "nothing here
    to read" is a real answer. Rendering with invented anchors would fade
    highlight colour the engine leaves alone.
    """
    path = tmp_path / "x.dng"
    path.write_bytes(b"II\x2a\x00 not really a tiff")
    assert chroma_suppres_from_file(path) is None
    assert chroma_suppres_from_file(tmp_path / "missing.arw") is None
    assert chroma_suppres_from_file(Path(__file__)) is None


def test_the_terms_travel_as_plain_ints_on_the_wire() -> None:
    """to_json is the shader's contract: four ints in the engine's own units."""
    payload = chroma_suppres_terms(ANCHORS).to_json()
    assert payload == {"hiY": 15360, "loY": 0, "slopeHi": 512, "slopeLo": 512}
    assert all(isinstance(v, int) for v in payload.values())
