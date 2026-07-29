r"""拿 `tile_dump.py` 抓的 RawNRSIMD 入口/出口,量它的**支撑集有多大**。

前提(已确认,见 notes/static-rawnr.md):它的阈值表就是 ARW 噪声模型那张
(`rawnr_probe.py` 抓的 tbl0 与 `rawnr_model.py` 算的逐项一致),细节增益 256(=1.0),
限幅 1023。

范围只到「线性核能看出什么」为止。**算子形状本身已经不用猜了** ——
`rawnr_simd.py` 逐位复刻了 R/B 那条路(99.97%),见 notes/static-rawnr.md 5.3;
早先在这里按 RawNRHalf 的形状穷举候选,最好的也只解释掉 12.8%,那批扫描连同
结论都已被取代,不再保留。

用法::

    python rawnr_fit.py tiles_NR_fl1250.npz rawnr_tables_fl_test.npz [--tile=12]
"""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from rawnr_ref import shifted  # noqa: E402

SCR = os.path.dirname(os.path.abspath(__file__))

#: 拟合时的行抽样步长。168 个未知数不需要一百多万条方程,而稠密设计矩阵在
#: r=6 时是 1.6 GB(加上 lstsq 自己的副本能到 5 GB);抽样后结果看不出差别。
ROW_STRIDE = 8


def opt(name, default):
    return next((a.split("=", 1)[1] for a in sys.argv if a.startswith(f"--{name}=")),
                default)


def linear_fit(src, delta, radius):
    """把 delta 拟合成 (邻居 - 中心) 的线性组合,返回 (核, 残差 std)。

    拟合的是差分而不是原值 —— 直流分量对降噪算子毫无意义,留着它只会让最小二乘
    去解释亮度本身。
    """
    r = radius
    c = shifted(src, 0, 0, r)
    keys = [(dy, dx) for dy in range(-r, r + 1) for dx in range(-r, r + 1) if dy or dx]
    a = np.empty((c[::ROW_STRIDE].size, len(keys)), np.float64)
    for i, (dy, dx) in enumerate(keys):
        a[:, i] = (shifted(src, dy, dx, r) - c)[::ROW_STRIDE].ravel()
    y = shifted(delta, 0, 0, r)[::ROW_STRIDE].ravel().astype(np.float64)
    coef, *_ = np.linalg.lstsq(a, y, rcond=None)
    resid = y - a @ coef
    ker = np.zeros((2 * r + 1, 2 * r + 1))
    for (dy, dx), v in zip(keys, coef, strict=True):
        ker[dy + r, dx + r] = v
    return ker, float(resid.std())


def main():
    tiles = np.load(sys.argv[1] if len(sys.argv) > 1
                    else os.path.join(SCR, "tiles_NR_fl1250.npz"))
    tables = np.load(sys.argv[2] if len(sys.argv) > 2
                     else os.path.join(SCR, "rawnr_tables_fl_test.npz"))
    tile = opt("tile", "12")
    src = tiles[f"t{tile}_in"][..., 0].astype(np.int64)
    dst = tiles[f"t{tile}_out"][..., 0].astype(np.int64)
    thr = tables["tbl0"]
    print("素材:", tiles["source"], " tile", tile, src.shape)

    # **必须按有效矩形裁剪。** 输出是新分配的缓冲区,矩形外从没被写过 —— 不裁的话
    # 那圈未初始化数据的 |d| 有 800 上下,会把统计和拟合全部带偏(拟合残差一路
    # 不收敛就是这么来的)。矩形在 task+0x30。
    m = tiles[f"t{tile}_meta"]
    x0, y0, x1, y1 = (int(v) for v in m[12:16])
    print(f"有效矩形 x[{x0},{x1}) y[{y0},{y1}) —— 裁掉外面 {x0} 像素的边距")
    src, dst = src[y0:y1, x0:x1], dst[y0:y1, x0:x1]
    print(f"输入域 {src.min()}..{src.max()}   输出域 {dst.min()}..{dst.max()}  "
          f"输入 std {src.std():.1f}")

    delta = dst - src
    print(f"delta: std {delta.std():.2f}  |d| 中位 {np.median(np.abs(delta)):.0f}  "
          f"p99 {np.percentile(np.abs(delta), 99):.0f}  max {np.abs(delta).max()}")
    print(f"阈值表 thr[0]={thr[0]} thr[2048]={thr[2048]} max={thr.max()}")

    # 支撑集:逐半径拟合,残差不再下降的地方就是它的实际边界
    print("\n线性核拟合(残差 std,越低说明该半径解释得越多):")
    for r in (1, 2, 3, 4, 5, 6):
        _, resid = linear_fit(src, delta, r)
        print(f"  {2 * r + 1}x{2 * r + 1}  残差 {resid:7.3f}   "
              f"(delta std {delta.std():.3f})")

    # 奇偶结构:CFA 上同相位的抽头才有权重。7x7 够看出这一点,单独再算一次。
    r = 3
    ker, _ = linear_fit(src, delta, r)
    even = [(dy, dx) for dy in range(-r, r + 1) for dx in range(-r, r + 1)
            if (dy or dx) and dy % 2 == 0 and dx % 2 == 0]
    odd = [(dy, dx) for dy in range(-r, r + 1) for dx in range(-r, r + 1)
           if (dy or dx) and (dy % 2 or dx % 2)]
    ew = np.mean([abs(ker[dy + r, dx + r]) for dy, dx in even])
    ow = np.mean([abs(ker[dy + r, dx + r]) for dy, dx in odd])
    print(f"\n7x7 核里 |权重| 均值:偶偏移 {ew:.5f}   奇偏移 {ow:.5f}   "
          f"比值 {ew / max(ow, 1e-12):.1f}")
    np.set_printoptions(precision=4, suppress=True, linewidth=200)
    print(ker)
    # 这一层是 **CFA 马赛克**(奇数间隔的差是偶数间隔的四倍),所以算子必须逐相位;
    # 跨相位的抽头拿去比阈值只会全被拒。逐相位的逐位复刻见 rawnr_simd.py。


if __name__ == "__main__":
    main()
