r"""再切一刀:10% 的色度差与 7.6° 的色相差,是矩阵造成的还是曲线造成的?

`pre_ycc_check.py` 把误差定位到了「矩阵 + 曲线」这一段。现在把 LinearMatrix16 的
入口与出口也拼出来:入口是白平衡后的相机原色(线性),出口是矩阵之后(仍线性),
再往后一步就是 MainGamma。三份都有,就能把两个环节各自的贡献分开。

用法: python matrix_check.py <ARW> <matrix_frames.npz> [style]
"""
import os
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ycc_check import chroma_stats  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "apps/worker/src"))
from llr_worker.sony.linear_matrix import SegmentedMatrix  # noqa: E402
from llr_worker.sony.sr2 import look_calibrations, unpack_param_block  # noqa: E402
from llr_worker.sony.tone import LOOK_ORDER, tone_curve  # noqa: E402

FULL = 16383
WHITE = 8192   # 线性域的白点(曲线 LUT 的 x/128)


def main():
    arw = Path(sys.argv[1])
    z = np.load(sys.argv[2])
    style = sys.argv[3] if len(sys.argv) > 3 else "VV2"
    cal = look_calibrations(arw)[LOOK_ORDER.index(style)]

    src = z["ZcTaskSIMDLinearMatrix16_in"].astype(np.float64)
    mid = z["ZcTaskSIMDLinearMatrix16_out"].astype(np.float64)
    dst = z["ZcTaskMainGamma_out"].astype(np.float64)
    ok = (src.max(-1) > 0) & (mid.max(-1) > 0)
    print("有效像素 %d,  入口 max %d  出口 max %d" % (ok.sum(), src.max(), mid.max()))
    print("入口均值 %s  出口均值 %s" % (src[ok].mean(0).round(1), mid[ok].mean(0).round(1)))

    # 我们的矩阵作用在同一批入口像素上 —— 白平衡、去马赛克全部由引擎提供
    m = SegmentedMatrix(unpack_param_block(cal.param_block))
    ours = m.apply((src[ok] / WHITE).astype(np.float32)) * WHITE
    theirs = mid[ok]
    d = ours - theirs
    print("\n矩阵(喂同一批入口像素):")
    print("  |差| 中位 %.2f  p99 %.1f  最大 %.1f    我们均值 %s"
          % (np.median(np.abs(d)), np.percentile(np.abs(d), 99), np.abs(d).max(),
             ours.mean(0).round(1)))
    # 线性域直接比绿色差 —— 这一段值还很小,转 8-bit 比色度会被阈值筛没
    for lbl, i in (("R-G", 0), ("B-G", 2)):
        a, b = ours[:, i] - ours[:, 1], theirs[:, i] - theirs[:, 1]
        sel = np.abs(b) > 5
        print("  %s  中位比 引擎/我们 = %.4f   我们均值 %+.1f  引擎均值 %+.1f"
              % (lbl, np.median(b[sel] / a[sel]), a.mean(), b.mean()))

    # 曲线作用在引擎自己的矩阵出口上,和引擎的 MainGamma 出口比。
    # 白点是 8192 不是 16383(见 PIPELINE §4:曲线的 x/128 是 LUT 索引,白点 8192)
    lut = tone_curve(cal, style)
    x = np.linspace(0.0, 1.0, lut.size)
    toned = np.stack([np.interp(np.clip(mid[ok][:, c] / WHITE, 0, 1), x, lut)
                      for c in range(3)], -1) * FULL
    e = toned - dst[ok]
    print("\n曲线(喂引擎自己的矩阵出口):")
    print("  |差| 中位 %.1f  p99 %.1f  最大 %.1f    我们均值 %s  引擎均值 %s"
          % (np.median(np.abs(e)), np.percentile(np.abs(e), 99), np.abs(e).max(),
             toned.mean(0).round(1), dst[ok].mean(0).round(1)))
    chroma_stats("曲线出口", np.clip(toned / FULL, 0, 1) * 255,
                 np.clip(dst[ok] / FULL, 0, 1) * 255)


if __name__ == "__main__":
    main()
