r"""验证 YCC 段:引擎的 RGB2YCC 与 YCC2RGB 并不互逆,差额就是缺掉的饱和度。

正向取的色差是 R-G / B-G,先按符号做一次交叉耦合,再按符号各乘一个增益;回程却是
标准 BT.601 逆变换。八个参数逐外观不同,躺在 DataIFD 的 0x7842(基准)与
0x7843..0x7846(四组光源增量)里,按光源权重插值。

用法: python ycc_check.py <ARW> [style]
"""
import os
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, "/home/jannchie/llr/apps/worker/src")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sat_probe import load_jpeg, render  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sony_repro.sr2 import data_ifds  # noqa: E402

LOOK_ORDER = ("ST", "VV", "NT", "PT", "FL", "VV2", "IN", "SH", "BW", "SE")
CHROMA_BASE_TAG = 0x7842
CHROMA_ILLUM_TAGS = (0x7843, 0x7844, 0x7845, 0x7846)
FULL = 16383  # YCC2RGB 把结果钳在 14 位


def chroma_params(ifd, weights=(1024, 0, 0, 0)):
    """八个 short:前四个是交叉耦合,后四个是增益。权重 1024 = 1.0。"""
    base = np.asarray(ifd[CHROMA_BASE_TAG]).astype(np.int64)
    acc = np.zeros(8, dtype=np.int64)
    for tag, w in zip(CHROMA_ILLUM_TAGS, weights):
        if w:
            acc += np.asarray(ifd[tag]).astype(np.int64) * w
    return base + (acc >> 10)


def rgb_to_ycc(rgb, p, luma=(2432, 4864, 896)):
    """引擎的正向变换。rgb 是 14 位整数域的 float。"""
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    y = np.floor((r * luma[0] + g * luma[1] + b * luma[2])) // 8192

    cross = (p[:4] >> 2) / 256.0          # 9 位有符号,再 /256
    gain = ((p[4:] >> 3) & 0xFF) / 128.0  # 8 位无符号,/64 后还要 *0.5

    u = r - g   # 红色差
    v = b - g   # 蓝色差
    # 交叉项各自看对方的符号,而且用的都是未经修改的 u/v
    v2 = np.where(u >= 0, cross[1], cross[3]) * u + v
    u2 = np.where(v >= 0, cross[0], cross[2]) * v + u

    cr = np.where(u2 >= 0, gain[1], gain[3]) * u2
    cb = np.where(v2 >= 0, gain[0], gain[2]) * v2
    return y, np.clip(cb, -8192, 8191), np.clip(cr, -8192, 8191)


def ycc_to_rgb(y, cb, cr):
    """标准 BT.601,定点系数与引擎一致。"""
    r = (y * 10000 + cr * 14020) // 10000
    g = (y * 10000 - cr * 7141 - cb * 3441) // 10000
    b = (y * 10000 + cb * 17720) // 10000
    return np.clip(np.stack([r, g, b], -1), 0, FULL)


def srgb8(rgb14):
    return (np.clip(rgb14 / FULL, 0, 1) * 255).round().astype(np.uint8)


def chroma_stats(name, ours, theirs):
    """在 YCbCr 上比色度半径 —— 缺口本来就只在色度。"""
    m = np.array([[0.299, 0.587, 0.114], [-0.168736, -0.331264, 0.5],
                  [0.5, -0.418688, -0.081312]], dtype=np.float32)

    def ycc(x):
        a = x.astype(np.float32) / 255
        return a @ m[0], a @ m[1], a @ m[2]

    y_o, cb_o, cr_o = ycc(ours)
    y_t, cb_t, cr_t = ycc(theirs)
    r_o, r_t = np.hypot(cb_o, cr_o), np.hypot(cb_t, cr_t)
    sel = (r_o > 0.02) & (y_o > 0.06) & (y_o < 0.9)
    ratio = r_t[sel] / np.maximum(r_o[sel], 1e-6)
    dh = np.degrees(np.arctan2(cr_t, cb_t) - np.arctan2(cr_o, cb_o))
    dh = (dh + 180) % 360 - 180
    print(f"  {name:<12} 色度比 中位 {np.median(ratio):.4f}"
          f"  p25 {np.percentile(ratio, 25):.4f}  p75 {np.percentile(ratio, 75):.4f}"
          f"   色相移 {np.median(dh[sel]):+.2f}°"
          f"   亮度差 {np.median(y_t[sel] - y_o[sel]):+.4f}")


def main():
    arw = Path(sys.argv[1])
    style = sys.argv[2] if len(sys.argv) > 2 else "VV2"
    ifd = data_ifds(arw)[LOOK_ORDER.index(style)]
    p = chroma_params(ifd)
    print(f"{arw.name}  外观={style}")
    print(f"八参数 {p.tolist()}")
    print(f"  交叉耦合 {[round(float(c), 4) for c in (p[:4] >> 2) / 256.0]}")
    print(f"  色度增益 {[round(float(c), 4) for c in ((p[4:] >> 3) & 0xFF) / 128.0]}")

    before8 = render(arw, style)                       # 只到 MainGamma 为止
    rgb14 = before8.astype(np.float64) / 255.0 * FULL
    y, cb, cr = rgb_to_ycc(rgb14, p)
    after8 = srgb8(ycc_to_rgb(y, cb, cr))
    theirs = load_jpeg(arw.with_suffix(".JPG"), before8.shape)

    print("\n与机内 JPEG 相比(1.0 = 完全对上):")
    chroma_stats("加 YCC 段前", before8, theirs)
    chroma_stats("加 YCC 段后", after8, theirs)

    for name, img in (("before", before8), ("after", after8), ("engine", theirs)):
        print(f"  {name:<7} 平均 RGB {img.reshape(-1, 3).mean(0).round(1)}")


if __name__ == "__main__":
    main()
