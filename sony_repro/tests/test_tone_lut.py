"""tone_lut 的 pytest 测试:覆盖 MainGamma 应用语义与构造/序列化 API。"""

import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from sony_repro.tone_lut import ToneLUT  # noqa: E402


# ----------------------------------------------------------------------
# 恒等 / 线性 LUT
# ----------------------------------------------------------------------
def test_identity_roundtrips_integer_inputs():
    lut = ToneLUT.identity(256)
    v = np.arange(256, dtype=np.float32)
    out = lut.apply(v, exact=True)
    assert out.dtype == np.int16
    assert np.array_equal(out, v.astype(np.int16))


def test_identity_out_max_linear_mapping():
    n = 5
    lut = ToneLUT.identity(n, out_max=100)
    # entries[i] = round(i/(n-1)*100) = 0,25,50,75,100
    assert np.array_equal(lut.entries, np.array([0, 25, 50, 75, 100], dtype=np.int16))


def test_linear_lut_expected_values():
    # LUT[i] = 2*i
    entries = (np.arange(10) * 2).astype(np.int16)
    lut = ToneLUT(entries)
    v = np.array([0, 3, 9], dtype=np.float32)
    out = lut.apply(v, exact=False)
    assert np.allclose(out, [0, 6, 18])


# ----------------------------------------------------------------------
# 半整数插值 + 截断
# ----------------------------------------------------------------------
def test_half_integer_float_mode_is_exact_average():
    entries = np.array([0, 3, 10, 21], dtype=np.int16)
    lut = ToneLUT(entries)
    v = np.array([0.5, 1.5, 2.5], dtype=np.float32)
    out = lut.apply(v, exact=False)
    # 相邻两项平均
    assert np.allclose(out, [1.5, 6.5, 15.5])


def test_half_integer_exact_mode_truncates_toward_zero():
    entries = np.array([0, 3, 10, 21], dtype=np.int16)
    lut = ToneLUT(entries)
    v = np.array([0.5, 1.5, 2.5], dtype=np.float32)
    out = lut.apply(v, exact=True)
    # 1.5->1, 6.5->6, 15.5->15 (向零截断)
    assert out.dtype == np.int16
    assert np.array_equal(out, np.array([1, 6, 15], dtype=np.int16))


def test_truncation_toward_zero_with_negative_entries():
    # 负输出值:向零截断意味着朝 0 走 (-1.5 -> -1)
    entries = np.array([-3, 0, 3], dtype=np.int16)
    lut = ToneLUT(entries)
    v = np.array([0.5], dtype=np.float32)  # avg(-3,0) = -1.5
    out = lut.apply(v, exact=True)
    assert out[0] == -1  # 向零截断,不是 floor(-2)


# ----------------------------------------------------------------------
# 越界 clamp
# ----------------------------------------------------------------------
def test_out_of_range_clamps():
    entries = np.array([10, 20, 30, 40], dtype=np.int16)
    lut = ToneLUT(entries)
    v = np.array([-5.0, 0.0, 3.0, 100.0], dtype=np.float32)
    out = lut.apply(v, exact=False)
    # -5 -> clamp 到 0 -> 10;100 -> clamp 到 3 -> 40
    assert np.allclose(out, [10, 10, 40, 40])


def test_idx_clamped_at_top_for_interpolation():
    entries = np.array([0, 10, 20, 30], dtype=np.int16)
    lut = ToneLUT(entries)
    # v 恰好为 n-1,idx 会被 clamp 到 n-2,frac 变 1,取最后一项
    out = lut.apply(np.array([3.0], dtype=np.float32), exact=False)
    assert np.allclose(out, [30])


# ----------------------------------------------------------------------
# from_ramp
# ----------------------------------------------------------------------
def test_from_ramp_dense_is_identity_to_lut():
    known = (np.arange(64) * 3).astype(np.int16)
    idx = np.arange(64)
    lut = ToneLUT.from_ramp(idx, known.astype(np.float64), n=64)
    assert np.array_equal(lut.entries, known)


def test_from_ramp_recovers_known_lut_from_sparse():
    n = 128
    known = np.round(np.linspace(0, 255, n)).astype(np.int16)
    # 稀疏采样 known 的一部分下标(含端点 n-1,避免外插的平台效应)
    sample_idx = np.unique(np.append(np.arange(0, n, 8), n - 1))
    sample_vals = known[sample_idx].astype(np.float64)
    lut = ToneLUT.from_ramp(sample_idx, sample_vals, n=n)
    # 线性 ramp 下,线性插值应能几乎完美复原
    assert np.max(np.abs(lut.entries.astype(int) - known.astype(int))) <= 1


