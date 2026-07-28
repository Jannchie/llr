r"""不猜几何,直接从数据把有效核解出来。

`spica_recon.py` 否掉了「5x5 row-major」的猜测(相关系数只有 0.43)。这里改成
**不预设网格形状**:固定一个 idx 和一个朝向,拿 7x7 邻域(覆盖 -3..+3,把标量版
`-3dx…+2dx` 的范围完全包住)的 49 个像素对 `out - in` 做最小二乘。

哪些位置的系数显著,几何就是哪些位置 —— 25 个 tap 会自己浮出来。

两个必须注意的地方:
* **按 idx 分组**。不同 idx 用不同的核,混在一起回归出来的是加权平均,没有意义。
* **限制在线性区**。出口还要过一条三次软限幅和 ISO 增益,饱和段会把回归带偏;
  只用 |out - in| 小的样本,那里限幅应当接近恒等。

用法: python spica_solve.py [--idx 1] [--top 30] [tiles_SIMDSpica.npz]
"""
import json
import os
import sys

import numpy as np

SCR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCR)
from spica_recon import MARGIN, STEP, build_lut, classify  # noqa: E402

R = 3                       # 邻域半径,7x7 = 49 个候选位置


def _opt(flag, default):
    return type(default)(sys.argv[sys.argv.index(flag) + 1]) if flag in sys.argv else default


def main():
    want_idx = _opt("--idx", 1)
    top = _opt("--top", 30)
    args = [a for a in sys.argv[1:] if not a.startswith("--") and not a.isdigit()]
    path = args[0] if args else os.path.join(SCR, "tiles_SIMDSpica.npz")

    lut = build_lut()
    z = np.load(path)
    tiles = sorted(int(k[1:].split("_")[0]) for k in z if k.endswith("_in"))

    X, Y = [], []
    for t in tiles:
        a = z[f"t{t}_in"][..., 0].astype(np.float64)
        b = z[f"t{t}_out"][..., 0].astype(np.float64)
        m = classify(a)
        idx, dx, dy = lut[m, 0], lut[m, 1], lut[m, 2]

        v = slice(MARGIN, -MARGIN)
        sel = np.zeros(a.shape, bool)
        sel[v, v] = True
        sel &= (idx == want_idx) & (dx == 1) & (dy == 1)     # 固定朝向,先解 e
        d = b - a
        sel &= np.abs(d) < 120                               # 线性区,避开软限幅饱和
        ys, xs = np.nonzero(sel)
        if len(ys) == 0:
            continue
        if len(ys) > 60000:                                  # 够用即可,控制内存
            k = np.random.default_rng(0).choice(len(ys), 60000, replace=False)
            ys, xs = ys[k], xs[k]
        cols = [a[ys + i, xs + j] for i in range(-R, R + 1) for j in range(-R, R + 1)]
        X.append(np.stack(cols, 1))
        Y.append(d[ys, xs])

    if not X:
        print(f"idx={want_idx} 没有样本")
        return
    X = np.concatenate(X)
    Y = np.concatenate(Y)
    X = np.hstack([X, np.ones((len(X), 1))])                 # 带常数项
    print(f"idx={want_idx}, 朝向 e(1,1):  {len(X)} 个样本 x {X.shape[1]} 个未知数")

    coef, *_ = np.linalg.lstsq(X, Y, rcond=None)
    pred = X @ coef
    resid = Y - pred
    print(f"  残差 RMS {np.sqrt((resid ** 2).mean()) / STEP:.4f} 个 8-bit 级"
          f"   (原信号 {np.sqrt((Y ** 2).mean()) / STEP:.4f})")
    print(f"  R^2 = {1 - (resid ** 2).sum() / ((Y - Y.mean()) ** 2).sum():.6f}")
    print(f"  常数项 {coef[-1]:.4f}   系数和 {coef[:-1].sum():.6f}")

    w = coef[:-1].reshape(2 * R + 1, 2 * R + 1)
    print("\n7x7 系数(x1000),行 = dy -3..+3,列 = dx -3..+3:")
    print("        " + "".join(f"{j:>9d}" for j in range(-R, R + 1)))
    for i in range(2 * R + 1):
        print(f"  {i - R:+d}  " + "".join(f"{1000 * w[i, j]:9.2f}" for j in range(2 * R + 1)))

    flat = [(abs(v), i - R, j - R, v) for i, row in enumerate(w)
            for j, v in enumerate(row)]
    flat.sort(reverse=True)
    print(f"\n按 |系数| 排序的前 {top} 个位置:")
    for mag, i, j, v in flat[:top]:
        print(f"  ({i:+d},{j:+d})  {1000 * v:9.3f}")
    print(f"\n前 25 个的 |系数| 之和占全部的 "
          f"{100 * sum(f[0] for f in flat[:25]) / sum(f[0] for f in flat):.2f}%")


if __name__ == "__main__":
    main()
