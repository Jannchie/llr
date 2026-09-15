"""ZcTaskSIMDMarble's chroma cleanup (sony/marble.py).

Decoded from Imaging Edge and verified 100.0000% bit-exact against the
engine's own buffers at export, step by step and end to end (notes in
sony_repro/notes/measured-chroma-gap.md 2.27, reference modules in
sony_repro/tools/marble_ref/). These tests pin the port to a crop of that
tile: the engine's input RGB in, the engine's output RGB out, with the luma the
engine had after its Clarity pass supplied so only the chroma path is tested.
"""

from pathlib import Path

import numpy as np
import pytest

from llr_worker.sony import marble as M

FIXTURE = Path(__file__).parent / "fixtures" / "marble_tile.npz"


@pytest.fixture(scope="module")
def tile():
    if not FIXTURE.exists():
        pytest.skip("marble_tile.npz not present")
    return np.load(FIXTURE)


def test_gamut_luts_match_the_engine_generator():
    luts = M.gamut_luts()
    # sRGB EOTF table: first non-zero at index 13; x^(256/563) encode ends at 16383.
    assert luts.dec_srgb[12] == 0 and luts.dec_srgb[13] == 1
    assert luts.enc_pow[0x3FFF] == 16383
    assert luts.dec_pow[0x3FFF] == 16381


def test_chroma_path_is_bit_exact_on_the_engine_tile(tile):
    inp, ref, y_eng = tile["in"], tile["out"], tile["y_after_clarity"]
    params = M.slider_params(M.CALIB_7CM2, 5)
    rw, gw, bw = M.gamut_fwd(inp[..., 0], inp[..., 1], inp[..., 2])
    y, c1, c2 = M.rgb_to_ycc(rw, gw, bw)
    c1n, c2n = M.marble_ycc_planes(y, c1, c2, params)
    ro, go, bo = M.ycc_to_rgb(y_eng, c1n, c2n)  # amount 1.0 at ISO 2000
    ro, go, bo = M.gamut_inv(ro, go, bo)
    m = slice(32, -32)
    for ours, k in ((ro, 0), (go, 1), (bo, 2)):
        d = np.abs(ours[m, m].astype(np.int32) - ref[m, m, k].astype(np.int32))
        assert d.max() == 0, f"plane {k}: {int((d > 0).sum())} mismatches, max {int(d.max())}"


def test_export_amount_is_one_and_the_ramp_is_the_other_path():
    """Captured at ISO 320 in auto and manual NR: the engine's blend is 1.0
    (highiso-denoise-gap.md 9.3), the mode-3 path of 0x140395dd0. The ISO
    ramp is the other path, kept for reference."""
    assert M.blend_amount(2000, 5) == 1.0
    assert M.blend_amount(100, 5) == 1.0
    assert M.blend_amount(320, 0) == 1.0
    assert M.marble_block(320)["amountAuto"] == 1.0
    assert M.blend_amount_ramp(2000, 5) == 1.0
    assert M.blend_amount_ramp(100, 5) == 0.5
    assert abs(M.blend_amount_ramp(800, 5) - 0.73333) < 1e-4
    assert abs(M.blend_amount_ramp(100, 10) - 0.7) < 1e-6
    assert M.blend_amount_ramp(1600, 10) == 1.0


def test_calibration_comes_from_the_shot_sr2_tags():
    """The block the engine hands cnr2 is the camera's, SR2 tags 0x794a..0x795e
    copied in (highiso-denoise-gap.md 9): the ISO 25600 frame DSC03692 carries
    -7/2222/184/487/640 and the factor-8 flag, the ISO 2000 frame DSC03036
    -4/982/158/187/652 and factor 4. Words are sign-extended whatever the TIFF
    type, and a partial set is no set."""
    tags = dict(zip(M.MARBLE_SR2_TAGS, [1, 8192, 0xFFF9, 2222, 0xFFFF, 184, -1, 487, 640, 640, 128, 128,
                                        256, 256, 1280, 30, 40, 30, 40, 205, 1], strict=True))
    c = M.calib_from_tags(tags)
    assert (c["p34"], c["p38"], c["p3c"], c["p40"], c["p44"], c["p48"], c["base_4c"]) == (-7, 2222, -1, 184, -1, 487, 640)
    assert c["factor"] == 8 and c["enabled"] is True and (c["x5c"], c["x60"], c["x64"]) == (256, 256, 1280)
    tags[0x795E] = 0
    assert M.calib_from_tags(tags)["factor"] == 4
    del tags[0x7951]
    assert M.calib_from_tags(tags) is None
    # and the wire form carries the shot's thresholds and factor, not a fixed block
    block = M.marble_block(25600, c)
    assert block["calib"]["p38"] == 2222 and block["calib"]["factor"] == 8
    assert set(block["calib"]) == set(M.CALIB_7CM2)


