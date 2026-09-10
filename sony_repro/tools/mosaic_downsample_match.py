"""引擎那份预览 mosaic,是不是就是 rawpy 这份 raw 缩下来的。

这是"输入是否同源"最直接的问法。`vignette_order_check.py` 想用径向亮度分布来
判断,可这张是夜景,径向中位数完全被画面内容带着走(引擎那条线平得只有 3% 起伏,
只是因为降采样把内容抹平了),判据失效。

换个不依赖内容的做法:把 rawpy 的**每个相位平面**按面积平均(box)缩到引擎的
尺寸,再逐像素比。Bayer 必须分相位缩 —— 直接缩整张会把四个通道混在一起。

对得上就说明:

  * 两边读的是同一批数字,引擎的预览只是 llr 那份的降采样;
  * 绝对值也对得上的话,降噪之前**没有**做暗角校正(校正会把边缘整体抬高)。

对不上,才轮到怀疑引擎在解码阶段另做了什么。
"""
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import numpy as np
import rawpy
from PIL import Image

sys.path.insert(0, "/home/jannchie/llr/sony_repro/tools")

from aniso_stage import find_arw  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
ORIGIN = [(0, 0), (0, 1), (1, 0), (1, 1)]


#: 缩放方式决定了噪声还在不在。BOX 是面积平均,会把单像素噪声抹掉 sqrt(n) 倍;
#: NEAREST 是跳采样,噪声原样保留。`mosaic_noise_scale.py` 量到引擎那份的噪声
#: 与 raw 之比是 0.972 —— 一点没降,所以若它真是缩下来的,只可能是**跳采样**。
#: 第一版只试了 BOX,相关 0.005,据此判了"另一条解码路径"。
FILTERS = {"BOX(面积平均)": Image.BOX, "NEAREST(跳采样)": Image.NEAREST,
           "BILINEAR": Image.BILINEAR}


def box_resize(a: np.ndarray, w: int, h: int, flt=Image.BOX) -> np.ndarray:
    im = Image.fromarray(a.astype(np.float32), mode="F")
    return np.asarray(im.resize((w, h), flt), np.float64)


def main() -> int:
    stem = sys.argv[1] if len(sys.argv) > 1 else "DSC03036"
    cap = sys.argv[2] if len(sys.argv) > 2 else f"rawnr_full_{stem}.npz"
    z = np.load(os.path.join(HERE, cap))
    mos = z["mosaic"]
    arw = find_arw(stem)
    with rawpy.imread(str(arw)) as raw:
        vis = raw.raw_image_visible.copy()
        full = raw.raw_image.copy()

    print(f"{stem}  引擎 mosaic {mos.shape}   rawpy visible {vis.shape}   "
          f"含边 {full.shape}\n")
    for src_label, src in (("visible", vis), ("含边", full)):
        for fname, flt in FILTERS.items():
            print(f"  用 rawpy {src_label} + {fname}:")
            for i, (oy, ox) in enumerate(ORIGIN):
                eng = mos[oy::2, ox::2].astype(np.float64)
                h, w = eng.shape
                got = box_resize(src[oy::2, ox::2], w, h, flt)
                n = 12
                a, b = got[n:-n, n:-n], eng[n:-n, n:-n]
                r = float(np.corrcoef(a.ravel(), b.ravel())[0, 1])
                d = a - b
                print(f"    相位{i} {ORIGIN[i]}  相关 {r:+.6f}   "
                      f"|差| 中位 {float(np.median(np.abs(d))):7.3f}   "
                      f"逐位相同 {100 * float(np.mean(a == b)):6.2f}%")
            print()
    print("  相关接近 1 且偏置接近 0 => 引擎的预览就是这份 raw 缩下来的,"
          "\n  降噪之前没有额外的校正,两边的输入同源(只是尺寸不同)。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
