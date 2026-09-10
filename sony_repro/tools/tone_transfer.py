"""llr 与 Edit 之间是不是差一条**色调曲线** —— 有曲线就能校,没曲线才是空间问题。

`llr_vs_edit.py` 量到成品差 |Δ| 均值 0.0486,高光段 −0.113、暗部 +0.021,而两边的
DRO / Creative Style / Contrast / WB 已核对一致。这个形状有两种可能:

  * **逐点的色调映射不同** —— 那么给定 llr 的值就能预测 Edit 的值,散点会收成一条
    窄带,转移曲线一画就出来,而且可以直接校正;
  * **空间/局部处理不同**(局部对比、局部色调、demosaic 的差别)—— 那么同一个输入
    值会散到很宽的一片,压根没有曲线可言。

判据就是**条件散布**:按 llr 的值分档,看每档里 Edit 值的中位数(曲线)和四分位距
(带宽)。带宽窄 => 有曲线;带宽和整体差距同量级 => 没有。

分通道各算一条:三条重合说明是共同的色调,分开则是白平衡/色彩矩阵的事。
"""
import os
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import numpy as np
from PIL import Image

sys.path.insert(0, "/home/jannchie/llr/sony_repro/tools")
sys.path.insert(0, "/home/jannchie/llr/apps/worker/src")

from aniso_stage import find_arw, render  # noqa: E402

REC601 = np.array([0.299, 0.587, 0.114], np.float32)
NBIN = 16


def main() -> int:
    stem = sys.argv[1] if len(sys.argv) > 1 else "DSC03036"
    arw = find_arw(stem)
    jpg = Path(str(arw).replace(".ARW", ".JPG"))
    edit = np.asarray(Image.open(jpg).convert("RGB"), np.float32) / 255.0
    llr = np.asarray(render(arw, denoise_model="passthrough", chroma=True),
                     np.float32)
    h, w = min(llr.shape[0], edit.shape[0]), min(llr.shape[1], edit.shape[1])
    llr, edit = llr[:h, :w], edit[:h, :w]
    # llr_vs_edit 估到的整体平移 (+3,0);不校正的话散布里会混进错位的贡献。
    llr = np.roll(llr, -3, axis=0)
    m = (slice(96, h - 96), slice(96, w - 96))
    llr, edit = llr[m], edit[m]
    print(f"{stem}  比较区域 {llr.shape}(已按 +3 行对齐)\n")

    edges = np.linspace(0.0, 1.0, NBIN + 1)
    for ci, ch in enumerate("RGB"):
        a, b = llr[..., ci].ravel(), edit[..., ci].ravel()
        print(f"  {ch} 通道:  llr 档      Edit 中位     差       四分位距")
        for lo, hi in zip(edges, edges[1:]):
            s = (a >= lo) & (a < hi)
            if s.sum() < 2000:
                continue
            q1, med, q3 = np.percentile(b[s], [25, 50, 75])
            mid = (lo + hi) / 2
            print(f"          [{lo:.3f},{hi:.3f})  {med:8.4f}  "
                  f"{med - mid:+8.4f}   {q3 - q1:8.4f}")
        print()

    # 带宽 vs 差距:哪个大,答案就在哪边。
    y_a, y_b = llr @ REC601, edit @ REC601
    widths, gaps = [], []
    for lo, hi in zip(edges, edges[1:]):
        s = (y_a >= lo) & (y_a < hi)
        if s.sum() < 2000:
            continue
        q1, med, q3 = np.percentile(y_b[s], [25, 50, 75])
        widths.append(q3 - q1)
        gaps.append(abs(med - (lo + hi) / 2))
    wm, gm = float(np.median(widths)), float(np.median(gaps))
    print(f"  亮度:各档的四分位距 中位 {wm:.4f};  中位偏移 中位 {gm:.4f}")
    if wm < gm:
        print("  => 带宽比偏移窄 => 主要是**逐点的色调映射**不同,存在可校的曲线。")
    else:
        print("  => 带宽比偏移还宽 => 同一个输入值散到很宽一片,"
              "\n     不是一条曲线能解释的,是空间/局部处理的差别(demosaic 那一段)。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
