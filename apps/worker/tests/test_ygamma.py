"""YGamma's LUT, the half of the stage that was missing (sony/chroma.py).

The pivot/contrast half was verified against the engine long ago; the table in
front of it was not, because every frame in the sample corpus was FL or VV2 and
those looks get a table that really is near identity. Standard and Neutral do
not: theirs has a highlight knee, and leaving it out lifted a Standard frame's
highlights by 2-3% and clipped them.

These tests pin the three things that claim rests on: the integer stage against
the engine's own captured in/out, the per-look selector against a real file, and
the wire shape the browser consumes.

The same three, once more for Edit's 色彩复制 = 高级. That setting was first
taken for the 3-D LUT alone; it also swaps this table and this contrast, and the
two advanced fixtures here are what says so.
"""

from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

from llr_worker.sony import calibration_for, looks_in_file
from llr_worker.sony.chroma import (
    LUMA_CONTRAST_ADVANCED,
    LUMA_CONTRAST_UNIT,
    LUMA_LUT_SIZE,
    LUMA_LUT_WIRE,
    luma_gamma,
    luma_lut,
    luma_terms,
    ygamma_planes,
)
from llr_worker.sony.profile import look_render_info
from llr_worker.sony.sr2 import (
    LUMA_LUT_FLAT,
    LUMA_LUT_KNEE,
    LookCalibration,
    luma_lut_key_for,
)

FIXTURE = Path(__file__).parent / "fixtures" / "ygamma_tile.npz"
#: The same stage under 高级, on Edit's own export tiles: DSC03036 for the knee
#: family and cs_DSC02919 (FL) for the flat one. Both are 128x128 crops taken
#: from inside a tile's valid rect; see each npz's own `source`.
ADVANCED_FIXTURES = {
    LUMA_LUT_KNEE: Path(__file__).parent / "fixtures" / "ygamma_tile_adv3036.npz",
    LUMA_LUT_FLAT: Path(__file__).parent / "fixtures" / "ygamma_tile_advFL.npz",
}


def _calibration(key: tuple[int, int]) -> LookCalibration:
    """A calibration carrying nothing but one YGamma selector."""
    zero = np.zeros(8, dtype=np.int64)
    return LookCalibration(
        name="", param_block=b"", curve_x=zero, curve_y=zero, chroma_base=zero,
        chroma_deltas=np.zeros((4, 8), np.int64), chroma_weights=zero[:4],
        luma_pivot=zero, luma_contrast=zero, chroma_final=None, luma_lut_key=key)


def luma_lut_for_key(key: tuple[int, int], advanced: bool = False) -> np.ndarray:
    """The table for one selector, without a file to read a calibration from.

    Only the selector matters to luma_lut, so the rest of the calibration is
    zeroed rather than faked into something plausible.
    """
    return luma_lut(_calibration(key), advanced)

# DSC03036, an ILCE-7CM2 Standard frame. Not in samples/ — it is the only shot
# on hand that exercises the knee family, so the tests that need it skip rather
# than pretend.
STANDARD_ARW = Path("/mnt/e/temp_photo/DSC03036.ARW")
requires_standard = pytest.mark.skipif(
    not STANDARD_ARW.exists(), reason="DSC03036.ARW (a Standard-look frame) not available")


def test_the_integer_stage_reproduces_the_engine_s_own_tile_bit_for_bit() -> None:
    """What "the table is right" means: the engine's own pixels, exactly.

    The fixture is a 128x128 crop of a tile captured at the export of DSC03036
    (Standard, Fade 0), taken from inside the tile's valid rect. Its Y spans 92
    to 16366 with 5433 pixels above 8192, so both sides of the knee are
    exercised — a stage tested only below 8192 would pass with the table
    deleted, which is exactly the mistake this file exists to catch.
    """
    data = np.load(FIXTURE)
    lut = luma_lut_for_key(tuple(int(v) for v in data["lut_key"]))
    contrast = float(data["contrast"]) / LUMA_CONTRAST_UNIT
    assert contrast == 17280 / 16384  # the shot's Fade 0 entry of tag 0x780e

    y_in = data["tile_in"][..., 0].astype(np.int16)
    got = ygamma_planes(y_in, lut, int(data["pivot"]), contrast)
    assert np.array_equal(got, data["tile_out"][..., 0].astype(np.int16))

    # The crop was chosen for this; assert it so a future re-cut cannot quietly
    # drop the highlight half of the operator.
    assert int((y_in.astype(np.int32) > 8192).sum()) == 5433

    # And the two chroma planes come out of this stage untouched, which is why
    # the whole of YGamma can live on the luma the shader already has.
    for plane in (1, 2):
        assert np.array_equal(data["tile_in"][..., plane], data["tile_out"][..., plane])


