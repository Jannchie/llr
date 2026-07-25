r"""多张图合并统计:缺失那一段在色度平面上的形状。

单张图的色相分布太偏,合起来才能覆盖整圈。只用 DRO 关、微调全零的样张,
否则未复刻的 DRO 会混进来。
"""
import os
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from chroma_probe import ycc  # noqa: E402
from sat_probe import load_jpeg, render  # noqa: E402

N_HUE = 24
N_RAD = 6
RAD_EDGES = np.array([0.02, 0.05, 0.09, 0.14, 0.20, 0.28, 1.0])


def collect(paths, style):
    acc = {"hue_num": np.zeros(N_HUE), "hue_den": np.zeros(N_HUE), "hue_dh": [[] for _ in range(N_HUE)],
           "rad_num": np.zeros(N_RAD), "rad_den": np.zeros(N_RAD),
           "lum_num": np.zeros(5), "lum_den": np.zeros(5)}
    for p in paths:
        p = Path(p)
        ours = render(p, style)
        theirs = load_jpeg(p.with_suffix(".JPG"), ours.shape)
        y_o, cb_o, cr_o = ycc(ours)
        y_t, cb_t, cr_t = ycc(theirs)
        r_o, r_t = np.hypot(cb_o, cr_o), np.hypot(cb_t, cr_t)
        h_o = np.arctan2(cr_o, cb_o)
        m = (r_o > 0.02) & (y_o > 0.06) & (y_o < 0.9)

        hb = np.clip(((h_o + np.pi) / (2 * np.pi) * N_HUE).astype(int), 0, N_HUE - 1)
        rb = np.clip(np.searchsorted(RAD_EDGES, r_o, "right") - 1, 0, N_RAD - 1)
        lb = np.clip(((y_o - 0.06) / (0.9 - 0.06) * 5).astype(int), 0, 4)
        ratio = r_t / np.maximum(r_o, 1e-6)
        dh = np.degrees(np.arctan2(cr_t, cb_t) - h_o)
        dh = (dh + 180) % 360 - 180

        for arr, num, den in ((hb, "hue_num", "hue_den"), (rb, "rad_num", "rad_den"), (lb, "lum_num", "lum_den")):
            np.add.at(acc[num], arr[m], ratio[m])
            np.add.at(acc[den], arr[m], 1)
        for k in range(N_HUE):
            sel = m & (hb == k)
            if sel.sum():
                acc["hue_dh"][k].append(np.median(dh[sel]))
        print(f"  {p.name} done", flush=True)
    return acc


def main():
    style = sys.argv[1]
    paths = sys.argv[2:]
    acc = collect(paths, style)

    print(f"\n=== {style}, {len(paths)} 张合并 ===")
    print("色相 -> 色度增益 (0° = Cb+ 方向, 逆时针):")
    for k in range(N_HUE):
        if acc["hue_den"][k] < 5000:
            continue
        deg = -180 + (k + 0.5) * 360 / N_HUE
        dh = np.median(acc["hue_dh"][k]) if acc["hue_dh"][k] else float("nan")
        print(f"  {deg:+6.1f}°  n={int(acc['hue_den'][k]):8d}  增益 {acc['hue_num'][k]/acc['hue_den'][k]:.4f}"
              f"  色相移 {dh:+6.2f}°")

    print("\n色度半径 -> 增益:")
    for k in range(N_RAD):
        if acc["rad_den"][k] < 5000:
            continue
        print(f"  r∈[{RAD_EDGES[k]:.2f},{RAD_EDGES[k+1]:.2f})  n={int(acc['rad_den'][k]):8d}"
              f"  增益 {acc['rad_num'][k]/acc['rad_den'][k]:.4f}")

    print("\n亮度 -> 增益:")
    for k in range(5):
        if acc["lum_den"][k] < 5000:
            continue
        print(f"  箱{k}  n={int(acc['lum_den'][k]):8d}  增益 {acc['lum_num'][k]/acc['lum_den'][k]:.4f}")


if __name__ == "__main__":
    main()
