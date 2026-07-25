"""LinearMatrix16 —— Imaging Edge Edit.exe 的分段线性色彩矩阵(已逆向,逐位验证)。

对应管线中的 ``ZcTaskSIMDLinearMatrix16``(RVA 0x37e530),核心 kernel 0x37f8e0。
它**不是**一个全局 3x3 矩阵,而是一组按色相分段的矩阵:

1. 参数区 ``lv+0x91998`` 存一张 **6 x 16 的节点表**(``+0x28`` 起,float32,
   取值恒为 1/1024 的整数倍)。六行依次是 m01, m02, m10, m12, m20, m21,
   存表时取了负号;对角元不存,由「每行和为 1」推出 —— 即矩阵保持中性灰不变。
2. 该表被展开成 **1024 个 3x3 矩阵**的数组(``+0x228`` 起,步长 72 字节:
   前 9 个 float 是矩阵,后 9 个是 ``I - M``)。展开规则为**环形**线性插值,
   节点位于 0, 64, ..., 960::

       bin = idx // 64
       M[idx] = lerp(K[bin], K[(bin + 1) % 16], (idx - 64 * bin) / 64)

   已用实机 dump 全量核对:1024 项、每项 9 个元素,与实测最大差 **0.0**。
3. 每个像素算出一个 0..1023 的**色相角索引**去查这张表。Edit 内部用的是定点
   atan 近似(常量 366/109/37、x0.875、x1/512,象限偏移 ±256/±512/±768/±1024,
   整圈 1024),本模块改用真实 ``atan2`` 加一张标定 LUT 等价复现。

节点表随图变化,但**不需要 frida** —— 它是相机写进 RAW 的标定数据,
藏在加密的 SR2SubIFD 的 tag 0x780f 里。用 :func:`sony_repro.sr2.linear_matrix_coeff`
离线读出即可(65 张实测与引擎内存逐位一致)。
"""
from __future__ import annotations

from pathlib import Path

import numpy as np

N_KNOT = 16
KNOT_STEP = 64
N_INDEX = N_KNOT * KNOT_STEP          # 1024
OFFDIAG = ((0, 1), (0, 2), (1, 0), (1, 2), (2, 0), (2, 1))

_DATA = Path(__file__).resolve().parent.parent / "data"


def matrices_from_coeff(coeff: np.ndarray) -> np.ndarray:
    """(6, 16) 节点系数 -> (16, 3, 3) 节点矩阵。

    ``coeff`` 为参数区原始值(float32,1/1024 的整数倍),行序 m01, m02, m10,
    m12, m20, m21;非对角元取其相反数,对角元由每行和为 1 推出。
    """
    o = -np.asarray(coeff, dtype=np.float32)
    m = np.zeros((o.shape[1], 3, 3), dtype=np.float32)
    for k, (i, j) in enumerate(OFFDIAG):
        m[:, i, j] = o[k]
    m[:, 0, 0] = 1 - o[0] - o[1]
    m[:, 1, 1] = 1 - o[2] - o[3]
    m[:, 2, 2] = 1 - o[4] - o[5]
    return m


def expand(knots: np.ndarray) -> np.ndarray:
    """(16, 3, 3) 节点矩阵 -> (1024, 3, 3) 全表,环形线性插值。"""
    k = np.asarray(knots, dtype=np.float32)
    idx = np.arange(N_INDEX)
    b = idx // KNOT_STEP
    t = ((idx - b * KNOT_STEP) / np.float32(KNOT_STEP)).astype(np.float32)
    lo, hi = k[b], k[(b + 1) % N_KNOT]
    return (lo + (hi - lo) * t[:, None, None]).astype(np.float32)


def hue_index(rgb: np.ndarray, lut: np.ndarray | None = None) -> np.ndarray:
    """线性 RGB -> 0..1023 的色相角索引。

    ``lut`` 为标定表(见 ``tools/build_hue_lut.py``),缺省从 ``data/`` 载入。
    低彩度处索引误差无害:所有分段矩阵每行和都为 1,中性色恒等映射。
    """
    if lut is None:
        lut = load_hue_lut()
    a = np.asarray(rgb, dtype=np.float64)
    y = a.sum(axis=-1) / 3.0
    ang = np.arctan2(a[..., 0] - y, a[..., 2] - y) % (2 * np.pi)
    n = len(lut)
    bi = np.clip((ang / (2 * np.pi) * n).astype(np.int64), 0, n - 1)
    return np.clip(np.rint(lut[bi]).astype(np.int64), 0, N_INDEX - 1)


def load_hue_lut(path: str | Path | None = None) -> np.ndarray:
    p = Path(path) if path else _DATA / "hue_index_lut.npz"
    return np.load(p)["lut"]


def load_coeff(path: str | Path) -> np.ndarray:
    """从 dump 的 npz 载入 (6, 16) 节点系数。"""
    z = np.load(path)
    return np.asarray(z["coef"], dtype=np.float32)


class SegmentedMatrix:
    """一张展开好的分段矩阵表,可直接作用到图像上。"""

    def __init__(self, coeff: np.ndarray, hue_lut: np.ndarray | None = None):
        self.coeff = np.asarray(coeff, dtype=np.float32)
        self.knots = matrices_from_coeff(self.coeff)
        self.table = expand(self.knots)
        self._hue_lut = hue_lut

    @classmethod
    def from_dump(cls, path: str | Path, hue_lut: np.ndarray | None = None) -> "SegmentedMatrix":
        return cls(load_coeff(path), hue_lut)

    @classmethod
    def from_arw(cls, path: str | Path, hue_lut: np.ndarray | None = None) -> "SegmentedMatrix":
        """直接从 ARW 的标定数据构造 —— 无需跑 Edit.exe。"""
        from .sr2 import linear_matrix_coeff

        return cls(linear_matrix_coeff(path), hue_lut)

    def matrix_at(self, index: np.ndarray | int) -> np.ndarray:
        return self.table[np.asarray(index) % N_INDEX]

    def apply(self, rgb: np.ndarray) -> np.ndarray:
        """对 ``(..., 3)`` 的线性 RGB 逐像素套用对应分段的矩阵。"""
        a = np.asarray(rgb)
        m = self.matrix_at(hue_index(a, self._hue_lut))
        return np.einsum("...ij,...j->...i", m.astype(a.dtype), a)
