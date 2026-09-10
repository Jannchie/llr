"""量「横纹竖纹」:噪声的能量在方向上是不是均匀的。

起因是肉眼观察 —— llr 的噪点"有纹理感,像爬满了横纹和竖纹"。这件事之前所有的
指标都测不到:MAD、std、逐带 σ、相关系数,全是**各向同性**的统计量,把能量在
方向上怎么分布这一维直接积掉了。所以"llr 比 Edit 多 30% 噪声"这种说法,和
"llr 的噪声长得像格纹"是两回事,后者才是看得见的那个。

做法:在平坦 tile 上取方块,加窗后做 2D FFT,把功率谱按**角度**分箱。各向同性的
噪声在各个角度上应当等量;横纹让能量堆在 ky 轴(即 kx≈0),竖纹堆在 kx 轴。
报出「轴向 / 对角」的能量比 —— 1.0 是各向同性,明显大于 1 就是格纹。

同时报**可分离小波的嫌疑**:轴向能量里,水平与垂直是否同时偏高。可分离变换
(先行后列)的 LH / HL 子带正好就是横纹与竖纹,两者会一起抬起来;而传感器的
行噪声只抬一个方向。

用法::

    python aniso.py <图A> <图B> ...        # 直接比几张导出的图
"""
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import numpy as np
from PIL import Image

TILE = 256          # 分析窗;要够大才有频率分辨率
NTILE = 12          # 取多少块平坦区
W601 = np.array([0.299, 0.587, 0.114], np.float32)


def load(path):
    a = np.asarray(Image.open(path).convert("RGB"), np.float32)
    return a


def flat_tiles(y, n=NTILE, tile=TILE):
    """挑方差最低的若干块,且互不重叠 —— 噪声要在平坦处看。"""
    h, w = y.shape
    step = tile // 2
    cand = []
    for cy in range(0, h - tile, step):
        for cx in range(0, w - tile, step):
            blk = y[cy:cy + tile, cx:cx + tile]
            cand.append((float(blk.std()), cy, cx))
    cand.sort()
    out, used = [], []
    for _s, cy, cx in cand:
        if any(abs(cy - uy) < tile and abs(cx - ux) < tile for uy, ux in used):
            continue
        used.append((cy, cx))
        out.append((cy, cx))
        if len(out) >= n:
            break
    return out


def spectrum(block):
    """加窗功率谱,去掉直流,归一化到总能量 1。"""
    b = block - block.mean()
    win = np.outer(np.hanning(b.shape[0]), np.hanning(b.shape[1]))
    p = np.abs(np.fft.fftshift(np.fft.fft2(b * win))) ** 2
    p[p.shape[0] // 2, p.shape[1] // 2] = 0.0
    total = p.sum()
    return p / total if total > 0 else p


def directional(p, lo=0.15, hi=0.85):
    """(轴向/对角 能量比, 水平轴能量, 垂直轴能量)。

    只统计中高频环带:低频是画面结构,最高频是 JPEG 的块效应,都不是要找的东西。
    角度按 ±15° 取轴向(0°、90°)与对角(45°、135°)两组,面积相同所以可直接比。
    """
    n0, n1 = p.shape
    ky = (np.arange(n0) - n0 // 2)[:, None] / (n0 / 2)
    kx = (np.arange(n1) - n1 // 2)[None, :] / (n1 / 2)
    r = np.hypot(ky, kx)
    band = (r >= lo) & (r <= hi)
    ang = np.degrees(np.arctan2(ky, kx)) % 180.0
    near = lambda a, c: np.minimum(np.abs(a - c), 180 - np.abs(a - c)) <= 15.0  # noqa: E731
    horiz = band & near(ang, 0.0)     # kx 轴 -> 沿 x 变化快 -> **竖**纹
    vert = band & near(ang, 90.0)     # ky 轴 -> 沿 y 变化快 -> **横**纹
    diag = band & (near(ang, 45.0) | near(ang, 135.0))
    eh, ev = p[horiz].mean(), p[vert].mean()
    ed = p[diag].mean()
    return float((eh + ev) / 2 / max(ed, 1e-20)), float(eh / max(ed, 1e-20)), \
        float(ev / max(ed, 1e-20))


def main():
    paths = [Path(a) for a in sys.argv[1:]]
    if not paths:
        raise SystemExit("给至少一个图像路径")
    ref_tiles = None
    print(f"  {'图':<26} {'轴向/对角':>9} {'竖纹':>7} {'横纹':>7}   判读")
    for p in paths:
        rgb = load(p)
        y = rgb @ W601
        # 用第一张的 tile 位置,保证几张图量的是同一块画面。
        if ref_tiles is None:
            ref_tiles = flat_tiles(y)
        ratios = []
        for cy, cx in ref_tiles:
            sp = spectrum(y[cy:cy + TILE, cx:cx + TILE])
            ratios.append(directional(sp))
        a = np.array(ratios)
        ax, eh, ev = np.median(a, axis=0)
        note = ("各向同性" if ax < 1.15 else
                ("轴向偏高" if ax < 1.5 else "**明显格纹**"))
        if ax >= 1.15 and min(eh, ev) > 1.1:
            note += ",水平垂直同时抬起(可分离变换的签名)"
        print(f"  {p.name:<26} {ax:9.2f} {eh:7.2f} {ev:7.2f}   {note}")
    print(f"\n  在 {len(ref_tiles)} 块最平坦的 {TILE}x{TILE} 上量,"
          f"同一批位置。1.00 = 噪声在各方向等量。")
    print("  竖纹 = 沿 x 变化快(kx 轴);横纹 = 沿 y 变化快(ky 轴)。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
