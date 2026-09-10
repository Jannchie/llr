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


def test_amount_follows_iso_and_slider():
    assert M.blend_amount(2000, 5) == 1.0
    assert M.blend_amount(100, 5) == 0.5
    assert abs(M.blend_amount(800, 5) - 0.73333) < 1e-4
    assert abs(M.blend_amount(100, 10) - 0.7) < 1e-6
    assert M.blend_amount(1600, 10) == 1.0


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