def test_the_two_families_are_the_shapes_they_were_dumped_as() -> None:
    """Sample points from both tables, so a bad npz cannot pass silently.

    The knee is the whole difference: identity to Y=8192 for both, then slope
    0.90625 for Standard and Neutral against near identity for the rest.
    """
    knee = luma_lut_for_key(LUMA_LUT_KNEE)
    flat = luma_lut_for_key(LUMA_LUT_FLAT)
    for table in (knee, flat):
        assert table.shape == (LUMA_LUT_SIZE,)
        assert table[8192] == 8192
    assert (knee[12288], knee[16383]) == (11904, 15614)
    assert (flat[12288], flat[16383]) == (12288, 16382)
    # Why the table is 32768 long and not 16384: the engine's Y plane is int16
    # and overshoots full scale at this point, so the lookup has to be defined
    # past 16383. Only the first half can be reached from the shader.
    assert knee[LUMA_LUT_WIRE] > 0 and flat[LUMA_LUT_WIRE] > 0


def test_an_unseen_selector_falls_back_to_the_near_identity_family() -> None:
    """A body writing a pair nobody has dumped still renders, and says so."""
    assert np.array_equal(luma_lut_for_key((1, 2)), luma_lut_for_key(LUMA_LUT_FLAT))


def test_the_float_path_steps_where_the_integer_one_does() -> None:
    """luma_gamma is what the shader mirrors, so it has to agree with the stage.

    Same truncated index, same table, same clip — the only difference is the
    0..1 scale. Checked across the knee, where an interpolating lookup would
    diverge instead.
    """
    knee = luma_lut_for_key(LUMA_LUT_KNEE)
    contrast = 17280 / LUMA_CONTRAST_UNIT
    y16 = np.arange(0, 16384, 7, dtype=np.int16)
    want = ygamma_planes(y16, knee, 0, contrast).astype(np.float64) / 16383.0
    got = luma_gamma(y16.astype(np.float64) / 16383.0, 0.0, contrast, knee)
    # Within one integer step: the float path does not truncate its *output* the
    # way the engine's int16 store does, and nothing downstream of the shader
    # would see the difference.
    assert np.abs(got - want).max() < 1.5 / 16383.0


def test_luma_gamma_without_a_table_is_the_line_it_always_was() -> None:
    """The old two-term form, kept for callers with no calibration to read."""
    y = np.linspace(0.0, 1.0, 33)
    assert np.allclose(luma_gamma(y, 0.0, 1.05), np.clip(y * 1.05, 0.0, 1.0))


def test_the_family_a_look_s_name_implies() -> None:
    """The donor path's fallback: the split is Standard and Neutral, no others."""
    assert luma_lut_key_for("Standard") == LUMA_LUT_KNEE
    assert luma_lut_key_for("Neutral") == LUMA_LUT_KNEE
    for name in ("Vivid", "Portrait", "FL", "VV2", "IN", "SH", "BW", "Sepia", "FL2", "FL3"):
        assert luma_lut_key_for(name) == LUMA_LUT_FLAT, name


@requires_standard
def test_the_selector_splits_a_real_file_s_ten_looks_the_way_the_engine_does() -> None:
    """(0x780c, 0x780d) read per look, on a file that carries both families.

    This is the part that cannot be assumed: the two tags are per-look, so the
    ten SR2DataIFDs of one file disagree on them, and reading the top-level pair
    for every look would give all ten Standard's knee.
    """
    seen = {}
    for code in looks_in_file(STANDARD_ARW):
        cal = calibration_for(STANDARD_ARW, code)
        assert cal is not None
        seen[code] = tuple(int(v) for v in cal.luma_lut_key)
    assert seen["ST"] == LUMA_LUT_KNEE
    assert seen["NT"] == LUMA_LUT_KNEE
    for code in ("VV", "PT", "FL", "VV2", "IN", "SH", "BW", "SE"):
        assert seen[code] == LUMA_LUT_FLAT, code
    # ...and that the selector actually reaches a different table.
    assert luma_lut(calibration_for(STANDARD_ARW, "ST"))[16383] == 15614
    assert luma_lut(calibration_for(STANDARD_ARW, "FL"))[16383] == 16382


