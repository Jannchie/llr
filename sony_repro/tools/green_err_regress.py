"""绿色核差在哪:拿**残差**去定位另一绿平面的贡献。

现状(`green_base_test.py`):套 R/B 的算子跑绿色,逐位相同 51.7%、误差中位 0、
整体解释 99.75%。几何大体是对的,差的是一处局部修正。而捕获里绿色有 `ref2`
(另一绿平面)、R/B 没有 —— 这条本身就说明绿色的滤波器读了它。

平坦区回归定不出 ref2 的位置,因为那里 `ref ≈ ref2`,共线性极强
(`green_kern_solve.py` 的 other 项系数小且带负号,就是这个)。所以换成对**残差**
回归:凡是 ref2 独立于 ref 的成分,只在非平坦处才显形,而残差恰好也集中在那里。

    残差 = 我的输出 − 引擎输出

对候选池 {ref2 各偏移 − ref 中心} 回归。若某个偏移的系数显著且成群,那就是
ref2 进入抽头集的位置;若残差与 ref2 全然无关,那这条线索是错的,得回去读反汇编。

⚠️ 残差里含**阈值选择翻转**造成的跳变(一个抽头进出的幅度),那部分不是线性的,
回归解释不掉。所以判据是"系数是否结构化"(成群、对称、量级接近 1/25),
而不是 R² 有多高。
"""
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import numpy as np

sys.path.insert(0, "/home/jannchie/llr/sony_repro/tools")
from green_base_test import FILT_MARGIN, W, filt, shifted  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))


def main():
    z = np.load(os.path.join(HERE, "rawnr_kern_fl_test_g_s0w0.npz"))
    d, ref, ref2, out = z["detail"], z["ref"], z["ref2"], z["out"]
    tbl0, tbl3 = z["tbl0"], z["tbl3"]
    gain, limit = int(z["gain"][0]), int(z["limit"][0])
    off = float(z["offset"][0])
    h, w = ref.shape

    got = filt(d, ref, tbl0, int(tbl3[0]), gain, limit, off, "m9")
    sl = (slice(W - FILT_MARGIN, h - W - FILT_MARGIN),
          slice(W - FILT_MARGIN, w - W - FILT_MARGIN))
    eng = out[W:h - W, W:w - W]
    err = (got[sl] - eng).astype(np.float64)

    print(f"残差:非零 {100*float(np.mean(err != 0)):.2f}%  "
          f"标准差 {err.std():.4f}  最大 {np.abs(err).max():.4g}")
    print(f"两绿平面的差 |ref2−ref|:均值 {float(np.abs(ref2-ref).mean()):.3f}  "
          f"最大 {float(np.abs(ref2-ref).max()):.3f}")

    # 候选:ref2 各偏移相对 ref 中心的**增量**。用增量而不是原值,
    # 是为了把两平面共有的部分(已经被 m9 那一版吃掉了)先剔掉。
    # err 的原点在 ref 的 (W, W);候选池要留 R 的边距,所以两边都以 ref 的坐标
    # 为准对齐,而不是各裁各的 —— 头一版按边距之差裁,形状对不上。
    R = 6
    top = R - W          # err 里要跳过的行数(可能为负,那就说明 R < W)
    if top < 0:
        raise SystemExit(f"R={R} 小于 W={W},候选池够不到 err 的边缘")
    e2d = err[top:err.shape[0] - top, top:err.shape[1] - top]
    e = e2d.ravel()
    c0 = shifted(ref, 0, 0, R).astype(np.float64)
    print(f"  对齐:err {err.shape} -> {e2d.shape},候选 {c0.shape}")

    cols, names = [], []
    for dy in range(-4, 5):
        for dx in range(-4, 5):
            cols.append((shifted(ref2, dy, dx, R).astype(np.float64) - c0).ravel())
            names.append(f"ref2({dy:+d},{dx:+d})")
    A = np.stack(cols, axis=1)
    if A.shape[0] != e.size:
        print(f"对齐失败:A {A.shape[0]} vs err {e.size}")
        return 1

    coef, *_ = np.linalg.lstsq(A, e, rcond=None)
    pred = A @ coef
    r2 = 1 - float(((e - pred) ** 2).sum() / (e ** 2).sum())
    print(f"\n  对 ref2 增量回归:R² = {r2:.4f}  (残差能被 ref2 解释掉多少)")
    order = np.argsort(-np.abs(coef))
    print("  最大的 16 个:")
    for i in order[:16]:
        print(f"    {names[i]:<16} {coef[i]:+.5f}")
    print(f"\n  1/25 = {1/25:.5f};若某几个成群落在这个量级且位置对称,"
          f"那就是 ref2 的抽头位置。")
    print("  若 R² 很低且系数杂乱,这条线索不成立 —— 回去读 0x3a0c30 的反汇编。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
