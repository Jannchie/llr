"""SR2SubIFD 解密 + 节点表离线提取。

真值是 frida 从 Edit.exe 内存里 dump 的参数区(``data/lm16_DSC03015_full.npz``);
本测试证明不跑 Edit.exe 也能得到逐位相同的表。
"""
from pathlib import Path

import numpy as np
import pytest

from sony_repro import linear_matrix as lm
from sony_repro import sr2

DATA = Path(__file__).resolve().parent.parent / "data"
FULL = DATA / "lm16_DSC03015_full.npz"
ARW = Path("/mnt/e/10960725/DSC03015.ARW")

needs_arw = pytest.mark.skipif(not ARW.exists(), reason="缺少素材 ARW")


@pytest.fixture(scope="module")
def block():
    return sr2.read_sr2_tag(ARW)


@needs_arw
def test_tag_is_276_bytes(block):
    """解包函数读的正是 23x12=276 字节,tag 大小必须刚好对上。"""
    assert len(block) == 276


@needs_arw
def test_matches_engine_memory_bit_exact(block):
    """离线解出的节点表必须与引擎内存中的逐位相同。"""
    truth = np.load(FULL)["coef"]
    assert np.array_equal(sr2.unpack_param_block(block), truth)


@needs_arw
def test_coeff_are_multiples_of_1_over_1024(block):
    q = sr2.unpack_param_block(block) * 1024.0
    assert np.allclose(q, np.rint(q), atol=1e-3)


@needs_arw
def test_expands_to_the_engine_table(block):
    """整条链走通:ARW -> 系数 -> 节点矩阵 -> 1024 项表,对上引擎的表。"""
    table = np.load(FULL)["table"]
    got = lm.expand(lm.matrices_from_coeff(sr2.unpack_param_block(block)))
    assert np.array_equal(got, table)


@needs_arw
def test_segmented_matrix_from_arw():
    sm = lm.SegmentedMatrix.from_arw(ARW)
    assert np.array_equal(sm.table, np.load(FULL)["table"])


@needs_arw
def test_leading_dwords_are_the_knot_positions(block):
    """位流前 6 个 dword 编码节点位置表 0,0,64,128,...;它是格式没跑偏的哨兵。"""
    import struct

    d = struct.unpack_from("<6I", block, 0)
    got = [d[0] & 0x3FF, (d[1] >> 11) & 0x3FF, d[1] & 0x3FF]
    assert got == [64, 192, 256]


def test_decrypt_is_involutive_on_a_synthetic_buffer():
    """密钥流是纯异或,同一密钥解两次应还原 —— 保证实现没混进额外变换。"""
    rng = np.random.default_rng(0)
    buf = bytes(rng.integers(0, 256, 512, dtype=np.uint8))
    once = sr2.decrypt(buf, 64, 256, 1144201745)
    twice = sr2.decrypt(once, 64, 256, 1144201745)
    assert twice == buf
    assert once != buf


def test_unpack_rejects_short_block():
    with pytest.raises(ValueError):
        sr2.unpack_param_block(b"\0" * 275)


@needs_arw
def test_missing_tag_raises(tmp_path):
    with pytest.raises(KeyError):
        sr2.read_sr2_tag(ARW, tag=0xDEAD)
