"""color_profile 的 pytest 测试:矩阵拟合、三线性插值、残差 LUT 拟合、序列化与 .cube 导出。"""

import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from sony_repro.color_profile import (  # noqa: E402
    ColorTransform,
    delta_e_stats,
    fit,
    fit_matrix,
)


def _rng():
    return np.random.default_rng(1234)


# ----------------------------------------------------------------------
# fit_matrix
# ----------------------------------------------------------------------
def test_fit_matrix_identity():
    rng = _rng()
    src = rng.random((500, 3))
    m = fit_matrix(src, src)
    assert np.allclose(m, np.eye(3), atol=1e-8)
    pred = src @ m.T
    assert delta_e_stats(pred, src)["mean"] < 1e-8


def test_fit_matrix_recover_known():
    rng = _rng()
    src = rng.random((1000, 3))
    m_true = rng.random((3, 3))
    tgt = src @ m_true.T
    m = fit_matrix(src, tgt)
    assert np.allclose(m, m_true, atol=1e-8)


def test_fit_matrix_affine():
    rng = _rng()
    src = rng.random((1000, 3))
    m_true = rng.random((3, 3))
    bias = np.array([0.1, -0.2, 0.05])
    tgt = src @ m_true.T + bias
    m = fit_matrix(src, tgt, affine=True)
    assert m.shape == (3, 4)
    assert np.allclose(m[:, :3], m_true, atol=1e-8)
    assert np.allclose(m[:, 3], bias, atol=1e-8)


# ----------------------------------------------------------------------
# 三线性插值
# ----------------------------------------------------------------------
def test_trilinear_hits_nodes_exactly():
    # 用一个随机 LUT,在节点坐标上查表应精确命中节点值。
    rng = _rng()
    s = 5
    lut = rng.random((s, s, s, 3))
    ct = ColorTransform(np.eye(3), lut, (0.0, 1.0))
    axis = np.linspace(0.0, 1.0, s)
    for i in range(s):
        for j in range(s):
            for k in range(s):
                p = np.array([[axis[i], axis[j], axis[k]]])
                out = ct.apply(p)
                assert np.allclose(out[0], lut[i, j, k], atol=1e-9)


def test_trilinear_linear_function():
    # LUT 表达一个已知线性函数 f(r,g,b) = [r, g, b] 的某仿射;中间点应得线性加权结果。
    s = 3
    grid = _identity_grid(s)
    # 线性函数: out = A @ x
    a = np.array([[1.0, 0.5, 0.0],
                  [0.0, 1.0, 0.0],
                  [0.2, 0.0, 1.0]])
    lut = grid @ a.T
    ct = ColorTransform(np.eye(3), lut, (0.0, 1.0))
    rng = _rng()
    pts = rng.random((200, 3))
    out = ct.apply(pts)
    expect = pts @ a.T
    # 三线性插值对全局线性函数精确
    assert delta_e_stats(out, expect)["mean"] < 1e-9


def _identity_grid(s):
    axis = np.linspace(0.0, 1.0, s)
    rr, gg, bb = np.meshgrid(axis, axis, axis, indexing="ij")
    return np.stack([rr, gg, bb], axis=-1)


def test_trilinear_clamp_out_of_domain():
    s = 4
    grid = _identity_grid(s)
    ct = ColorTransform(np.eye(3), grid, (0.0, 1.0))
    pts = np.array([[-0.5, 0.5, 1.5], [2.0, -1.0, 0.3]])
    out = ct.apply(pts)
    expect = np.clip(pts, 0.0, 1.0)
    assert np.allclose(out, expect, atol=1e-9)


# ----------------------------------------------------------------------
# fit:矩阵 + 残差 LUT 显著降低误差
# ----------------------------------------------------------------------
def _synthetic_pairs(n=6000, seed=7):
    rng = np.random.default_rng(seed)
    src = rng.random((n, 3))
    m_true = np.array([[1.1, -0.1, 0.05],
                       [-0.05, 1.05, 0.02],
                       [0.03, -0.02, 0.98]])
    lin = src @ m_true.T
    # 逐通道非线性畸变(gamma 型 + 轻微交叉)
    lin = np.clip(lin, 0.0, 1.0)
    tgt = np.empty_like(lin)
    tgt[:, 0] = np.clip(lin[:, 0] ** 0.8 + 0.03 * lin[:, 1], 0, 1)
    tgt[:, 1] = np.clip(lin[:, 1] ** 1.2, 0, 1)
    tgt[:, 2] = np.clip(np.sqrt(lin[:, 2]) * 0.95, 0, 1)
    return src, tgt


