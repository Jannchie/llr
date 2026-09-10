"""格纹在**哪个空间频率**上 —— 把轴向/对角比按频率环带拆开。

`aniso.py` 报的是 r∈[0.15,0.85] 整个环带的一个总数,只能回答"有没有格纹",
回答不了"多粗的格纹"。而周期这一维是能直接指认来源的:

  * 周期 2px(r≈1.0) —— 去马赛克的棋盘/相位泄漏,或最细一级小波
  * 周期 4px(r≈0.5) —— 第二级小波,或 2x2 块处理
  * 周期 8px 以上    —— 降噪斑块、guided filter 的 subsample 网格
  * 特定周期的尖峰   —— 拼块边界(周期 = tile 边长)

同时报**行/列投影的自相关**作为独立佐证:频域看到的周期,空间域必须也能看到。
两边不一致就说明其中一个是窗函数或 JPEG 块效应的假象 —— 8x8 的 JPEG 块本身
就在 r=0.25 上有能量,而且正好是轴向的,这是最容易骗人的一项。

用法::

    python aniso_freq.py <图A> <图B> ...
"""
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import numpy as np
from PIL import Image

sys.path.insert(0, "/home/jannchie/llr/sony_repro/tools")
from aniso import TILE, flat_tiles, spectrum  # noqa: E402

W601 = np.array([0.299, 0.587, 0.114], np.float32)
NTILE = 16
#: 环带边界,按归一化频率 r(1.0 = 奈奎斯特,即周期 2px)。
RINGS = [(0.10, 0.20), (0.20, 0.35), (0.35, 0.55), (0.55, 0.75), (0.75, 0.98)]


def ring_ratio(p, lo, hi):
    """一个环带里的 (轴向/对角, 竖纹, 横纹)。"""
    n0, n1 = p.shape
    ky = (np.arange(n0) - n0 // 2)[:, None] / (n0 / 2)
    kx = (np.arange(n1) - n1 // 2)[None, :] / (n1 / 2)
    r = np.hypot(ky, kx)
    band = (r >= lo) & (r < hi)
    ang = np.degrees(np.arctan2(ky, kx)) % 180.0
    near = lambda a, c: np.minimum(np.abs(a - c), 180 - np.abs(a - c)) <= 15.0  # noqa: E731
    eh = p[band & near(ang, 0.0)].mean()
    ev = p[band & near(ang, 90.0)].mean()
    ed = p[band & (near(ang, 45.0) | near(ang, 135.0))].mean()
    ed = max(ed, 1e-20)
    return (eh + ev) / 2 / ed, eh / ed, ev / ed


def profile_autocorr(y, tiles, axis, lags=12):
    """平坦块里,沿一个方向做投影后的自相关 —— 空间域的周期证据。

    axis=0 把每一**行**平均掉,留下沿 y 的一维信号,它的周期就是横纹的周期。
    先减掉块均值,再按 lag 0 归一化,所以读数是相关系数。
    """
    acc = np.zeros(lags + 1)
    for cy, cx in tiles:
        blk = y[cy:cy + TILE, cx:cx + TILE]
        sig = blk.mean(axis=1 - axis)
        sig = sig - sig.mean()
        denom = float(sig @ sig)
        if denom <= 0:
            continue
        acc += np.array([float(sig[:len(sig) - k] @ sig[k:]) / denom
                         for k in range(lags + 1)])
    return acc / max(len(tiles), 1)


def main():
    paths = [Path(a) for a in sys.argv[1:]]
    if not paths:
        raise SystemExit("给至少一个图像路径")
    ref_tiles = None
    hdr = "  ".join(f"{f'{2/hi:.1f}-{2/lo:.1f}px':>11}" for lo, hi in RINGS)
    print(f"  {'图':<24} {hdr}")
    store = {}
    for p in paths:
        y = np.asarray(Image.open(p).convert("RGB"), np.float32) @ W601
        if ref_tiles is None:
            ref_tiles = flat_tiles(y, n=NTILE)
        specs = [spectrum(y[cy:cy + TILE, cx:cx + TILE]) for cy, cx in ref_tiles]
        row = []
        for lo, hi in RINGS:
            a = np.array([ring_ratio(s, lo, hi) for s in specs])
            row.append(np.median(a, axis=0))
        store[p.name] = (y, row)
        cells = "  ".join(f"{r[0]:11.2f}" for r in row)
        print(f"  {p.name:<24} {cells}")

    print("\n  ^ 轴向/对角能量比,按周期分带。1.00 = 各向同性。")
    print("    JPEG 的 8x8 块效应落在 2.0-2.7px 与 5.7-8.0px 附近,两张图都有,"
          "所以要看的是**差值**不是绝对值。\n")

    print(f"  {'图':<24} {'竖纹(沿x)':>10} {'横纹(沿y)':>10}   最强的那一带")
    for name, (_y, row) in store.items():
        k = int(np.argmax([r[0] for r in row]))
        lo, hi = RINGS[k]
        print(f"  {name:<24} {row[k][1]:10.2f} {row[k][2]:10.2f}   "
              f"{2/hi:.1f}-{2/lo:.1f}px")

    print("\n  空间域自相关(平坦块投影,lag 1..8):")
    for name, (y, _row) in store.items():
        av = profile_autocorr(y, ref_tiles, axis=0)
        ah = profile_autocorr(y, ref_tiles, axis=1)
        f = lambda a: " ".join(f"{v:+.2f}" for v in a[1:9])  # noqa: E731
        print(f"  {name}")
        print(f"    沿 y(横纹): {f(av)}")
        print(f"    沿 x(竖纹): {f(ah)}")
    print("\n  lag=1 处显著为负 = 相邻像素反相 = 2px 周期的棋盘/条纹。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
