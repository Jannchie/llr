r"""YCC 段之后到成品之间,色度到底被压了多少?是哪一段压的?

PIPELINE.md 的「被推翻结论 #11」说「YCC 段之后还少一步把色度压两成」是假问题。
那条结论针对的是**当时的判断依据**(拿整幅复刻比机内 JPEG),不是针对引擎本身。
这里只用引擎自己的前后两份,**同一次运行**抓下来,所以对齐和设置都不可能有差。

用法: 先 stage_frame.py 抓 ZcTaskYCC2RGB:out ZcTaskSIMDITP:out ZcTaskSIMDMarble:out
      再 python backhalf_chroma.py
"""
import sys
from pathlib import Path

import numpy as np

W = np.array([0.299, 0.587, 0.114])
ORDER = ["ZcTaskYCC2RGB_out", "ZcTaskSIMDITP_out", "ZcTaskSSCS_out",
         "ZcTaskAreaCompSIMD_out", "ZcTaskSIMDSharpness_out",
         "ZcTaskSIMDSpica_out", "ZcTaskSIMDMarble_out"]


def stats(x):
    y = x @ W
    return y, np.hypot(x[..., 0] - y, x[..., 2] - y)


def main():
    z = np.load(sys.argv[1] if len(sys.argv) > 1 else Path(__file__).parent / "stage_frames.npz")
    have = [k for k in ORDER if k in z.files]
    frames = {k: z[k].astype(np.float64) for k in have}
    ok = np.logical_and.reduce([f.max(-1) > 0 for f in frames.values()])
    print(f"重叠像素 {ok.sum()}\n")

    prev = None
    for k in have:
        y, c = stats(frames[k][ok])
        line = f"{k:<24} 亮度 {y.mean():8.1f}  色度中位 {np.median(c):8.1f}"
        if prev is not None:
            py, pc = prev
            m = pc > 200
            line += (f"   相对上一段: 亮度 x{np.median(y[py > 200] / py[py > 200]):.4f}"
                     f"  色度 x{np.median(c[m] / pc[m]):.4f}")
        print(line)
        prev = (y, c)


if __name__ == "__main__":
    main()