def test_iso_table_stands_in_without_the_tags():
    """The fallback follows the values 31 ILCE-7CM2 frames carried: the rows
    verbatim, log-ISO interpolation between, the ends held, factor 8 from the
    first ISO that wrote it."""
    lo, hi = M.calib_for_iso(2000), M.calib_for_iso(25600)
    assert (lo["p34"], lo["p38"], lo["p40"], lo["p48"], lo["base_4c"], lo["factor"]) == (-4, 982, 158, 187, 652, 4)
    assert (hi["p34"], hi["p38"], hi["p40"], hi["p48"], hi["base_4c"], hi["factor"]) == (-7, 2222, 184, 487, 640, 8)
    assert M.calib_for_iso(100)["p38"] == 831 and M.calib_for_iso(50) == M.calib_for_iso(100)
    assert M.calib_for_iso(102400) == hi
    mid = M.calib_for_iso(3200)
    assert 1159 < mid["p38"] < 1314 and mid["factor"] == 4
    assert M.calib_for_iso(6400)["factor"] == 8
    for key in ("p30", "lo1", "hi1", "strength", "base_54"):
        assert lo[key] == hi[key] == M.CALIB_7CM2[key]
    assert 831 <= M.marble_block(400)["calib"]["p38"] <= 840   # between the ISO 320 and 800 rows


def test_upsample_weights_are_the_engine_tables():
    """Edit.exe .data 0x140559640 (factor 4) and 0x1405596c0 (factor 8), read
    from the binary: four taps summing to 128 at centred phases."""
    a0, a1, b1, b0 = M.bilinear_weights(4)
    assert a0.tolist() == [84, 28, 60, 20, 36, 12, 12, 4]
    assert b0.tolist() == [12, 4, 36, 12, 60, 20, 84, 28]
    a0, a1, b1, b0 = M.bilinear_weights(8)
    assert a0.tolist()[:8] == [105, 75, 45, 15, 91, 65, 39, 13]
    assert a1.tolist()[:4] == [15, 45, 75, 105] and b1.tolist()[-4:] == [15, 45, 75, 105]
    assert b0.tolist()[:4] == [7, 5, 3, 1]
    assert ((a0 + a1 + b1 + b0) == 128).all()


FIXTURE_F8 = Path(__file__).parent / "fixtures" / "marble_tile_f8.npz"


def test_factor_8_chroma_path_is_bit_exact_on_the_iso_25600_tile():
    """The ISO 25600 tile (DSC03692) runs the engine's factor-8 helpers
    (cnr1_c070 / cnr4_fe70) on the shot's own tags; the port reproduces its
    output exactly (sony_repro/tools/marble_ref/verify_capture.py, every helper
    100.0000%)."""
    if not FIXTURE_F8.exists():
        pytest.skip("marble_tile_f8.npz not present")
    tile = np.load(FIXTURE_F8)
    inp, ref, y_eng = tile["in"], tile["out"], tile["y_after_clarity"]
    calib = M.calib_from_tags(dict(zip(M.MARBLE_SR2_TAGS, [1, 8192, -7, 2222, -1, 184, -1, 487, 640, 640, 128, 128,
                                                           256, 256, 1280, 30, 40, 30, 40, 205, 1], strict=True)))
    params = M.slider_params(calib, 5)
    assert params["factor"] == 8
    rw, gw, bw = M.gamut_fwd(inp[..., 0], inp[..., 1], inp[..., 2])
    y, c1, c2 = M.rgb_to_ycc(rw, gw, bw)
    c1n, c2n = M.marble_ycc_planes(y, c1, c2, params)
    ro, go, bo = M.ycc_to_rgb(y_eng, c1n, c2n)  # amount 1.0 at ISO 25600
    ro, go, bo = M.gamut_inv(ro, go, bo)
    m = slice(48, -48)   # the factor-8 support is twice the factor-4 one; the crop sits on the 8-px grid
    for ours, k in ((ro, 0), (go, 1), (bo, 2)):
        d = np.abs(ours[m, m].astype(np.int32) - ref[m, m, k].astype(np.int32))
        assert d.max() == 0, f"plane {k}: {int((d > 0).sum())} mismatches, max {int(d.max())}"


def test_slider_maps_to_the_engine_thresholds():
    p5, p0, p10 = (M.slider_params(M.CALIB_7CM2, s) for s in (5, 0, 10))
    assert (p5["p4c"], p5["p54"]) == (652, 128)
    assert (p0["p4c"], p0["p54"]) == (391, 179)
    assert (p10["p4c"], p10["p54"]) == (2738, 77)


def test_float_entry_point_leaves_luma_and_removes_fine_chroma():
    rng = np.random.default_rng(0)
    base = np.full((128, 128, 3), 0.4, np.float32)
    noise = rng.normal(0, 0.02, (128, 128, 1)).astype(np.float32)
    rgb = np.clip(base + np.concatenate([noise, -noise, noise * 0.5], -1), 0, 1)
    out = M.apply_marble_chroma_nr(rgb, iso=2000, chroma_slider=5)
    w = np.array([0.299, 0.587, 0.114], np.float32)
    y_in, y_out = rgb @ w, out @ w
    assert np.abs(y_out - y_in)[16:-16, 16:-16].mean() < 2e-3
    rg_in = (rgb[..., 0] - rgb[..., 1])[16:-16, 16:-16]
    rg_out = (out[..., 0] - out[..., 1])[16:-16, 16:-16]
    assert rg_out.std() < 0.2 * rg_in.std()
