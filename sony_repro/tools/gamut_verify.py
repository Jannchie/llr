r"""先把公式钉死:出口那份能不能由入口那份精确算出来。

对得上才说明「sRGB 解码 -> 矩阵 A -> 2.2 gamma 编码」这个读法没错,
后面拿它当真值才有意义。顺便把出口渲成 PNG,好确认这块 tile 是画面的哪里。
"""
import os

import numpy as np
from PIL import Image

SCR = os.path.dirname(os.path.abspath(__file__))
FULL = 16383
SHIFT = 8192
MATRIX_A = np.array([[5042, 3234, -84], [748, 6845, 599], [115, 595, 7481]]) / SHIFT


def srgb_decode(c):
    c = np.clip(c, 0.0, 1.0)
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)


z = np.load(os.path.join(SCR, "gamut_frame.npz"))
luts = np.load(os.path.join(SCR, "gamut_luts.npz"))
dec, enc = luts["decodeA_0x65bd60"], luts["encode_0x61bd60"]

for t in range(3):
    src = np.stack([z["t%d_in_p%d" % (t, k)] for k in range(3)], -1).astype(np.int64)
    dst = np.stack([z["t%d_out_p%d" % (t, k)] for k in range(3)], -1).astype(np.int64)
    x0, y0, x1, y1 = z["meta_%d" % t][:4]
    src, dst = src[y0:y1, x0:x1], dst[y0:y1, x0:x1]

    # 用实机抓下来的两张表,整数路径逐位复算
    lin = dec[np.clip(src, 0, FULL)]
    mix = (lin.astype(np.int64) @ np.rint(MATRIX_A * SHIFT).astype(np.int64).T) >> 13
    ours = enc[np.clip(mix, 0, FULL)]
    d = ours.astype(np.int64) - dst
    exact = (d == 0).mean()

    # 再看看纯解析式(sRGB / 2.2)够不够准,接进 worker 时不想搬两张 32KB 的表
    ana = (np.clip(srgb_decode(src / FULL) @ MATRIX_A.T, 0, 1) ** (1 / 2.2) * FULL)
    print("tile%d %dx%d  查表逐位相同 %.4f%%  最大差 %d   解析式最大差 %.1f  中位差 %.2f"
          % (t, src.shape[1], src.shape[0], exact * 100, np.abs(d).max(),
             np.abs(ana - dst).max(), np.median(np.abs(ana - dst))))

    if t == 0:
        for name, a in (("in", src), ("out", dst)):
            img = (np.clip(a / FULL, 0, 1) * 255).astype(np.uint8)
            Image.fromarray(img).save(os.path.join(SCR, "gamut_%s.png" % name))
        print("  已存 gamut_in.png / gamut_out.png")