def test_from_ramp_requires_sorted_input():
    with pytest.raises(ValueError):
        ToneLUT.from_ramp(np.array([2, 1, 0]), np.array([0, 1, 2]), n=3)


# ----------------------------------------------------------------------
# apply_image
# ----------------------------------------------------------------------
def test_apply_image_three_channels_shared_lut():
    lut = ToneLUT.identity(256)
    img = np.random.randint(0, 256, size=(4, 5, 3)).astype(np.float32)
    out = lut.apply_image(img, exact=True)
    assert out.shape == img.shape
    assert out.dtype == np.int16
    assert np.array_equal(out, img.astype(np.int16))


def test_apply_image_per_channel_luts_independent():
    n = 256
    # 每通道不同的线性增益
    lut_r = ToneLUT(np.clip(np.arange(n) * 1, 0, 32767).astype(np.int16))
    lut_g = ToneLUT.identity(n, out_max=None)
    lut_b = ToneLUT(np.zeros(n, dtype=np.int16))  # 全黑
    img = np.full((2, 2, 3), 100.0, dtype=np.float32)
    out = lut_r.apply_image(img, exact=True, luts=[lut_r, lut_g, lut_b])
    assert np.all(out[..., 0] == 100)
    assert np.all(out[..., 1] == 100)
    assert np.all(out[..., 2] == 0)


def test_apply_image_rejects_non_hwc():
    lut = ToneLUT.identity(16)
    with pytest.raises(ValueError):
        lut.apply_image(np.zeros((4, 4), dtype=np.float32))


# ----------------------------------------------------------------------
# save / load
# ----------------------------------------------------------------------
def test_save_load_roundtrip(tmp_path):
    entries = np.round(np.linspace(0, 4095, 256)).astype(np.int16)
    lut = ToneLUT(entries)
    p = tmp_path / "lut.npz"
    lut.save(str(p))
    loaded = ToneLUT.load(str(p))
    assert np.array_equal(loaded.entries, lut.entries)
    assert loaded.entries.dtype == np.int16


# ----------------------------------------------------------------------
# to_cube
# ----------------------------------------------------------------------
def test_to_cube_header_and_rows(tmp_path):
    n = 64
    lut = ToneLUT.identity(n, out_max=1000)
    p = tmp_path / "lut.cube"
    lut.to_cube(str(p))
    text = p.read_text(encoding="utf-8")
    lines = [ln for ln in text.splitlines() if ln.strip()]

    # 头部尺寸正确
    size_lines = [ln for ln in lines if ln.startswith("LUT_1D_SIZE")]
    assert len(size_lines) == 1
    assert int(size_lines[0].split()[1]) == n

    # 数据行数正确
    data_lines = [ln for ln in lines if not ln.startswith("#") and not ln.startswith("LUT_1D_SIZE")]
    assert len(data_lines) == n

    # 值在 [0,1]
    vals = np.array([[float(x) for x in ln.split()] for ln in data_lines])
    assert vals.shape == (n, 3)
    assert vals.min() >= 0.0
    assert vals.max() <= 1.0
    # 归一化后首行 0、末行 1
    assert vals[0, 0] == pytest.approx(0.0)
    assert vals[-1, 0] == pytest.approx(1.0)


def test_to_cube_constant_lut_all_zero(tmp_path):
    lut = ToneLUT(np.full(8, 5, dtype=np.int16))
    p = tmp_path / "const.cube"
    lut.to_cube(str(p))
    text = p.read_text(encoding="utf-8")
    data_lines = [ln for ln in text.splitlines() if ln.strip() and not ln.startswith("#") and not ln.startswith("LUT_1D_SIZE")]
    vals = np.array([[float(x) for x in ln.split()] for ln in data_lines])
    assert np.all(vals == 0.0)


# ----------------------------------------------------------------------
# 构造健壮性
# ----------------------------------------------------------------------
def test_rejects_too_short_lut():
    with pytest.raises(ValueError):
        ToneLUT(np.array([5], dtype=np.int16))


def test_rejects_2d_entries():
    with pytest.raises(ValueError):
        ToneLUT(np.zeros((4, 4), dtype=np.int16))
