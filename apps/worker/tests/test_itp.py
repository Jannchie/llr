"""`sony/itp.py` against the engine and against itself.

The fixture is a 192x192 sub-block of one of Edit.exe's own preview tiles
(DSC02995, ILCE-7CM2, ISO 320): the mosaic that entered `ZcTaskSIMDITP`, the
float32 working plane it made of it, and the three uint16 planes it wrote
(sony_repro/tools/itp_dump_probe.py). `rect` is the sub-block inset by the
engine's 16-pixel halo; results are compared a further 12 pixels in, where
every stage has written.

The tail of the file pins the second obligation: `itp_numba` must be bit-identical
to `itp_numpy`, not merely close. Anything else and the 1-LSB budget above stops
meaning what it says.
"""
from pathlib import Path

import numpy as np
import pytest

from llr_worker.sony import itp, itp_numpy

FIXTURE = Path(__file__).parent / "fixtures" / "itp_tile.npz"
F = np.float32


@pytest.fixture(scope="module")
def tile():
    z = np.load(FIXTURE)
    return {k: z[k] for k in z.files}


def _inner(a, rect, inset=12):
    x0, y0, x1, y1 = rect
    return a[y0 + inset:y1 - inset, x0 + inset:x1 - inset]


def test_convert_reproduces_engine_working_plane(tile):
    gains = itp.wb_gains(tuple(int(v) for v in tile["wb_rggb"]))
    assert list(np.round(gains * 2048).astype(int)) == [1895, 1056, 1056, 2222]
    W = itp.convert(tile["mosaic"], gains, float(tile["black"]))
    np.testing.assert_array_equal(W, tile["W"])


def test_tile_matches_engine_planes(tile):
    rect = tuple(int(v) for v in tile["rect"])
    gains = itp.wb_gains(tuple(int(v) for v in tile["wb_rggb"]))
    got = itp.itp_tile(tile["mosaic"], gains, float(tile["black"]), rect)
    for k, plane in enumerate(got):
        want = tile[f"out{k}"]
        d = np.abs(_inner(plane, rect).astype(int) - _inner(want, rect).astype(int))
        # float32 accumulation order in the two nine-tap FIRs leaves a
        # one-LSB difference on a few pixels per million; anything beyond
        # that is a decoding error.
        assert d.max() <= 1, f"plane {k}: max diff {d.max()}"
        assert (d > 0).sum() <= 3, f"plane {k}: {(d > 0).sum()} pixels differ"


def test_strips_are_seamless(tile):
    mos = tile["mosaic"]
    wb = tuple(int(v) for v in tile["wb_rggb"])
    whole = itp.demosaic(mos, wb, 512.0, strip_rows=10_000, workers=1)
    strips = itp.demosaic(mos, wb, 512.0, strip_rows=40, workers=3)
    np.testing.assert_array_equal(strips, whole)


def test_flat_field_is_reproduced_exactly_per_channel():
    # A flat field with different per-channel levels: every primitive here
    # preserves constants, so the demosaic must return each channel's own level
    # everywhere -- including at the reflect-padded borders, which is what
    # checks that the padding keeps the CFA phase.
    h, w = 70, 90
    mos = np.zeros((h, w), np.uint16)
    mos[0::2, 0::2] = 3000
    mos[0::2, 1::2] = 2000
    mos[1::2, 0::2] = 2000
    mos[1::2, 1::2] = 1000
    wb = (1838, 1024, 1024, 2155)
    out = itp.demosaic(mos, wb, 512.0, strip_rows=32, workers=2)
    gains = itp.wb_gains(wb)
    expect = [np.trunc((3000 - 512) * gains[0]) / 8192,
              np.trunc((2000 - 512) * gains[1]) / 8192,
              np.trunc((1000 - 512) * gains[3]) / 8192]
    for c in range(3):
        np.testing.assert_allclose(out[..., c], expect[c], rtol=0, atol=1.5 / 8192)


