"""格纹在画面上**怎么分布** —— 按到中心的半径分箱。

这是一个判别性很强的读数,因为几个候选来源的空间分布完全不同:

  * **镜头畸变 warp 的重采样** —— 位移在中心恒为 0(warp 退化成直通,双线性
    不插值),越往边角位移越大、局部缩放率偏离越多。所以伪影必须**中心弱、
    边角强**,而且是单调的。
  * 去马赛克、降噪、锐化 —— 都是位置无关的算子,分布应当**平坦**。
  * 拼块边界 —— 出现在固定的几条线上,不随半径单调。

对照的是同一张片的直出:直出在相机里也做了畸变校正,但那是在别处做的,所以
两条曲线的**形状差**才是要看的东西,不是各自的绝对值 —— 画面本身的内容(角落
往往更暗、噪声相对更大)也会随半径变,那份变化两张图共有,做差就消掉了。

用法::

    python aniso_map.py <llr图> <参照图>
"""
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import numpy as np
from PIL import Image

sys.path.insert(0, "/home/jannchie/llr/sony_repro/tools")
from aniso import TILE, directional, spectrum  # noqa: E402

W601 = np.array([0.299, 0.587, 0.114], np.float32)
NBIN = 5
#: 每个半径箱里取多少块最平坦的。太少则噪声大,太多会吃进结构区。
PER_BIN = 14


def grid_tiles(y, tile=TILE, stride=None):
    """整幅铺满的 tile 网格及其归一化半径与标准差。"""
    stride = stride or tile
    h, w = y.shape
    cy0, cx0 = h / 2.0, w / 2.0
    half = np.hypot(cy0, cx0)
    out = []
    for cy in range(0, h - tile, stride):
        for cx in range(0, w - tile, stride):
            blk = y[cy:cy + tile, cx:cx + tile]
            r = np.hypot(cy + tile / 2 - cy0, cx + tile / 2 - cx0) / half
            out.append((r, float(blk.std()), cy, cx))
    return out


def pick(tiles, nbin=NBIN, per=PER_BIN):
    """每个半径箱里挑最平坦的若干块,返回 {箱号: [(cy,cx), ...]}。

    按箱内分位数挑而不是全局挑 —— 全局挑会把所有 tile 都挑在天空所在的那圈
    半径上,那样半径这一维就没有对比了。
    """
    edges = np.linspace(0, 1.0, nbin + 1)
    sel = {}
    for b in range(nbin):
        inb = [t for t in tiles if edges[b] <= t[0] < edges[b + 1]]
        inb.sort(key=lambda t: t[1])
        sel[b] = [(cy, cx) for _r, _s, cy, cx in inb[:per]]
    return edges, sel


def main():
    paths = [Path(a) for a in sys.argv[1:]]
    if len(paths) < 1:
        raise SystemExit("给至少一个图像路径")

    ys = {}
    for p in paths:
        ys[p.name] = np.asarray(Image.open(p).convert("RGB"), np.float32) @ W601

    first = ys[paths[0].name]
    edges, sel = pick(grid_tiles(first, stride=TILE * 2))

    hdr = "  ".join(f"{f'{edges[b]:.1f}-{edges[b+1]:.1f}':>10}" for b in range(NBIN))
    print(f"  {'图':<24} {hdr}")
    rows = {}
    for name, y in ys.items():
        vals = []
        for b in range(NBIN):
            t = sel[b]
            if not t:
                vals.append(np.nan)
                continue
            a = np.array([directional(spectrum(y[cy:cy + TILE, cx:cx + TILE]))
                          for cy, cx in t])
            vals.append(float(np.median(a[:, 0])))
        rows[name] = vals
        cells = "  ".join(f"{v:10.2f}" for v in vals)
        print(f"  {name:<24} {cells}")

    if len(rows) >= 2:
        a, b = list(rows.values())[:2]
        d = [x - y for x, y in zip(a, b)]
        cells = "  ".join(f"{v:10.2f}" for v in d)
        print(f"  {'差(第一张 - 第二张)':<24} {cells}")
        finite = [v for v in d if np.isfinite(v)]
        if len(finite) >= 2:
            trend = finite[-1] - finite[0]
            print(f"\n  边缘 - 中心 = {trend:+.2f}")
            print("  明显为正(>0.15)= 越往边角格纹越重 -> 指向 warp 的重采样;")
            print("  基本持平       = 位置无关的算子 -> 指向降噪/锐化/去马赛克。")
    print(f"\n  x 轴是到画面中心的归一化半径(1.0 = 角)。每箱取该箱内最平坦的 "
          f"{PER_BIN} 块 {TILE}x{TILE}。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
