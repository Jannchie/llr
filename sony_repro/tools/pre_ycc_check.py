r"""误差在 YCC 段之前还是之后?拿引擎自己的 MainGamma 出口当靶子。

`ycc_exact.py` 已经证明 YCC 段逐像素正确(Y 100% 相同,色度差 ≤1),所以 §7.6 那个
「色度过两成」只能出在**喂给它的那份数据**上。引擎的 MainGamma 出口现在能整幅拼出来,
直接和我们的「矩阵 + 曲线」比就行 —— 不用再跟机内 JPEG 绕。

用法: python pre_ycc_check.py <ARW> <ycc_frames.npz> [style]
"""
import os
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sat_probe import load_jpeg, render  # noqa: E402
from ycc_check import chroma_stats  # noqa: E402

FULL = 16383


def main():
    arw = Path(sys.argv[1])
    z = np.load(sys.argv[2])
    style = sys.argv[3] if len(sys.argv) > 3 else "VV2"

    eng = z["ZcTaskRGB2YCC_in"]          # = MainGamma 的出口
    ok = eng.max(-1) > 0
    eng8 = np.clip(eng / FULL, 0, 1) * 255

    ours = render(arw, style, half=False).astype(np.float32)
    print("我们 %s   引擎(1/4) %s" % (ours.shape, eng.shape))

    # 把我们的降到引擎帧的网格上,两边都是全分辨率出来的,直接按比例取点
    sy, sx = ours.shape[0] / eng.shape[0], ours.shape[1] / eng.shape[1]
    yi = np.clip((np.arange(eng.shape[0]) * sy).astype(int), 0, ours.shape[0] - 1)
    xi = np.clip((np.arange(eng.shape[1]) * sx).astype(int), 0, ours.shape[1] - 1)
    ours8 = ours[np.ix_(yi, xi)]

    jpg = load_jpeg(arw.with_suffix(".JPG"), eng.shape).astype(np.float32)

    print("\n以引擎的 MainGamma 出口为基准(1.0 = 完全对上):")
    chroma_stats("我们矩阵+曲线", ours8[ok], eng8[ok])
    print("\n以机内 JPEG 为基准:")
    chroma_stats("引擎 MainGamma", eng8[ok], jpg[ok])
    chroma_stats("我们矩阵+曲线", ours8[ok], jpg[ok])
    print()
    for name, a in (("我们矩阵+曲线", ours8), ("引擎 MainGamma", eng8), ("机内 JPEG", jpg)):
        print("  %-14s 平均 RGB %s" % (name, a[ok].reshape(-1, 3).mean(0).round(1)))


if __name__ == "__main__":
    main()
