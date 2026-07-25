"""Tests for sony_repro.dro (structural DRO reproduction)."""

import numpy as np
import pytest

from sony_repro.dro import DROParams, apply_dro

LUMA = (0.2126, 0.7152, 0.0722)


def _lum(img):
    return LUMA[0] * img[..., 0] + LUMA[1] * img[..., 1] + LUMA[2] * img[..., 2]


def _make_split_image(h=128, w=128, dark=0.08, bright=0.85):
    """左半暗、右半亮的分块图。"""
    img = np.zeros((h, w, 3), dtype=np.float64)
    img[:, : w // 2, :] = dark
    img[:, w // 2 :, :] = bright
    return img


def _dark_mask(w):
    m = np.zeros((w,), dtype=bool)
    m[: w // 2] = True
    return m


def test_off_is_identity():
    img = _make_split_image()
    out = apply_dro(img, DROParams(mode="off"))
    assert np.max(np.abs(out - img)) < 1e-6


def test_dark_region_brightened():
    img = _make_split_image()
    out = apply_dro(img, DROParams(mode="lv3"))
    w = img.shape[1]
    dm = _dark_mask(w)
    before = _lum(img)[:, dm].mean()
    after = _lum(out)[:, dm].mean()
    assert after > before + 0.01  # 明显提亮


def test_bright_region_protected():
    img = _make_split_image()
    out = apply_dro(img, DROParams(mode="lv3"))
    w = img.shape[1]
    bm = ~_dark_mask(w)
    before = _lum(img)[:, bm].mean()
    after = _lum(out)[:, bm].mean()
    # 亮区应基本保持,不被明显压暗/提亮过头
    assert abs(after - before) < 0.15
    assert after <= before + 1e-6  # 不应把亮区继续推亮


def test_intensity_monotonic():
    img = _make_split_image()
    w = img.shape[1]
    dm = _dark_mask(w)
    before = _lum(img)[:, dm].mean()
    lifts = []
    for lv in ["lv1", "lv2", "lv3", "lv4", "lv5"]:
        out = apply_dro(img, DROParams(mode=lv))
        lifts.append(_lum(out)[:, dm].mean() - before)
    for a, b in zip(lifts, lifts[1:]):
        assert b >= a - 1e-9  # 单调不减


def test_no_nan_inf_and_clipped():
    rng = np.random.default_rng(0)
    img = rng.random((64, 96, 3))
    for mode in ["off", "auto", "lv1", "lv3", "lv5"]:
        out = apply_dro(img, DROParams(mode=mode))
        assert np.all(np.isfinite(out))
        assert out.min() >= 0.0 - 1e-9
        assert out.max() <= 1.0 + 1e-9


def test_color_preserved():
    # 恒定色相纯色块(未接近 clip),处理后 R:G:B 比例基本不变。
    h = w = 96
    img = np.empty((h, w, 3), dtype=np.float64)
    img[..., 0] = 0.30
    img[..., 1] = 0.15
    img[..., 2] = 0.05
    out = apply_dro(img, DROParams(mode="lv3"))
    ratio_in = np.array([0.30, 0.15, 0.05]) / 0.30
    px = out[h // 2, w // 2]
    ratio_out = px / px[0]
    assert np.max(np.abs(ratio_in - ratio_out)) < 1e-3


def test_locality_preserved():
    # 暗背景上的亮小块:处理后小块相对背景仍更亮(局部对比未被抹平)。
    h = w = 128
    img = np.full((h, w, 3), 0.06, dtype=np.float64)
    img[h // 2 - 6 : h // 2 + 6, w // 2 - 6 : w // 2 + 6, :] = 0.6
    out = apply_dro(img, DROParams(mode="lv4"))
    patch = out[h // 2 - 6 : h // 2 + 6, w // 2 - 6 : w // 2 + 6]
    bg = out[:12, :12]
    assert _lum(patch).mean() > _lum(bg).mean() + 0.1


def test_auto_mode_runs():
    img = _make_split_image(dark=0.05, bright=0.9)
    out = apply_dro(img, DROParams(mode="auto"))
    assert np.all(np.isfinite(out))
    w = img.shape[1]
    dm = _dark_mask(w)
    assert _lum(out)[:, dm].mean() > _lum(img)[:, dm].mean()


def test_invalid_mode_raises():
    img = _make_split_image()
    with pytest.raises(ValueError):
        apply_dro(img, DROParams(mode="bogus"))