def test_fit_reduces_error():
    src, tgt = _synthetic_pairs()
    m = fit_matrix(src, tgt)
    pred_matrix_only = src @ m.T
    err_matrix = delta_e_stats(pred_matrix_only, tgt)["mean"]

    ct = fit(src, tgt, lut_size=17)
    pred_full = ct.apply(src)
    err_full = delta_e_stats(pred_full, tgt)["mean"]

    assert err_full < err_matrix * 0.25
    assert err_full < 0.01


def test_fit_no_matrix_first():
    src, tgt = _synthetic_pairs()
    ct = fit(src, tgt, lut_size=17, fit_matrix_first=False)
    assert np.allclose(ct.matrix, np.eye(3))
    err = delta_e_stats(ct.apply(src), tgt)["mean"]
    assert err < 0.02


# ----------------------------------------------------------------------
# apply 形状一致性
# ----------------------------------------------------------------------
def test_apply_shape_consistency():
    src, tgt = _synthetic_pairs(n=1200)
    ct = fit(src, tgt, lut_size=9)
    flat = ct.apply(src)  # Nx3
    img = src.reshape(30, 40, 3)
    out_img = ct.apply(img)
    assert out_img.shape == (30, 40, 3)
    assert np.allclose(out_img.reshape(-1, 3), flat, atol=1e-12)


# ----------------------------------------------------------------------
# save / load / to_cube
# ----------------------------------------------------------------------
def test_save_load_roundtrip(tmp_path):
    src, tgt = _synthetic_pairs(n=1500)
    ct = fit(src, tgt, lut_size=11)
    p = tmp_path / "ct.npz"
    ct.save(str(p))
    ct2 = ColorTransform.load(str(p))
    assert np.allclose(ct.matrix, ct2.matrix)
    assert np.allclose(ct.lut3d, ct2.lut3d)
    assert np.allclose(ct.apply(src), ct2.apply(src), atol=1e-12)


def test_save_load_matrix_only(tmp_path):
    rng = _rng()
    m = rng.random((3, 4))
    ct = ColorTransform(m)
    p = tmp_path / "m.npz"
    ct.save(str(p))
    ct2 = ColorTransform.load(str(p))
    assert ct2.lut3d is None
    assert np.allclose(ct.matrix, ct2.matrix)


def _read_cube(path):
    size = None
    values = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("TITLE") or line.startswith("DOMAIN"):
                continue
            if line.startswith("LUT_3D_SIZE"):
                size = int(line.split()[1])
                continue
            parts = line.split()
            if len(parts) == 3:
                values.append([float(x) for x in parts])
    return size, np.array(values)


def test_to_cube_header_and_rows(tmp_path):
    src, tgt = _synthetic_pairs(n=1500)
    ct = fit(src, tgt, lut_size=9)
    p = tmp_path / "out.cube"
    ct.to_cube(str(p))
    size, values = _read_cube(str(p))
    assert size == 9
    assert values.shape[0] == 9 ** 3


def test_to_cube_bakes_matrix(tmp_path):
    # 带非恒等矩阵的变换:单独应用导出的 cube 应与 apply 接近。
    src, tgt = _synthetic_pairs(n=4000)
    ct = fit(src, tgt, lut_size=17)
    assert not np.allclose(ct.matrix, np.eye(3))  # 确有非恒等矩阵

    p = tmp_path / "baked.cube"
    ct.to_cube(str(p))
    size, values = _read_cube(str(p))

    # 用 .cube 重建一个纯查表变换(矩阵=单位阵),定义域 [0,1]
    # values 顺序:R 最快 -> reshape 后索引为 (b,g,r,3),需转成 (r,g,b,3)
    cube_lut = values.reshape(size, size, size, 3).transpose(2, 1, 0, 3)
    cube_ct = ColorTransform(np.eye(3), cube_lut, (0.0, 1.0))

    pts = np.random.default_rng(99).random((500, 3))
    a = ct.apply(pts)
    b = cube_ct.apply(pts)
    # bake 网格大小 = 17:单独 cube 与原变换的差异只剩「曲面在 17^3 网格上重采样再插值」
    # 的分辨率误差,对平滑变换约 0.5%~0.6%,属视觉接近。
    assert delta_e_stats(a, b)["mean"] < 8e-3


# ----------------------------------------------------------------------
# delta_e_stats
# ----------------------------------------------------------------------
def test_delta_e_stats_keys():
    a = np.zeros((10, 3))
    b = np.ones((10, 3))
    st = delta_e_stats(a, b)
    for k in ("mean", "p50", "p95"):
        assert k in st
    assert st["mean"] == pytest.approx(np.sqrt(3.0))
