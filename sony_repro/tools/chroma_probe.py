r"""缺失的那一段是什么形式:在 YCbCr 色度平面上量。

引擎的 SSCS 跑在 RGB2YCC 之后,所以要在同一个空间比 —— 8-bit sRGB 转 YCbCr,
看色度半径的比值,以及它是否随亮度、色相、半径本身变化。恒定就意味着一个纯增益,
可以直接补上;有结构就得照结构复刻。
"""
import os
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sat_probe import load_jpeg, render  # noqa: E402

# BT.601,和 JPEG 自身用的一致
M = np.array([[0.299, 0.587, 0.114],
              [-0.168736, -0.331264, 0.5],
              [0.5, -0.418688, -0.081312]], dtype=np.float32)


def ycc(rgb8):
    a = rgb8.astype(np.float32) / 255
    y = a @ M[0]
    cb = a @ M[1]
    cr = a @ M[2]
    return y, cb, cr


def stats(name, vals, weights=None):
    print(f"  {name:22s} 中位 {np.median(vals):.4f}  p25 {np.percentile(vals,25):.4f}"
          f"  p75 {np.percentile(vals,75):.4f}")


def analyse(arw, style):
    arw = Path(arw)
    jpg = arw.with_suffix(".JPG")
    ours = render(arw, style)
    theirs = load_jpeg(jpg, ours.shape)

    y_o, cb_o, cr_o = ycc(ours)
    y_t, cb_t, cr_t = ycc(theirs)
    r_o = np.hypot(cb_o, cr_o)
    r_t = np.hypot(cb_t, cr_t)
    hue = np.arctan2(cr_o, cb_o)

    mask = (r_o > 0.02) & (y_o > 0.05) & (y_o < 0.92)
    ratio = r_t[mask] / r_o[mask]
    print(f"\n{arw.name} ({style})  参与 {mask.mean()*100:.0f}%")
    stats("色度半径比 全局", ratio)

    print("  按色度半径分箱:")
    for lo, hi in [(0.02, 0.05), (0.05, 0.10), (0.10, 0.15), (0.15, 0.22), (0.22, 0.35), (0.35, 1.0)]:
        b = mask & (r_o >= lo) & (r_o < hi)
        if b.sum() < 2000:
            continue
        print(f"    r∈[{lo:.2f},{hi:.2f})  n={b.sum():8d}  比 {np.median(r_t[b]/r_o[b]):.4f}")

    print("  按亮度分箱:")
    for lo, hi in [(0.05, 0.15), (0.15, 0.3), (0.3, 0.5), (0.5, 0.7), (0.7, 0.92)]:
        b = mask & (y_o >= lo) & (y_o < hi)
        if b.sum() < 2000:
            continue
        print(f"    Y∈[{lo:.2f},{hi:.2f})  n={b.sum():8d}  比 {np.median(r_t[b]/r_o[b]):.4f}")

    print("  按色相分箱(12 段, 0=品红方向 Cr+):")
    for k in range(12):
        lo, hi = -np.pi + k * np.pi / 6, -np.pi + (k + 1) * np.pi / 6
        b = mask & (hue >= lo) & (hue < hi)
        if b.sum() < 2000:
            continue
        print(f"    {int(np.degrees(lo)):+4d}°  n={b.sum():8d}  比 {np.median(r_t[b]/r_o[b]):.4f}")

    # 色相本身有没有被转动 —— 若有,就不是纯增益
    dh = np.degrees(np.arctan2(cr_t, cb_t) - hue)
    dh = (dh + 180) % 360 - 180
    print(f"  色相角变化 中位 {np.median(dh[mask]):+.2f}°  "
          f"p10 {np.percentile(dh[mask],10):+.1f}°  p90 {np.percentile(dh[mask],90):+.1f}°")
    return np.median(ratio)


if __name__ == "__main__":
    args = sys.argv[1:]
    pairs = [(args[i], args[i + 1]) for i in range(0, len(args), 2)]
    got = []
    for arw, style in pairs:
        got.append((Path(arw).name, style, analyse(arw, style)))
    if len(got) > 1:
        print("\n汇总:")
        for n, s, r in got:
            print(f"  {n:16s} {s:4s} {r:.4f}")