@requires_standard
def test_the_profile_carries_the_table_the_browser_indexes() -> None:
    """to_json's wire shape: 16384 ints on the engine's own scale, per look."""
    for code, top in (("ST", 15614), ("FL", 16382)):
        info = look_render_info(calibration_for(STANDARD_ARW, code), code).to_json()
        table = info["profileLumaLut"]
        assert len(table) == LUMA_LUT_WIRE
        assert all(isinstance(v, int) for v in table[:16])
        assert table[16383] == top
        assert table[8192] == 8192


# --- Edit's 色彩复制 = 高级, which is not only the 3-D LUT -----------------


@pytest.mark.parametrize(("key", "family"), [(LUMA_LUT_KNEE, "Standard"),
                                             (LUMA_LUT_FLAT, "FL")])
def test_the_advanced_stage_reproduces_edit_s_own_tile_bit_for_bit(
        key: tuple[int, int], family: str) -> None:
    """What "高级 swaps the table" means: Edit's own pixels.

    Rendering these tiles through the *standard* table misses — which is the
    whole point of the pair. Both frames are Fade 0, so their contrast is the
    table's entry 0, 17280/16384, in either mode (the FL frame's own 0x780e
    reads exactly that too; an earlier note that it read 16384 was wrong).
    """
    data = np.load(ADVANCED_FIXTURES[key])
    assert tuple(int(v) for v in data["lut_key"]) == key, family
    contrast = float(data["contrast"]) / LUMA_CONTRAST_UNIT
    assert contrast == LUMA_CONTRAST_ADVANCED == 17280 / 16384

    y_in = data["tile_in"][..., 0].astype(np.int16)
    want = data["tile_out"][..., 0].astype(np.int16)
    got = ygamma_planes(y_in, luma_lut_for_key(key, advanced=True),
                        int(data["pivot"]), contrast)
    assert np.array_equal(got, want)

    # Both crops straddle Y=8192, where the two families part company, and both
    # reach well into the highlights. A crop that did not would pass with the
    # standard table substituted.
    assert int((y_in.astype(np.int32) > 8192).sum()) > 5000
    assert int(y_in.max()) > 12000

    # And the standard table really would have failed, so this is a measurement
    # rather than a tautology on whichever table happens to be wired up.
    standard = ygamma_planes(y_in, luma_lut_for_key(key), int(data["pivot"]), contrast)
    assert not np.array_equal(standard, want)

    # Chroma still comes out of YGamma untouched under 高级 — the 3-D LUT that
    # follows is a separate stage, and this tile is captured before it.
    for plane in (1, 2):
        assert np.array_equal(data["tile_in"][..., plane], data["tile_out"][..., plane])


def test_the_advanced_tables_are_a_second_pair_and_not_the_first() -> None:
    """The selector still picks the family; `advanced` picks the dump.

    Sample points rather than a whole-array compare, so a swapped npz key shows
    up as a number rather than as "not equal". Note where they differ from the
    standard pair: from Y=1 upward, not only past the knee — 8192 maps to 8176
    in both advanced tables, which is why 高级 cannot be approximated by leaving
    the table alone and changing the contrast.
    """
    knee = luma_lut_for_key(LUMA_LUT_KNEE, advanced=True)
    flat = luma_lut_for_key(LUMA_LUT_FLAT, advanced=True)
    for table in (knee, flat):
        assert table.shape == (LUMA_LUT_SIZE,)
        assert table[8192] == 8176
    assert (knee[12000], knee[16383]) == (11488, 14975)
    assert (flat[12000], flat[16383]) == (11840, 15679)
    assert not np.array_equal(knee, luma_lut_for_key(LUMA_LUT_KNEE))
    assert not np.array_equal(flat, luma_lut_for_key(LUMA_LUT_FLAT))
    # An unseen selector falls back to the same family it does under 标准.
    assert np.array_equal(luma_lut_for_key((1, 2), advanced=True), flat)


# The 0x780e Fade table — the same ten entries in every look of every body read
# so far (α7C II, α7 V); 17280 first, which is where 高级's "constant" came from.
FADE_TABLE = np.array([17280, 15616, 14976, 14208, 13568, 12928, 12160, 11520, 10752, 10112], np.int64)


