r"""Vatr 的增益依赖多大范围的邻域?扫尺度。

`vatr_probe.py` 先报「残差与 33x33 邻域均值相关 -0.0001」,看着像逐像素算法;
但同一份残差在 64 块(=256 原始像素)尺度上明显有结构。**是盒子选小了** ——
dump 是 STEP=4,33x33 只覆盖 132 个原始像素,而经典 DRO 的核要大得多。

这里对每个尺度都扣掉「按像素值的中位增益」再看残差相关,找出真正的核尺度。
"""
import sys
from pathlib import Path

import numpy as np

NPZ = Path(__file__).parent / "stage_frames.npz"
STEP = 4


def boxmean(x, k):
    pad = np.pad(x, k // 2, mode="edge")
    cs = np.pad(pad.cumsum(0).cumsum(1), ((1, 0), (1, 0)))
    return (cs[k:, k:] - cs[:-k, k:] - cs[k:, :-k] + cs[:-k, :-k]) / (k * k)


def main():
    z = np.load(sys.argv[1] if len(sys.argv) > 1 else NPZ)
    a, b = z["ZcTaskVatr_in"], z["ZcTaskVatr_out"]
    ok = (a.max(-1) > 0) & (a[..., 1] > 100)
    g_in = a[..., 1].astype(np.float64)
    gain = np.where(g_in > 0, b[..., 1].astype(np.float64) / np.maximum(g_in, 1), 1.0)

    xs, gs = g_in[ok], gain[ok]
    bins = np.clip((xs / 32).astype(int), 0, 511)
    med = np.zeros(512)
    for i in np.unique(bins):
        med[i] = np.median(gs[bins == i])
    resid = gs - med[bins]
    print(f"增益 std {gs.std():.5f}   扣掉逐像素曲线后残差 std {resid.std():.5f}\n")
    print(f"{'核(原始像素)':>14}  残差 vs 邻域均值相关   解释掉的残差方差")
    for k in (9, 33, 65, 129, 257, 385, 513):
        bm = boxmean(g_in, k)[ok]
        r = np.corrcoef(resid, bm)[0, 1]
        print(f"{k * STEP:>14}  {r:+.4f}                {100 * r * r:5.1f}%")


if __name__ == "__main__":
    main()
