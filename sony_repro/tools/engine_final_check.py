r"""整条管线对**引擎自己的最终画面**,而不是对机内 JPEG。

JPEG 只是代理:它是相机渲的,不是 Edit.exe 渲的,两者色度还差 2.6%。判断一次改动
到底有没有让我们更接近引擎,得拿引擎的成品比。`stage_frame.py ... ZcTaskSIMDMarble:out`
就能把它整幅拼出来(Marble 是管线最后一个阶段)。

用法: python engine_final_check.py <ARW> <frames.npz> [style]
"""
import os
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from e2e_pipeline import render  # noqa: E402
from ycc_check import chroma_stats  # noqa: E402

FULL = 16383


def to_grid(a, shape):
    sy, sx = a.shape[0] / shape[0], a.shape[1] / shape[1]
    yi = np.clip((np.arange(shape[0]) * sy).astype(int), 0, a.shape[0] - 1)
    xi = np.clip((np.arange(shape[1]) * sx).astype(int), 0, a.shape[1] - 1)
    return a[np.ix_(yi, xi)]


def align(ours, eng8, shape):
    """竖幅的图 rawpy 按 EXIF 方向出片,引擎的画布是横的 —— 按相关性挑 rot90。"""
    ref = eng8[..., 1].astype(np.float64)
    ref = ref - ref.mean()
    best = None
    for k in range(4):
        cand = np.rot90(ours, k)
        if (cand.shape[0] > cand.shape[1]) != (shape[0] > shape[1]):
            continue
        g = to_grid(cand, shape)[..., 1].astype(np.float64)
        score = float((ref * (g - g.mean())).mean())
        if best is None or score > best[0]:
            best = (score, k, cand)
    return best[2], best[1]


def main():
    arw = Path(sys.argv[1])
    z = np.load(sys.argv[2])
    style = sys.argv[3] if len(sys.argv) > 3 else None
    key = next(k for k in z.files if k.endswith("_out") or k.endswith("_in"))
    eng = z[key]
    ok = eng.max(-1) > 0
    eng8 = np.clip(eng / FULL, 0, 1) * 255

    print("引擎帧 %s (%s)" % (eng.shape, key))
    for label, with_chroma in (("矩阵+曲线", False), ("再加 RGB2YCC", True)):
        ours, _cp = render(arw, "sony", with_chroma=with_chroma)
        ours = (ours * 255).astype(np.float32)
        rot, k = align(ours, eng8, eng.shape[:2])
        chroma_stats(label, to_grid(rot, eng.shape[:2])[ok], eng8[ok])
        if label == "矩阵+曲线":
            print("       (rot90 k=%d,%s)" % (k, " 外观 " + (style or "?")))
    print("\n  引擎最终 平均 RGB %s" % eng8[ok].reshape(-1, 3).mean(0).round(1))


if __name__ == "__main__":
    main()