def test_output_scale_puts_green_white_near_one():
    # Sensor white on green: 0.515625 * (16383 - 512) = 8183.6, i.e. 0.999 of
    # llr's camera-RGB white (Sony's tone-LUT index 8192).
    mos = np.full((40, 40), 16383, np.uint16)
    out = itp.demosaic(mos, (1024, 1024, 1024, 1024), 512.0, workers=1)
    assert abs(float(out[20, 20, 1]) - 8183 / 8192) < 2e-4


def test_rejects_non_bayer_shape():
    with pytest.raises(ValueError):
        itp.demosaic(np.zeros((4, 4, 3), np.uint16), (1024, 1024, 1024, 1024), 512.0)


# -- the numba backend against the numpy reference --------------------------------

numba_only = pytest.mark.skipif(itp._numba_impl is None, reason="numba not installed")


def _random_mosaics():
    """Odd sizes, full 14-bit range: the strictest check of operation order.

    Every stage's accumulation order shows up here and nowhere else -- a flat or
    smooth field lets a reassociated float32 sum land on the same value.
    """
    rng = np.random.default_rng(20240607)
    for h, w in ((300, 517), (97, 131), (222, 65)):
        yield h, w, rng.integers(0, 16384, size=(h, w), dtype=np.uint16)


@numba_only
def test_numba_tile_is_bit_identical_on_the_fixture(tile):
    rect = tuple(int(v) for v in tile["rect"])
    gains = itp.wb_gains(tuple(int(v) for v in tile["wb_rggb"]))
    want = itp_numpy.itp_tile(tile["mosaic"], gains, float(tile["black"]), rect)
    got = itp.itp_tile(tile["mosaic"], gains, float(tile["black"]), rect)
    for k in range(3):
        np.testing.assert_array_equal(got[k], want[k], err_msg=f"plane {k}")


@numba_only
def test_numba_tile_is_bit_identical_on_random_mosaics():
    gains = itp.wb_gains((2100, 1024, 1024, 1800))
    for h, w, mos in _random_mosaics():
        rect = (16, 16, w - 16, h - 16)
        want = itp_numpy.itp_tile(mos, gains, 512.0, rect)
        got = itp.itp_tile(mos, gains, 512.0, rect)
        for k in range(3):
            np.testing.assert_array_equal(got[k], want[k], err_msg=f"{h}x{w} plane {k}")


@numba_only
def test_numba_demosaic_is_bit_identical_on_random_mosaics():
    wb = (2100, 1024, 1024, 1800)
    for h, w, mos in _random_mosaics():
        want = itp_numpy.demosaic(mos, wb, 512.0, strip_rows=36, workers=3)
        got = itp.demosaic(mos, wb, 512.0, strip_rows=36, workers=3)
        np.testing.assert_array_equal(got, want, err_msg=f"{h}x{w}")


@numba_only
def test_numpy_backend_switch_runs_the_reference(monkeypatch, tile):
    # the switch is a module attribute so it can be flipped for an A/B; with it
    # on "numpy" the numba module must not be reached at all
    monkeypatch.setattr(itp._numba_impl, "itp_tile", _explode)
    monkeypatch.setattr(itp._numba_impl, "strip", _explode)
    monkeypatch.setattr(itp, "BACKEND", "numpy")
    rect = tuple(int(v) for v in tile["rect"])
    gains = itp.wb_gains(tuple(int(v) for v in tile["wb_rggb"]))
    want = itp_numpy.itp_tile(tile["mosaic"], gains, float(tile["black"]), rect)
    for k, plane in enumerate(itp.itp_tile(tile["mosaic"], gains, float(tile["black"]), rect)):
        np.testing.assert_array_equal(plane, want[k])
    mos = tile["mosaic"]
    wb = tuple(int(v) for v in tile["wb_rggb"])
    np.testing.assert_array_equal(itp.demosaic(mos, wb, 512.0, strip_rows=40, workers=2),
                                  itp_numpy.demosaic(mos, wb, 512.0, strip_rows=40, workers=2))


def _explode(*_args, **_kwargs):
    raise AssertionError("the numba kernel ran with LLR_ITP_BACKEND=numpy")
