"""Tests for the chroma cleanup read off Edit.exe's final stage.

The measured properties these pin come from `ZcTaskSIMDMarble`'s own input and
output at full resolution; see `sony_repro/notes/measured-chroma-gap.md` §2.7.
Where a property was measured, the test asserts it. Where the measurement is
silent -- notably how the engine treats a hard colour edge -- the test
characterises this module's behaviour instead of pretending to match.
"""

from __future__ import annotations

import numpy as np
import pytest

from llr_worker.sony.chromanr import (
    DEFAULT_LEVELS,
    apply_chroma_nr,
    coarse_only,
)

LUMA = np.array([0.299, 0.587, 0.114], dtype=np.float32)


def _luma(rgb: np.ndarray) -> np.ndarray:
    return rgb @ LUMA


def _detail(plane: np.ndarray) -> np.ndarray:
    """The finest scale: the plane minus its own 2x2 box average, upsampled."""
    h, w = plane.shape[0] & ~1, plane.shape[1] & ~1
    p = plane[:h, :w]
    m = (p[0::2, 0::2] + p[1::2, 0::2] + p[0::2, 1::2] + p[1::2, 1::2]) * 0.25
    return p - np.repeat(np.repeat(m, 2, axis=0), 2, axis=1)


def _mad(x: np.ndarray) -> float:
    return float(np.median(np.abs(x - np.median(x))) * 1.4826)


def _noisy_grey(size: int = 128, seed: int = 3) -> np.ndarray:
    """Flat grey with independent per-channel noise, i.e. pure chroma noise."""
    rng = np.random.default_rng(seed)
    base = np.full((size, size, 3), 0.4, dtype=np.float32)
    return base + rng.normal(0.0, 0.01, base.shape).astype(np.float32)


def test_a_constant_image_is_untouched() -> None:
    flat = np.full((64, 64, 3), 0.3, dtype=np.float32)
    flat[..., 0] = 0.5
    out = apply_chroma_nr(flat)
    assert np.allclose(out, flat, atol=1e-5)


def test_zero_levels_round_trips_through_the_colour_transform() -> None:
    """With nothing removed, the YCbCr round trip has to come back where it started.

    The tolerance is 1e-4 rather than float32 epsilon on purpose: the engine's
    coefficients are four-decimal integers (3441, 7141) and are therefore not the
    exact inverse of the Rec.601 forward, so the green channel drifts about 1e-5
    per pass. That drift is the engine's, not ours, and it is the price of using
    its numbers instead of an exactly-invertible pair.
    """
    rng = np.random.default_rng(11)
    img = rng.random((32, 32, 3), dtype=np.float32)
    assert np.allclose(apply_chroma_nr(img, levels=0), img, atol=1e-4)


def test_luma_is_preserved_exactly() -> None:
    """Measured: the engine moves luma about 0.3%, and that belongs to Clarity.

    So the chroma stage must not touch it at all. "Exactly" here means to float32
    round-off, which is what carrying luma through untransformed buys.
    """
    img = _noisy_grey()
    out = apply_chroma_nr(img)
    assert np.allclose(_luma(out), _luma(img), atol=1e-5)


def test_the_fine_chroma_band_is_removed_not_shrunk() -> None:
    """The load-bearing measurement: |r| <= 0.032 between input and output bands.

    A shrinkage would leave the output's fine band a scaled copy of the input's,
    so correlation would stay near 1 whatever the factor. The engine's is
    essentially zero, and so must this be, or it is reproducing the wrong shape.
    """
    img = _noisy_grey(size=256)
    out = apply_chroma_nr(img)
    d_in = _detail(img[..., 0] - img[..., 1]).ravel()
    d_out = _detail(out[..., 0] - out[..., 1]).ravel()
    assert _mad(d_out) < 0.1 * _mad(d_in)
    assert abs(float(np.corrcoef(d_in, d_out)[0, 1])) < 0.2