def test_the_advanced_contrast_keeps_the_shot_s_fade() -> None:
    """Probed on the running engine at export: SH at Fade 6 reads pivot 10624 /
    contrast 12160 in 高级 exactly as in 标准, FL patched to Fade 3 reads 14208
    in both, FL at Fade 0 reads 17280 in both. 高级 does not replace the table;
    the 17280 it was first seen with is entry 0. Replacing it threw the fade
    away (Fade 1 frames 7 L* too dark in the shadows, SH's Fade 6 frames 20 L*).
    """
    cal = replace(_calibration(LUMA_LUT_FLAT),
                  luma_pivot=np.array([0] + [10624] * 9, np.int64), luma_contrast=FADE_TABLE)
    assert luma_terms(cal, 0, advanced=True)[1] == LUMA_CONTRAST_ADVANCED
    for fade, want in ((0, 17280), (30, 14208), (60, 12160), (90, 10112)):
        pivot, contrast = luma_terms(cal, fade)
        pivot_adv, contrast_adv = luma_terms(cal, fade, advanced=True)
        assert contrast == contrast_adv == want / LUMA_CONTRAST_UNIT
        assert pivot_adv == pivot == (0 if fade == 0 else 10624) / 16383.0


def test_the_render_path_swaps_both_halves_on_the_one_flag() -> None:
    """apply_chroma(lut3d=True) is 高级, not "the 3-D LUT bolted on".

    Read off grey, where the luma weights sum to one and every chroma branch
    collapses, so the encoded output is YGamma's own. y = 0.9 lands on index
    14744: the advanced flat table holds 14256 there against 16382 standard,
    and the contrast goes 1.0 -> 1.0546875. The 3-D LUT moves grey too (a
    constant 31 of 16383 on both planes, test_lut3d.py), which is why this
    compares against the model's own answer rather than to the table alone.
    """
    from llr_worker.sony.chroma import apply_chroma, ycc_to_rgb
    from llr_worker.sony.lut3d import apply_lut3d_float

    cross = np.zeros(4, np.float32)
    gain = np.zeros(4, np.float32)
    grey = np.full((1, 1, 3), 0.9, np.float32)
    standard = luma_lut_for_key(LUMA_LUT_FLAT)
    advanced = luma_lut_for_key(LUMA_LUT_FLAT, advanced=True)

    got = apply_chroma(grey, cross, gain, 0.0, 1.0, lut=standard, lut3d=True,
                       lut_advanced=advanced, contrast_advanced=LUMA_CONTRAST_ADVANCED)
    y = np.full((1, 1), advanced[14744] / 16383.0 * LUMA_CONTRAST_ADVANCED)
    zero = np.zeros((1, 1))
    want = ycc_to_rgb(*apply_lut3d_float(y, zero, zero))
    assert np.allclose(got, want, atol=1e-6)

    # Without the pair the caller gets the 3-D LUT alone, on the standard table
    # — the old behaviour, which is what a caller with no calibration wants.
    plain = apply_chroma(grey, cross, gain, 0.0, 1.0, lut=standard, lut3d=True)
    assert not np.allclose(plain, got, atol=2e-3)
    # ...and the 标准 path is untouched by any of this.
    off = apply_chroma(grey, cross, gain, 0.0, 1.0, lut=standard)
    assert np.allclose(off, apply_chroma(grey, cross, gain, 0.0, 1.0, lut=standard,
                                         lut_advanced=advanced,
                                         contrast_advanced=LUMA_CONTRAST_ADVANCED))


@requires_standard
def test_the_profile_carries_both_tables_and_both_contrasts() -> None:
    """The switch is a redraw in the browser, so both answers ride the wire."""
    for code, top, top_adv in (("ST", 15614, 14975), ("FL", 16382, 15679)):
        info = look_render_info(calibration_for(STANDARD_ARW, code), code).to_json()
        table = info["profileLumaLutAdvanced"]
        assert len(table) == LUMA_LUT_WIRE
        assert all(isinstance(v, int) for v in table[:16])
        assert table[16383] == top_adv
        assert table[8192] == 8176
        # ...beside the standard one, which is unchanged.
        assert info["profileLumaLut"][16383] == top
        assert info["profileLumaContrastAdvanced"] == LUMA_CONTRAST_ADVANCED
