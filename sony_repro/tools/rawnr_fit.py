r"""拿 `tile_dump.py` 抓的 RawNRSIMD 入口/出口,反推它到底在算什么。

前提(已确认,见 notes/static-rawnr.md):它的阈值表就是 ARW 噪声模型那张
(`rawnr_probe.py` 抓的 tbl0 与 `rawnr_model.py` 算的逐项一致),细节增益 256(=1.0),
限幅 1023。剩下要定的是**算子形状**:参考值是什么、支撑集多大、哪些抽头参与。

用法::

    python rawnr_fit.py tiles_NR_fl1250.npz rawnr_tables.npz [--tile 12]
"""
import os
import sys

import numpy as np

SCR = os.path.dirname(os.path.abspath(__file__))


def opt(name, default):
    return next((a.split("=", 1)[1] for a in sys.argv if a.startswith(f"--{name}=")),
                default)


def shifted(a, dy, dx, r):
    """a 平移 (dy,dx) 后的内部区域;r 是四周留出的边距。"""
    h, w = a.shape
    return a[r + dy:h - r + dy, r + dx:w - r + dx]


def linear_fit(src, delta, radius):
    """把 delta 拟合成 (邻居 - 中心) 的线性组合,返回 (核, 残差 std)。

    拟合的是差分而不是原值 —— 直流分量对降噪算子毫无意义,留着它只会让最小二乘
    去解释亮度本身。
    """
    r = radius
    c = shifted(src, 0, 0, r)
    cols, keys = [], []
    for dy in range(-r, r + 1):
        for dx in range(-r, r + 1):
            if dy == 0 and dx == 0:
                continue
            cols.append((shifted(src, dy, dx, r) - c).ravel())
            keys.append((dy, dx))
    a = np.stack(cols, axis=1).astype(np.float64)
    y = shifted(delta, 0, 0, r).ravel().astype(np.float64)
    coef, *_ = np.linalg.lstsq(a, y, rcond=None)
    resid = y - a @ coef
    ker = np.zeros((2 * r + 1, 2 * r + 1))
    for (dy, dx), v in zip(keys, coef, strict=True):
        ker[dy + r, dx + r] = v
    return ker, float(resid.std())


def candidate(src, thr, radius, gain, limit, skip_cross, ref_mode):
    """按 RawNRHalf 那套形状算一遍,支撑半径/是否跳过中心行列/参考值可换。

    要定的就是这几个自由度 —— 阈值表、增益、限幅都已经从进程里抓到了。
    """
    r = radius
    a = src
    c = shifted(a, 0, 0, r)
    if ref_mode == "binomial":
        n4 = (shifted(a, -1, 0, r) + shifted(a, 1, 0, r)
              + shifted(a, 0, -1, r) + shifted(a, 0, 1, r))
        diag = (shifted(a, -1, -1, r) + shifted(a, -1, 1, r)
                + shifted(a, 1, -1, r) + shifted(a, 1, 1, r))
        hp = 12 * c - 2 * n4 - diag
        d = np.sign(hp) * (np.abs(hp) >> 4)
        lo = c - d
    else:                                    # 参考值就是中心像素
        lo, d = c, np.zeros_like(c)

    ref = np.clip(lo, 0, (1 << 15) - 1)
    t = thr[ref]
    total = np.zeros_like(ref)
    count = np.zeros_like(ref)
    for dy in range(-r, r + 1):
        for dx in range(-r, r + 1):
            if dy == 0 and dx == 0:
                continue
            if skip_cross and (dy == 0 or dx == 0):
                continue
            v = shifted(a, dy, dx, r)
            ok = np.abs(v - ref) < t
            total += np.where(ok, v, 0)
            count += ok
    mean = (ref + total) // (count + 1)
    boost = np.clip((d * gain) >> 8, -limit, limit)
    return np.clip(mean + boost, 0, 0x3FFF), r


def main():
    tiles = np.load(sys.argv[1] if len(sys.argv) > 1
                    else os.path.join(SCR, "tiles_NR_fl1250.npz"))
    tables = np.load(sys.argv[2] if len(sys.argv) > 2
                     else os.path.join(SCR, "rawnr_tables.npz"))
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
    kers = {}
    for r in (1, 2, 3, 4, 5, 6):
        ker, resid = linear_fit(src, delta, r)
        kers[r] = ker
        print(f"  {2 * r + 1}x{2 * r + 1}  残差 {resid:7.3f}   "
              f"(delta std {delta.std():.3f})")

    # 奇偶结构:CFA 上同相位的抽头才有权重
    ker = kers[3]
    r = 3
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

    # 这一层是 **CFA 马赛克**(奇数间隔的差是偶数间隔的四倍),所以算子必须逐相位。
    # 跨相位的抽头拿去比阈值只会全被拒,把它们算进来的候选一律不成立。
    print("\n候选算子 vs 真值(逐相位,残差越小越像):")
    print(f"  {'参考值':10} {'半径':>4} {'跳中心行列':>10} {'残差 std':>9} {'解释掉':>7}")
    best = None
    for ref_mode in ("binomial", "centre"):
        for r in (1, 2, 3, 4):
            for skip in (True, False):
                out = dst.copy()
                for py in (0, 1):
                    for px in (0, 1):
                        got, rr = candidate(src[py::2, px::2], thr, r, 256, 1023,
                                            skip, ref_mode)
                        out[py::2, px::2][rr:-rr or None, rr:-rr or None] = got
                keep = 2 * max((1, 2, 3, 4))
                resid = float((out - dst)[keep:-keep, keep:-keep].std())
                frac = 1 - (resid / delta[keep:-keep, keep:-keep].std()) ** 2
                print(f"  {ref_mode:10} {r:>4} {str(skip):>10} {resid:>9.3f} {frac:>6.1%}")
                if best is None or resid < best[0]:
                    best = (resid, ref_mode, r, skip)
    print(f"  最佳:{best[1]} 半径{best[2]} 跳中心行列={best[3]}  残差 {best[0]:.3f}")


if __name__ == "__main__":
    main()
