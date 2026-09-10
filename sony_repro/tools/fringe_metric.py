"""高光边缘的彩边有多少 —— 一个**局部**指标,而不是全图平均。

彩边一直只有肉眼判断(`worst_crop.py` 的图)。全图 |Δ| 看不见它:它只出现在高光
边缘,占的面积极小,一平均就没了。要判断"下游 ITP 会不会吸收它",先得有个能量到
它的数。

做法:按「亮度高 + 梯度大」挑出高光边缘的像素,只在那儿量**色度**的变化量
(色度 = 去掉亮度之后的残量)。三档对照:

  * 高光边缘   —— 彩边真正出现的地方;
  * 一般边缘   —— 有结构但不亮,用来分离"边缘"和"高光"这两个因素;
  * 平坦区     —— 底噪。

比较 Sony RawNR 与小波在同一批像素上的读数:小波是已知不产生彩边的那个,它的
读数就是这个指标的下限参照。
"""
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import numpy as np

sys.path.insert(0, "/home/jannchie/llr/sony_repro/tools")
sys.path.insert(0, "/home/jannchie/llr/apps/worker/src")

from aniso_stage import find_arw, render  # noqa: E402

REC601 = np.array([0.299, 0.587, 0.114], np.float32)


def chroma_mag(img: np.ndarray) -> np.ndarray:
    """去掉亮度之后剩下的量 —— 彩边就体现在这上面。"""
    y = img @ REC601
    return np.linalg.norm(img - y[..., None], axis=-1)


def main() -> int:
    stem = sys.argv[1] if len(sys.argv) > 1 else "DSC03036"
    arw = find_arw(stem)
    off = np.asarray(render(arw, denoise_model="passthrough", chroma=True), np.float32)
    variants = {
        "Sony RawNR": render(arw, denoise_model="sony", chroma=True),
        "小波": render(arw, denoise_model="wavelet", chroma=True),
    }
    y = off @ REC601
    gy = np.abs(np.diff(y, axis=0, prepend=y[:1]))
    gx = np.abs(np.diff(y, axis=1, prepend=y[:, :1]))
    grad = np.maximum(gy, gx)
    g_hi = float(np.percentile(grad, 99.0))
    g_lo = float(np.percentile(grad, 50.0))
    print(f"{stem}  {off.shape}   梯度 p99={g_hi:.4f}  p50={g_lo:.4f}\n")

    masks = {
        "高光边缘(亮>0.5 且 梯度>p99)": (y > 0.5) & (grad > g_hi),
        "一般边缘(亮<0.2 且 梯度>p99)": (y < 0.2) & (grad > g_hi),
        "平坦区(梯度<p50)": grad < g_lo,
    }
    base_c = chroma_mag(off)
    print("  区域                          像素数      Sony 色度增量   小波色度增量")
    for label, m in masks.items():
        n = int(m.sum())
        if n < 500:
            print(f"    {label:<28} {n:8d}   样本太少")
            continue
        cells = []
        for name in ("Sony RawNR", "小波"):
            c = chroma_mag(np.asarray(variants[name], np.float32))
            cells.append(float(np.mean(c[m] - base_c[m])))
        print(f"    {label:<28} {n:8d}   {cells[0]:+.6f}      {cells[1]:+.6f}")

    print("\n  「高光边缘」一列 Sony 明显为正而小波不是 => 彩边是 RawNR 引入的,"
          "\n  且量到了 —— 这个数就是后面判断「ITP 有没有吸收它」的基线。"
          "\n  两者都接近 0 => 肉眼看到的彩边另有来源,不在降噪。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
