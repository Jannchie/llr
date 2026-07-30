"""Marble 对色差做了什么 —— 有真值就别读代码。

Marble 是最后一级,in 与 out 都能抓到全分辨率,所以可以直接对着真值验算子假设:

  H1 亮度不动?                    量 |Y_out − Y_in|
  H2 色差被低通?                  拿 (R−G)_in 过各种半径的盒/二项低通,看哪个最像 out
  H3 色差被中值?                  同上换中值
  H4 色差被按亮度衰减(像 ChromaSuppres)?  看 out/in 的比值随 Y 的走势
"""
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import numpy as np

Z = Path("/home/jannchie/llr/sony_repro/tools/tiles_marble.npz")
W601 = np.array([0.299, 0.587, 0.114])


def box(a, k):
    pad = k // 2
    p = np.pad(a, pad, mode="edge")
    cs = np.pad(p.cumsum(0).cumsum(1), ((1, 0), (1, 0)))
    return (cs[k:, k:] - cs[:-k, k:] - cs[k:, :-k] + cs[:-k, :-k]) / (k * k)


TILE = 32


def mad(x):
    return float(np.median(np.abs(x - np.median(x))) * 1.4826)


def detail(a):
    h, w = a.shape[0] & ~1, a.shape[1] & ~1
    a = a[:h, :w]
    m = (a[0::2, 0::2] + a[1::2, 0::2] + a[0::2, 1::2] + a[1::2, 1::2]) * 0.25
    return a - np.repeat(np.repeat(m, 2, axis=0), 2, axis=1)


def flat_tiles(y, frac=0.25, cap=200):
    h, w = y.shape[0] // TILE * TILE, y.shape[1] // TILE * TILE
    v = y[:h, :w].reshape(h // TILE, TILE, w // TILE, TILE).std(axis=(1, 3))
    ys, xs = np.nonzero(v <= np.quantile(v, frac))
    idx = np.linspace(0, len(ys) - 1, min(cap, len(ys))).astype(int)
    return [(slice(int(ys[k]) * TILE, int(ys[k]) * TILE + TILE),
             slice(int(xs[k]) * TILE, int(xs[k]) * TILE + TILE)) for k in idx]


def explained(target, cand):
    """cand 解释掉 target 的多少 —— 1 − var(残差)/var(target)。"""
    t, c = target.ravel(), cand.ravel()
    return 1.0 - float(np.var(t - c) / max(np.var(t), 1e-12))


def main():
    z = np.load(Z)
    for tag in ("t12", "t17"):
        a = z[f"{tag}_in"].astype(np.float64)
        b = z[f"{tag}_out"].astype(np.float64)
        m = 8  # 切掉边界
        a, b = a[m:-m, m:-m], b[m:-m, m:-m]
        yi, yo = a @ W601, b @ W601
        print(f"\n=== {tag}  {a.shape}")
        print(f"  亮度: in 均值 {yi.mean():8.1f}  out {yo.mean():8.1f}   "
              f"|Δ| 中位 {np.median(np.abs(yo - yi)):7.3f}")
        # 平坦区的细尺度 MAD —— 与 stage_chroma.py 同一指标。整块 std 被真实画面
        # 结构主导,量不出噪声:Marble 保住大尺度色彩结构而只清细噪声,std 因此几乎不动。
        tiles = flat_tiles(yi)
        for name, i, j in (("R-G", 0, 1), ("B-G", 2, 1)):
            di, do = a[..., i] - a[..., j], b[..., i] - b[..., j]
            fi = np.median([mad(detail(di[s])) for s in tiles])
            fo = np.median([mad(detail(do[s])) for s in tiles])
            print(f"  {name}: 细节 MAD in {fi:8.3f} → out {fo:8.3f}   "
                  f"×{fo / max(fi, 1e-9):5.3f}      "
                  f"整块 std {di.std():8.1f} → {do.std():8.1f}")
            # H2:各半径盒低通
            best = max(((k, explained(do, box(di, k))) for k in (3, 5, 7, 9, 13, 17, 25)),
                       key=lambda t: t[1])
            print(f"        盒低通最佳 k={best[0]:2d} 解释 {best[1] * 100:6.2f}%   "
                  f"(原样 {explained(do, di) * 100:6.2f}%,  常数 0 "
                  f"{explained(do, np.zeros_like(do)) * 100:6.2f}%)")
            # H4:整体缩放?
            s = float((do * di).sum() / max((di * di).sum(), 1e-12))
            print(f"        最佳整体缩放 {s:6.3f} 解释 {explained(do, s * di) * 100:6.2f}%")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
