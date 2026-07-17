"""Bayer pack/unpack plumbing and the CFA-support guard."""

from types import SimpleNamespace

import numpy as np
import pytest

from llr_worker.denoise import (
    PassthroughDenoiser,
    WaveletDenoiser,
    _plane_black_levels,
    cfa_is_bayer_2x2,
    denoise_raw_inplace,
    get_denoiser,
    pack_bayer,
    unpack_bayer,
)

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


def test_get_denoiser_rejects_unknown_model() -> None:
    with pytest.raises(ValueError, match="unknown denoise model"):
        get_denoiser("does-not-exist")
    assert get_denoiser("passthrough") is get_denoiser("passthrough")
