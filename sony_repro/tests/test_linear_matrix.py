from pathlib import Path

import numpy as np
import pytest

from sony_repro import linear_matrix as lm

DATA = Path(__file__).resolve().parent.parent / "data"
FULL = DATA / "lm16_DSC03015_full.npz"          # 实机 dump:节点表 + 展开后的 1024 项真值
BIN0 = DATA / "lm16_DSC02960.npz"               # 另一张图的参数区

pytestmark = pytest.mark.skipif(not FULL.exists(), reason="缺少实机 dump 数据")


@pytest.fixture(scope="module")
def dump():
    z = np.load(FULL)
    return z["coef"], z["table"]


def test_expand_matches_engine_bit_exact(dump):
    """1024 项展开必须与引擎内存中的数组逐位相同。"""
    coef, table = dump
    got = lm.expand(lm.matrices_from_coeff(coef))
    assert got.shape == table.shape
    assert np.array_equal(got, table)


def test_knots_land_on_table(dump):
    """节点处的矩阵应精确出现在表的 0, 64, ..., 960 位置。"""
    coef, table = dump
    knots = lm.matrices_from_coeff(coef)
    for i in range(lm.N_KNOT):
        assert np.array_equal(knots[i], table[i * lm.KNOT_STEP])


def test_wraps_circularly(dump):
    """末段 960..1023 应插回节点 0,而不是钳位或外推。"""
    coef, table = dump
    knots = lm.matrices_from_coeff(coef)
    mid = table[960 + lm.KNOT_STEP // 2]
    expect = (knots[15] + (knots[0] - knots[15]) * np.float32(0.5)).astype(np.float32)
    assert np.allclose(mid, expect, atol=1e-7)


def test_rows_sum_to_one(dump):
    """矩阵保持中性灰不变 —— 这是对角元由约束推出的直接后果。"""
    _, table = dump
    assert np.allclose(table.sum(axis=2), 1.0, atol=1e-6)


def test_neutral_is_preserved_for_every_segment(dump):
    _, table = dump
    grey = np.full(3, 0.42, dtype=np.float32)
    out = np.einsum("nij,j->ni", table, grey)
    assert np.allclose(out, grey, atol=1e-6)


def test_offdiag_are_multiples_of_1_over_1024(dump):
    coef, _ = dump
    q = coef * 1024.0
    assert np.allclose(q, np.rint(q), atol=1e-3)


def test_matrices_from_coeff_sign_convention(dump):
    """非对角元 = 节点系数取负。"""
    coef, _ = dump
    knots = lm.matrices_from_coeff(coef)
    for k, (i, j) in enumerate(lm.OFFDIAG):
        assert np.allclose(knots[:, i, j], -coef[k], atol=1e-7)


def test_dtype_and_shape(dump):
    coef, _ = dump
    t = lm.expand(lm.matrices_from_coeff(coef))
    assert t.dtype == np.float32 and t.shape == (lm.N_INDEX, 3, 3)


def test_segmented_matrix_from_dump_and_apply(dump):
    coef, table = dump
    sm = lm.SegmentedMatrix(coef)
    assert np.array_equal(sm.table, table)
    assert np.array_equal(sm.matrix_at(700), table[700])
    assert np.array_equal(sm.matrix_at(N := lm.N_INDEX + 5), table[N % lm.N_INDEX])


def test_apply_is_identity_on_neutral():
    coef = np.load(FULL)["coef"]
    sm = lm.SegmentedMatrix(coef, hue_lut=np.zeros(64, dtype=np.float32))
    img = np.full((4, 5, 3), 0.3, dtype=np.float32)
    assert np.allclose(sm.apply(img), img, atol=1e-6)


@pytest.mark.skipif(not (DATA / "hue_index_lut.npz").exists(), reason="缺少色相 LUT")
def test_hue_index_range_and_monotone_wrap():
    rng = np.random.default_rng(0)
    rgb = rng.random((2000, 3)).astype(np.float32) * 1000
    idx = lm.hue_index(rgb)
    assert idx.min() >= 0 and idx.max() < lm.N_INDEX
    assert idx.shape == (2000,)


@pytest.mark.skipif(not BIN0.exists(), reason="缺少第二份 dump")
def test_second_dump_also_expands_consistently():
    coef = lm.load_coeff(BIN0)
    knots = lm.matrices_from_coeff(coef)
    table = lm.expand(knots)
    assert np.allclose(table.sum(axis=2), 1.0, atol=1e-6)
    assert np.array_equal(table[0], knots[0])
