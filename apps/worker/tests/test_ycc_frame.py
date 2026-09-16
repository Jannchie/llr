"""Chroma interpolation for Sony's M/S-size YCbCr frames (sony/ycc_frame.py)."""

from types import SimpleNamespace

import numpy as np
import pytest

from llr_worker.sony import ycc_frame as Y


def libraw_convert(y: np.ndarray, cb: np.ndarray, cr: np.ndarray) -> np.ndarray:
    """sonycc.cpp ycc2rgb on full-resolution planes: float32 BT.601, truncated to uint16."""
    y, cb, cr = (np.asarray(p, np.float32) for p in (y, cb, cr))
    rgb = np.stack([y + np.float32(Y.KR) * cr,
                    y - np.float32(Y.KGB) * cb - np.float32(Y.KGR) * cr,
                    y + np.float32(Y.KB) * cb], -1)
    out = np.zeros((*y.shape, 4), np.uint16)
    out[..., :3] = np.clip(rgb, 0, 65535).astype(np.uint16)
    return out


def replicated(y: np.ndarray, cb_sites: np.ndarray, cr_sites: np.ndarray,
               step_h: int, step_v: int, phase_h: int = 0, phase_v: int = 0) -> np.ndarray:
    """What LibRaw hands rawpy: chroma sites replicated over their step, then converted."""
    h, w = y.shape

    def spread(sites: np.ndarray) -> np.ndarray:
        full = np.repeat(np.repeat(sites, step_v, axis=0), step_h, axis=1)
        out = np.empty((h, w), np.float32)
        out[phase_v:, phase_h:] = full[:h - phase_v, :w - phase_h]
        out[:phase_v, :] = out[phase_v, :]
        out[:, :phase_h] = out[:, phase_h:phase_h + 1]
        return out
    return libraw_convert(y, spread(cb_sites), spread(cr_sites))


def luma_of(rgb: np.ndarray) -> np.ndarray:
    """Y recovered through the inverse of the conversion (independent of the chroma)."""
    r, g, b = (rgb[..., i].astype(np.float64) for i in range(3))
    cr, cb = np.linalg.solve(Y._M, np.stack([r - g, b - g]).reshape(2, -1)).reshape(2, *r.shape)
    return g + Y.KGB * cb + Y.KGR * cr