def test_the_fine_chroma_band_shrinks_by_at_least_ten_times() -> None:
    """Measured range on the engine was 11x to 21x; assert the order, not a value."""
    img = _noisy_grey(size=256)
    out = apply_chroma_nr(img)
    for i, j in ((0, 1), (2, 1)):
        before = _mad(_detail(img[..., i] - img[..., j]))
        after = _mad(_detail(out[..., i] - out[..., j]))
        assert before / max(after, 1e-9) > 10.0, (i, j, before, after)


def test_coarse_colour_structure_survives() -> None:
    """Measured: whole-tile std of the colour difference went 623.7 -> 623.6.

    A patch far wider than the cutoff must come through with its colour intact,
    which is what separates this from desaturation.
    """
    img = np.full((256, 256, 3), 0.4, dtype=np.float32)
    img[64:192, 64:192, 0] = 0.6  # a 128px red patch, well above the cutoff
    out = apply_chroma_nr(img)
    inner = (slice(96, 160), slice(96, 160))  # away from the patch's own edge
    got = out[inner][..., 0] - out[inner][..., 1]
    want = img[inner][..., 0] - img[inner][..., 1]
    assert np.allclose(got, want, atol=2e-3)


def test_more_levels_remove_more() -> None:
    img = _noisy_grey(size=256)
    mads = [
        _mad(_detail(apply_chroma_nr(img, levels=n)[..., 0]
                     - apply_chroma_nr(img, levels=n)[..., 1]))
        for n in (1, 2, 4)
    ]
    assert mads[0] > mads[1] > mads[2]


def test_coarse_only_leaves_a_ramp_alone() -> None:
    """A linear ramp has no fine content, so a scale split must pass it through.

    Catches an expansion filter that shifts the image by half a pixel — a bug
    that a noise test cannot see, because noise has no position to shift.
    """
    ramp = np.tile(np.linspace(0.0, 1.0, 128, dtype=np.float32), (128, 1))
    out = coarse_only(ramp, levels=3)
    assert np.allclose(out[16:-16, 16:-16], ramp[16:-16, 16:-16], atol=2e-3)


@pytest.mark.parametrize("levels", [1, 2, 3, 4, 5])
def test_odd_sizes_keep_their_shape(levels: int) -> None:
    """Halving drops an odd row, so expansion has to pad it back."""
    rng = np.random.default_rng(5)
    img = rng.random((77, 91, 3), dtype=np.float32)
    assert apply_chroma_nr(img, levels=levels).shape == img.shape


def test_the_default_level_count_is_the_calibrated_one() -> None:
    """Pins the sweep's answer so a "rounder" default cannot drift in silently.

    Against Edit's output on three frames the chroma-to-luma ratio came out
    4.45x, 2.33x, 1.08x and 0.44x of Edit's for levels 1..4. Level 3 is the match;
    level 4 overshoots by more than two, which would make llr visibly smoother
    than the thing it is reproducing.
    """
    assert DEFAULT_LEVELS == 3


def test_a_hard_colour_edge_bleeds_and_this_records_how_much() -> None:
    """Characterisation, not a match: the engine's edge behaviour is unmeasured.

    A step contains every scale, so a scale split necessarily softens it. The
    number below is this module's, not Edit's — if a real frame shows colour
    bleeding across hard edges, this is the knob that moved and the measurement
    that has to be made against the engine.
    """
    img = np.full((128, 128, 3), 0.4, dtype=np.float32)
    img[:, 64:, 0] = 0.7
    out = apply_chroma_nr(img, levels=DEFAULT_LEVELS)
    d = out[..., 0] - out[..., 1]
    profile = d.mean(axis=0)
    lo, hi = profile[8], profile[-8]
    span = hi - lo
    # Width of the transition, counted where the profile sits between 10% and 90%.
    inside = np.abs(profile - (lo + 0.5 * span)) < 0.4 * abs(span)
    assert 4 <= int(inside.sum()) <= 64, int(inside.sum())
