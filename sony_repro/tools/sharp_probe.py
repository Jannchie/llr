r"""SIMDSharpness 到底做了什么?——先看形态,别急着套 unsharp mask。

判断顺序(每一步都可能直接否掉下一步):
 1. 三个平面各改了多少?只改一个 -> 只锐化亮度。
 2. 改动量与自身值相关吗?与拉普拉斯相关吗?
 3. 当成线性滤波器最小二乘拟合 KxK 核,看残差 —— 拟合得好就是线性 USM,
    拟合不好说明有阈值/削波之类的非线性。
 4. 若线性拟合不好,看 delta vs 高通响应的散点(分箱中位),找出那条传递曲线。

用法: python sharp_probe.py [npz路径] [tile编号...]
"""
import sys
from pathlib import Path

import numpy as np

NPZ = Path(__file__).parent / "tiles_SIMDSharpness.npz"


def boxblur(x, k):
    pad = np.pad(x, k // 2, mode="edge")
    cs = np.pad(pad.cumsum(0).cumsum(1), ((1, 0), (1, 0)))
    return (cs[k:, k:] - cs[:-k, k:] - cs[k:, :-k] + cs[:-k, :-k]) / (k * k)


def fit_kernel(src, dst, r, mask):
    """最小二乘拟合 (2r+1)^2 的线性核。返回核与残差 std。"""
    n = 2 * r + 1
    ys, xs = np.nonzero(mask)
    cols = []
    for dy in range(-r, r + 1):
        for dx in range(-r, r + 1):
            cols.append(src[ys + dy, xs + dx])
    A = np.stack(cols, 1).astype(np.float64)
    b = dst[ys, xs].astype(np.float64)
    k, *_ = np.linalg.lstsq(A, b, rcond=None)
    resid = b - A @ k
    return k.reshape(n, n), resid.std(), b.std()


def main():
    z = np.load(sys.argv[1] if len(sys.argv) > 1 else NPZ)
    tiles = [int(a) for a in sys.argv[2:]] or sorted(
        int(k[1:].split("_")[0]) for k in z if k.endswith("_in"))

    for t in tiles:
        a = z[f"t{t}_in"].astype(np.int32)
        b = z[f"t{t}_out"].astype(np.int32)
        meta = z[f"t{t}_meta"]
        print(f"\n===== tile {t}  {a.shape}  有效矩形 {meta[12:16]}  整幅位置 {meta[18:22]}")
        for k in range(a.shape[2]):
            x, y = a[..., k], b[..., k]
            d = y - x
            print(f"  平面{k}: 改动 {100 * (d != 0).mean():5.2f}%  "
                  f"均值 {x.mean():8.1f}->{y.mean():8.1f}  "
                  f"|d| 中位 {np.median(np.abs(d)):6.1f} p99 {np.percentile(np.abs(d), 99):7.1f} "
                  f"max {np.abs(d).max()}  std {x.std():.1f}->{y.std():.1f}")

        # 只在中央区域做拟合,躲开边界
        h, w, _ = a.shape
        m = np.zeros((h, w), bool)
        m[40:h - 40, 40:w - 40] = True
        for k in range(a.shape[2]):
            x = a[..., k].astype(np.float64)
            y = b[..., k].astype(np.float64)
            if (y - x).std() < 1e-9:
                print(f"  平面{k}: 恒等,跳过拟合")
                continue
            for r in (1, 2, 3, 4):
                ker, rs, bs = fit_kernel(x, y, r, m)
                print(f"  平面{k} 线性核 {2*r+1}x{2*r+1}: 残差 std {rs:8.3f} "
                      f"(输出 std {bs:.1f}, 解释 {100*(1-rs**2/bs**2):6.2f}%)  和 {ker.sum():.4f}")
                if r == 2:
                    np.set_printoptions(precision=4, suppress=True, linewidth=140)
                    print(ker)
            # 传递曲线:delta 对「自身 - 邻域均值」(高通)的关系
            d = y - x
            for kk in (3, 5, 9):
                hp = x - boxblur(x, kk)
                sel = m & (np.abs(hp) > 0)
                r = np.corrcoef(d[sel], hp[sel])[0, 1]
                print(f"  平面{k} delta vs 高通(box{kk}) 相关 {r:+.4f}")


if __name__ == "__main__":
    main()
