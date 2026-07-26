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

    def to_grid(a, shape):
        """按比例取点降到引擎帧的网格上(两边都是全分辨率出来的)。"""
        sy, sx = a.shape[0] / shape[0], a.shape[1] / shape[1]
        yi = np.clip((np.arange(shape[0]) * sy).astype(int), 0, a.shape[0] - 1)
        xi = np.clip((np.arange(shape[1]) * sx).astype(int), 0, a.shape[1] - 1)
        return a[np.ix_(yi, xi)]

    # 竖幅的图 rawpy 会按 EXIF 方向出片,而 stage_frame.py 的画布写死是横的。
    # 转错方向比不转还糟(错位对比会把色相差算成 30 度),所以按相关性挑,别猜。
    ref = eng8[..., 1].astype(np.float64)
    ref = ref - ref.mean()
    best = None
    for k in range(4):
        cand = np.rot90(ours, k)
        if (cand.shape[0] > cand.shape[1]) != (eng.shape[0] > eng.shape[1]):
            continue
        g = to_grid(cand, eng.shape[:2])[..., 1].astype(np.float64)
        score = float((ref * (g - g.mean())).mean())
        if best is None or score > best[0]:
            best = (score, k, cand)
    ours = best[2]
    print("  取 rot90 k=%d(相关性 %.1f)-> %s" % (best[1], best[0], ours.shape))

    ours8 = to_grid(ours, eng.shape[:2])
    # 机内 JPEG 先按我们的方向缩放,再走同一个网格 —— 直接缩到引擎帧会把竖幅压扁
    jpg = to_grid(load_jpeg(arw.with_suffix(".JPG"), ours.shape[:2]).astype(np.float32),
                  eng.shape[:2])

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
