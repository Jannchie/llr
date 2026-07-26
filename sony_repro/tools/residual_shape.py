r"""颜色的中位数都对上了,剩下的低频残差长什么样?按**色调**和**半径**拆。

engine_tally.py 给出:模糊后 RMSE 仍有 6.8/255(中位),而色度比 0.998、色相 0.14 度。
中位数对上不等于处处对上 —— 残差可能集中在某个色调区间(曲线两端)或某个半径
(暗角/镜头阴影)。这两种成因要用不同办法修,所以先分开。

用法: python residual_shape.py DSC02995 DSC03022 ...
"""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
import e2e_pipeline as E  # noqa: E402
from engine_final_check import align, to_grid  # noqa: E402

W = np.array([0.299, 0.587, 0.114])
FULL = 16383
TMP = Path("/home/jannchie/llr/tmp")
SRC = Path("/mnt/e/10960725")


def main():
    for stem in sys.argv[1:]:
        eng = np.load(TMP / f"final_{stem}.npz")["ZcTaskSIMDMarble_out"]
        ok = eng.max(-1) > 0
        b = np.clip(eng / FULL, 0, 1)
        ours, _ = E.render(SRC / f"{stem}.ARW", "sony")
        rot, _ = align((ours * 255).astype(np.float32), b * 255, eng.shape[:2])
        a = to_grid(rot, eng.shape[:2]) / 255.0

        ya, yb = a @ W, b @ W
        d = (yb - ya)[ok]
        print(f"\n=== {stem}  亮度残差(引擎 - 我们),{ok.sum()} 像素")
        print("  按色调:")
        for lo, hi in ((0, .05), (.05, .15), (.15, .3), (.3, .5), (.5, .7), (.7, .85), (.85, 1)):
            m = (ya[ok] >= lo) & (ya[ok] < hi)
            if m.sum() > 2000:
                print(f"    Y {lo:.2f}..{hi:.2f}  n={m.sum():>8}  均值 {d[m].mean():+.4f}"
                      f"  绝对中位 {np.median(np.abs(d[m])):.4f}")

        h, w = ya.shape
        yy, xx = np.mgrid[0:h, 0:w]
        rad = np.hypot((yy - h / 2) / (h / 2), (xx - w / 2) / (w / 2))[ok]
        rad = rad / rad.max()
        print("  按半径(1.0 = 画幅角):")
        for lo, hi in ((0, .25), (.25, .5), (.5, .7), (.7, .85), (.85, 1.01)):
            m = (rad >= lo) & (rad < hi)
            if m.sum() > 2000:
                print(f"    r {lo:.2f}..{hi:.2f}  n={m.sum():>8}  均值 {d[m].mean():+.4f}")


if __name__ == "__main__":
    main()
