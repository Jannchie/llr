"""逐级量亮度细节与色度细节 —— ×21.8 的亮度放大发生在哪一级。

⚠️ **平面语义中途会变。** ITP / SSCS 出口是三个 RGB 样的平面(均值都远小于 16383);
AreaComp 往后 plane1/plane2 的均值在 32768 附近,那是 `plane0=Y, plane1=Cr,
plane2=Cb` 的 YCC 布局(中点 0x8000)。一律按「plane1 是基底」去算 plane0−plane1,
在 YCC 阶段量到的是「Y 减 Cr」,是废数 —— 第一版脚本就是这么错的。
这里按均值自动判别布局。
"""
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import numpy as np

Z = Path("/home/jannchie/llr/sony_repro/tools/stage_frames.npz")
TILE = 32
ORDER = ["ZcTaskSIMDITP_out", "ZcTaskSSCS_out", "ZcTaskAreaCompSIMD_out",
         "ZcTaskSIMDSharpness_out", "ZcTaskSIMDSpica_out", "ZcTaskSIMDMarble_out"]


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
    if not len(ys):
        return []
    idx = np.linspace(0, len(ys) - 1, min(cap, len(ys))).astype(int)
    return [(int(ys[k]) * TILE, int(xs[k]) * TILE) for k in idx]


def main():
    z = np.load(Z)
    keys = [k for k in ORDER if k in z.files] + \
           [k for k in z.files if k not in ORDER and k != "step"]
    print(f"{'阶段':>26} {'布局':>5} {'亮度细节':>9} {'色度A':>9} {'色度B':>9}"
          f"  {'色度/亮度':>9}")
    print("-" * 78)
    prev = None
    for key in keys:
        a = z[key].astype(np.float64)
        if (a[..., 1] > 0).mean() < 0.1:
            continue
        m1, m2 = a[..., 1].mean(), a[..., 2].mean()
        ycc = m1 > 20000 and m2 > 20000
        if ycc:
            luma, ca, cb = a[..., 0], a[..., 1] - 32768.0, a[..., 2] - 32768.0
        else:
            # RGB 样:亮度取 Rec.601 加权,色度取两条色差 —— 与成品的量法一致
            luma = a @ np.array([0.299, 0.587, 0.114])
            ca, cb = a[..., 0] - a[..., 1], a[..., 2] - a[..., 1]
        ly, la, lb = [], [], []
        for y, x in flat_tiles(luma):
            s = (slice(y, y + TILE), slice(x, x + TILE))
            ly.append(mad(detail(luma[s])))
            la.append(mad(detail(ca[s])))
            lb.append(mad(detail(cb[s])))
        my, ma, mb = np.median(ly), np.median(la), np.median(lb)
        ratio = (ma + mb) / 2 / max(my, 1e-9)
        tag = "YCC" if ycc else "RGB"
        line = (f"{key:>26} {tag:>5} {my:9.3f} {ma:9.3f} {mb:9.3f}  {ratio:9.3f}")
        if prev is not None:
            line += (f"   相对上一级 亮度×{my / max(prev[0], 1e-9):6.2f}"
                     f" 色度×{(ma + mb) / max(prev[1] + prev[2], 1e-9):6.2f}")
        print(line)
        prev = (my, ma, mb)
    print("\n⚠️ 跨 RGB↔YCC 边界的那一格倍数含表示法变化,不是纯粹的增益。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
