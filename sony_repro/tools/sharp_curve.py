r"""SIMDSharpness 的传递曲线:delta 是高通响应的什么函数?

`sharp_probe.py` 已经给出:只动平面0,线性核解释 99.99% 的**输出**方差 —— 但输出方差
本来就几乎全是原图,这个数字没有意义。要看的是**delta 本身**被解释掉多少。
而且只有 20~50% 的像素有改动 -> 多半有死区(coring)。

做法:
 1. 直接对 delta 拟合 KxK 线性核(和应当为 0),得到高通算子 H。
 2. hp = conv(Y, H) 归一化后,把 delta 按 hp 分箱取中位 -> 传递曲线。
 3. 看死区宽度、线性段斜率、是否削波,以及曲线是否随本地亮度/方差变化。

用法: python sharp_curve.py [npz] [tile...]
"""
import sys
from pathlib import Path

import numpy as np

NPZ = Path(__file__).parent / "tiles_SIMDSharpness.npz"
R = 3


def conv(x, k):
    r = k.shape[0] // 2
    out = np.zeros_like(x, dtype=np.float64)
    pad = np.pad(x, r, mode="edge")
    for dy in range(-r, r + 1):
        for dx in range(-r, r + 1):
            c = k[dy + r, dx + r]
            if c:
                out += c * pad[r + dy:r + dy + x.shape[0], r + dx:r + dx + x.shape[1]]
    return out


def fit_delta_kernel(y, d, r, m):
    ys, xs = np.nonzero(m)
    cols = [y[ys + dy, xs + dx] for dy in range(-r, r + 1) for dx in range(-r, r + 1)]
    A = np.stack(cols, 1).astype(np.float64)
    b = d[ys, xs].astype(np.float64)
    k, *_ = np.linalg.lstsq(A, b, rcond=None)
    return k.reshape(2 * r + 1, 2 * r + 1), (b - A @ k).std(), b.std()


def main():
    z = np.load(sys.argv[1] if len(sys.argv) > 1 else NPZ)
    tiles = [int(a) for a in sys.argv[2:]] or sorted(
        int(k[1:].split("_")[0]) for k in z if k.endswith("_in"))

    for t in tiles:
        y = z[f"t{t}_in"][..., 0].astype(np.float64)
        d = z[f"t{t}_out"][..., 0].astype(np.float64) - y
        h, w = y.shape
        m = np.zeros((h, w), bool)
        m[40:h - 40, 40:w - 40] = True
        print(f"\n===== tile {t}   delta std {d[m].std():.2f}  "
              f"改动 {100*(d[m]!=0).mean():.1f}%  delta 均值 {d[m].mean():+.2f}")

        for r in (1, 2, 3):
            k, rs, bs = fit_delta_kernel(y, d, r, m)
            print(f"  对 delta 拟合 {2*r+1}x{2*r+1} 线性核: 残差 std {rs:7.2f} / "
                  f"delta std {bs:7.2f} -> 解释 {100*(1-rs**2/bs**2):5.1f}%   核和 {k.sum():+.4f}")
        k, rs, bs = fit_delta_kernel(y, d, R, m)
        np.set_printoptions(precision=4, suppress=True, linewidth=160)
        print("  高通核(未归一):"); print(k)

        # 归一化:让中心为 1、其余按比例 -> hp 的量纲与 Y 一致
        g = k[R, R]
        hp = conv(y, k / g)
        print(f"  中心系数(=总增益) {g:.4f}")

        # 传递曲线
        hpm, dm = hp[m], d[m]
        print("\n   hp 区间        n      delta 中位   delta 均值   p10..p90")
        edges = [-4000, -2000, -1000, -500, -300, -200, -150, -100, -70, -50, -35, -25,
                 -15, -8, -4, 0, 4, 8, 15, 25, 35, 50, 70, 100, 150, 200, 300, 500,
                 1000, 2000, 4000]
        for lo, hi in zip(edges[:-1], edges[1:]):
            s = (hpm >= lo) & (hpm < hi)
            if s.sum() < 100:
                continue
            lo10, hi90 = np.percentile(dm[s], [10, 90])
            print(f"  {lo:>6}..{hi:<6} {s.sum():>8}   {np.median(dm[s]):>9.1f}  "
                  f"{dm[s].mean():>10.1f}   {lo10:.0f}..{hi90:.0f}")

        # 死区?统计 |hp| 小的地方 delta 是不是 0
        for th in (2, 5, 10, 20, 40, 80):
            s = m & (np.abs(hp) < th)
            if s.sum() > 100:
                print(f"  |hp|<{th:<4}: {100*(d[s]==0).mean():5.1f}% 的像素 delta==0  "
                      f"(n={s.sum()})")

        # 曲线随亮度变吗?
        print("\n  按亮度分层的等效增益(线性回归 delta~hp,只用 |hp|>50 的点):")
        for lo, hi in ((0, 500), (500, 1500), (1500, 3000), (3000, 6000),
                       (6000, 10000), (10000, 16384)):
            s = m & (y >= lo) & (y < hi) & (np.abs(hp) > 50)
            if s.sum() < 500:
                continue
            a = np.polyfit(hp[s], d[s], 1)
            print(f"    Y {lo:>6}..{hi:<6} n={s.sum():>8}  斜率 {a[0]:+.4f} 截距 {a[1]:+.1f}")


if __name__ == "__main__":
    main()
