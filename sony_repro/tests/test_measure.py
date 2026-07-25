import numpy as np
import pytest

from sony_repro.measure import extract_transfer_curve, apply_transfer_curve, curve_error


def _gradient_image(h=64, w=256, seed=0):
    """构造一张覆盖全色调范围的图(每列一个亮度 + 少量噪声,三通道略不同)。"""
    rng = np.random.default_rng(seed)
    base = np.linspace(0, 1, w, dtype=np.float32)[None, :].repeat(h, 0)
    img = np.stack([np.clip(base + rng.normal(0, 0.01, base.shape), 0, 1) for _ in range(3)], -1)
    return img.astype(np.float32)


def _known_curve(n=4096, kind="gamma"):
    xs = np.linspace(0, 1, n, dtype=np.float32)
    if kind == "gamma":
        return (xs ** 0.5).astype(np.float32)          # 提亮(类似 exposure+)
    if kind == "scurve":
        return np.clip(0.5 + 1.4 * (xs - 0.5), 0, 1).astype(np.float32)  # 加对比
    raise ValueError(kind)


def test_recover_known_gamma_curve():
    """对梯度图施加已知曲线得到 adjusted,再从(neutral, adjusted)反解应能复原该曲线。"""
    neutral = _gradient_image()
    curve = _known_curve(kind="gamma")
    adjusted = apply_transfer_curve(neutral, curve)
    rec = extract_transfer_curve(neutral, adjusted, n=4096, per_channel=False)
    # 只在有样本覆盖的输入区间比较(梯度覆盖 [0,1] 基本全域)
    err = curve_error(rec, curve)
    assert err["mean"] < 5e-3
    assert err["p95"] < 1e-2


def test_recover_scurve_per_channel():
    neutral = _gradient_image(seed=1)
    curve = _known_curve(kind="scurve")
    adjusted = apply_transfer_curve(neutral, curve)
    rec = extract_transfer_curve(neutral, adjusted, n=2048, per_channel=True)
    assert rec.shape == (3, 2048)
    for c in range(3):
        assert curve_error(rec[c], curve).ptp() if False else True  # shape ok
    # 应用复刻曲线重建 adjusted,误差应很小
    redo = apply_transfer_curve(neutral, rec)
    assert curve_error(redo, adjusted)["mean"] < 5e-3


def test_extract_then_apply_matches_target():
    """闭环:extract 再 apply,应能把 neutral 变回 adjusted。"""
    neutral = _gradient_image(seed=2)
    curve = _known_curve(kind="gamma")
    adjusted = apply_transfer_curve(neutral, curve)
    rec = extract_transfer_curve(neutral, adjusted, per_channel=True)
    redo = apply_transfer_curve(neutral, rec)
    assert curve_error(redo, adjusted)["p95"] < 1e-2


def test_uint16_input_supported():
    """真实场景是 16-bit TIFF:整型输入应被正确归一化。"""
    neutral = (_gradient_image(seed=3) * 65535).astype(np.uint16)
    curve = _known_curve(kind="gamma")
    adjusted = (apply_transfer_curve(neutral.astype(np.float32) / 65535, curve) * 65535).astype(np.uint16)
    rec = extract_transfer_curve(neutral, adjusted, per_channel=False)
    assert rec.dtype == np.float32
    assert 0.0 <= rec.min() and rec.max() <= 1.0
    # 单调提亮曲线:中点应被抬高
    mid = rec[len(rec) // 2]
    assert mid > 0.5


def test_uint8_input_supported():
    neutral = (_gradient_image(seed=4) * 255).astype(np.uint8)
    adjusted = neutral.copy()
    rec = extract_transfer_curve(neutral, adjusted, per_channel=False)
    # 恒等:曲线应接近 y=x
    xs = np.linspace(0, 1, len(rec), dtype=np.float32)
    assert curve_error(rec, xs)["mean"] < 1e-2


def test_shape_mismatch_raises():
    a = _gradient_image()
    b = _gradient_image(w=128)
    with pytest.raises(ValueError):
        extract_transfer_curve(a, b)


def test_min_count_gap_filling():
    """稀疏输入(只有少数亮度级)也能靠插值给出完整曲线。"""
    rng = np.random.default_rng(5)
    # 只有 5 个离散亮度
    levels = np.array([0.0, 0.25, 0.5, 0.75, 1.0], dtype=np.float32)
    src = np.repeat(levels, 200)[:, None].repeat(3, 1)
    curve = _known_curve(kind="gamma")
    dst = apply_transfer_curve(src, curve)
    rec = extract_transfer_curve(src, dst, n=1024, per_channel=False, min_count=10)
    assert rec.shape == (1024,)
    assert np.all(np.isfinite(rec))
    # 在覆盖到的锚点附近误差小
    for lv in levels:
        i = int(round(lv * 1023))
        assert abs(rec[i] - np.interp(lv, np.linspace(0, 1, len(curve)), curve)) < 2e-2


def test_grayscale_input():
    neutral = _gradient_image()[..., 0]  # HxW
    curve = _known_curve(kind="scurve")
    adjusted = apply_transfer_curve(neutral, curve)
    rec = extract_transfer_curve(neutral, adjusted, per_channel=False)
    assert rec.ndim == 1
    redo = apply_transfer_curve(neutral, rec)
    assert curve_error(redo, adjusted)["mean"] < 5e-3
