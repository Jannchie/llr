r"""拿引擎自己的中间帧当靶子,验证复刻到底差在哪一步。

sscs_frame.py 抓下来的是 SSCS 入口处的三个平面 —— 那时 LinearMatrix16、MainGamma、
RGB2YCC、ChromaSuppres、YGamma、YCC2RGB、ITP 都已经跑完,而 SSCS 实测触发率为 0,
AreaComp 与锐化还没动手。比 JPEG 干净:没有压缩,也没有缩放插值。

用法: python frame_check.py <ARW> <sscs_frame.npz> [style]
"""
import os
import sys
from pathlib import Path

import numpy as np
from PIL import Image

sys.path.insert(0, "/home/jannchie/llr/apps/worker/src")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sat_probe import render  # noqa: E402
from ycc_check import chroma_params, rgb_to_ycc, ycc_to_rgb  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sony_repro.sr2 import data_ifds  # noqa: E402

LOOK_ORDER = ("ST", "VV", "NT", "PT", "FL", "VV2", "IN", "SH", "BW", "SE")
FULL = 16383


def resize(img8, w, h):
    return np.asarray(Image.fromarray(img8).resize((w, h), Image.LANCZOS))


def compare(name, ours, ref):
    d = ours.astype(float) - ref.astype(float)
    print(f"  {name:<16} 平均 {ours.reshape(-1, 3).mean(0).round(1)}"
          f"   偏差 {d.reshape(-1, 3).mean(0).round(2)}"
          f"   |偏差| 中位 {np.median(np.abs(d)):.2f}  RMSE {np.sqrt((d ** 2).mean()):.2f}")


def main():
    arw = Path(sys.argv[1])
    frame = np.load(sys.argv[2])
    style = sys.argv[3] if len(sys.argv) > 3 else "VV2"

    ref = np.stack([frame["p0"], frame["p1"], frame["p2"]], -1)
    h, w = ref.shape[:2]
    print(f"引擎帧 {w}x{h}  值域 {ref.min()}..{ref.max()}  中位 {int(np.median(ref))}")

    before = resize(render(arw, style, half=True), w, h)
    p = chroma_params(data_ifds(arw)[LOOK_ORDER.index(style)])
    rgb14 = before.astype(np.float64) / 255.0 * FULL
    y, cb, cr = rgb_to_ycc(rgb14, p)
    after = (np.clip(ycc_to_rgb(y, cb, cr) / FULL, 0, 1) * 255).round().astype(np.uint8)

    # 引擎那一层的量程未知,先看它与我们的 8-bit 输出是不是同一条直线上的
    for label, img in (("矩阵+曲线", before), ("再加 YCC 段", after)):
        a = img.reshape(-1, 3).astype(float)
        b = ref.reshape(-1, 3).astype(float)
        k = (a * b).sum() / (a * a).sum()
        print(f"  {label:<12} 最佳缩放 k={k:.4f}  相关 {np.corrcoef(a.ravel(), b.ravel())[0,1]:.5f}")

    scale = ref.max() / 255.0
    print(f"\n按 {scale:.4f} 把我们的输出缩到引擎那一层的量程后:")
    compare("矩阵+曲线", (before * scale).round(), ref)
    compare("再加 YCC 段", (after * scale).round(), ref)


if __name__ == "__main__":
    main()