@pytest.fixture
def edge_422() -> tuple[np.ndarray, np.ndarray]:
    """A 4:2:2 frame with a vertical colour edge at site 4 (pixel column 8): grey to gold."""
    rng = np.random.default_rng(0)
    h, w = 6, 20
    y = rng.integers(4000, 6000, (h, w)).astype(np.float32)
    # Every row gets its own chroma so that rows do not share it by accident
    # (the layout is read off the pixels; a constant plane would look 4:2:0).
    cb = np.repeat(rng.integers(-60, 60, (h, 1)), w // 2, axis=1).astype(np.float32)
    cr = np.repeat(rng.integers(-60, 60, (h, 1)), w // 2, axis=1).astype(np.float32)
    cb[:, 4:] -= 900.0
    cr[:, 4:] += 600.0
    return replicated(y, cb, cr, 2, 1), y


def test_a_libraw_ycc_handle_is_recognised_and_a_mosaic_is_not() -> None:
    image = np.zeros((4, 4, 4), np.uint16)
    ycc = SimpleNamespace(raw_pattern=None, raw_image=image, color_desc=b"RGBG")
    assert Y.is_ycc_frame(ycc)
    mosaic = SimpleNamespace(raw_pattern=np.array([[0, 1], [3, 2]]), raw_image=np.zeros((4, 4), np.uint16),
                             color_desc=b"RGBG")
    assert not Y.is_ycc_frame(mosaic)
    three = SimpleNamespace(raw_pattern=None, raw_image=np.zeros((4, 4, 3), np.uint16), color_desc=b"RGBG")
    assert not Y.is_ycc_frame(three)


def test_the_layout_is_read_off_the_shared_differences() -> None:
    rng = np.random.default_rng(1)
    y = rng.integers(3000, 9000, (12, 16)).astype(np.float32)
    cb = rng.integers(-2000, 2000, (12, 8)).astype(np.float32)
    cr = rng.integers(-2000, 2000, (12, 8)).astype(np.float32)
    assert Y.detect_subsampling(replicated(y, cb, cr, 2, 1)) == ((2, 1), (0, 0))
    cb2 = rng.integers(-2000, 2000, (6, 8)).astype(np.float32)
    cr2 = rng.integers(-2000, 2000, (6, 8)).astype(np.float32)
    assert Y.detect_subsampling(replicated(y, cb2, cr2, 2, 2)) == ((2, 2), (0, 0))
    # Pairs starting on an odd column.
    assert Y.detect_subsampling(replicated(y, cb, cr, 2, 1, phase_h=1)) == ((2, 1), (1, 0))
    # Full-resolution chroma: nothing to do.
    full = libraw_convert(y, rng.integers(-2000, 2000, (12, 16)), rng.integers(-2000, 2000, (12, 16)))
    assert Y.detect_subsampling(full) is None
    assert Y.interpolate_chroma(full.copy()) is None


def test_flat_chroma_is_left_bit_identical_and_the_edge_becomes_a_ramp(edge_422) -> None:
    rgb, _ = edge_422
    before = rgb.copy()
    assert Y.interpolate_chroma(rgb) == (2, 1)
    # Away from the edge nothing moves: the delta formulation reproduces
    # LibRaw's truncated pixels exactly.
    np.testing.assert_array_equal(rgb[:, :6], before[:, :6])
    np.testing.assert_array_equal(rgb[:, 10:], before[:, 10:])
    # Across the edge the shared chroma became the 3/4 : 1/4 triangle ramp.
    r, g = rgb[..., 0].astype(np.float64), rgb[..., 1].astype(np.float64)
    d_before = (before[..., 0].astype(np.float64) - before[..., 1])[0]
    d_after = (r - g)[0]
    lo, hi = d_before[5], d_before[10]
    assert d_after[6] == pytest.approx(lo, abs=1.5)
    assert d_after[7] == pytest.approx(0.75 * lo + 0.25 * hi, abs=1.5)
    assert d_after[8] == pytest.approx(0.25 * lo + 0.75 * hi, abs=1.5)
    assert d_after[9] == pytest.approx(hi, abs=1.5)
    # The neighbouring differences no longer come in pairs.
    assert Y.detect_subsampling(rgb) is None


def test_luma_survives_the_interpolation(edge_422) -> None:
    rgb, y = edge_422
    before = luma_of(rgb)
    Y.interpolate_chroma(rgb)
    np.testing.assert_allclose(luma_of(rgb), before, atol=0.51)
    np.testing.assert_allclose(before, y, atol=1.0)


def test_420_interpolates_both_axes() -> None:
    rng = np.random.default_rng(2)
    h, w = 12, 12
    y = rng.integers(4000, 6000, (h, w)).astype(np.float32)
    cb = np.full((h // 2, w // 2), -300.0, np.float32)
    cr = np.full((h // 2, w // 2), 200.0, np.float32)
    cr[3:, :] += 800.0      # a horizontal colour edge between pixel rows 5 and 6
    rgb = replicated(y, cb, cr, 2, 2)
    before = rgb.copy()
    assert Y.interpolate_chroma(rgb) == (2, 2)
    # Uniform along the rows, so only the two rows either side of the edge move.
    np.testing.assert_array_equal(rgb[:5], before[:5])
    np.testing.assert_array_equal(rgb[7:], before[7:])
    d = (rgb[..., 0].astype(np.float64) - rgb[..., 1])[:, 3]
    lo, hi = d[3], d[8]
    assert d[5] == pytest.approx(0.75 * lo + 0.25 * hi, abs=1.5)
    assert d[6] == pytest.approx(0.25 * lo + 0.75 * hi, abs=1.5)


def test_clipped_pixels_are_not_trusted_and_not_changed(edge_422) -> None:
    rgb, _ = edge_422
    # A pixel LibRaw clipped to 0 breaks the linear model; it stays as it is,
    # and its partner's chroma alone stands for the pair.
    rgb[2, 7, 0] = 0
    rgb[3, 8, :3] = 0
    before = rgb.copy()
    Y.interpolate_chroma(rgb)
    np.testing.assert_array_equal(rgb[2, 7], before[2, 7])
    np.testing.assert_array_equal(rgb[3, 8], before[3, 8])
    # The untouched rows still interpolate as before.
    assert rgb[0, 7, 0] != before[0, 7, 0]


def test_the_handle_wrapper_reports_what_it_did(edge_422) -> None:
    rgb, _ = edge_422
    raw = SimpleNamespace(raw_pattern=None, raw_image=rgb, color_desc=b"RGBG")
    assert Y.interpolate_chroma_inplace(raw) == "chroma 4:2:2 interpolated"
    assert Y.interpolate_chroma_inplace(raw) == "chroma already full resolution"
    mosaic = SimpleNamespace(raw_pattern=np.array([[0, 1], [3, 2]]), raw_image=np.zeros((4, 4), np.uint16),
                             color_desc=b"RGBG")
    assert Y.interpolate_chroma_inplace(mosaic) is None
