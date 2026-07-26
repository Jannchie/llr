r"""管线最后那一步:输出色彩空间转换 —— 缺掉的两成饱和度就在这里。

`ZcTaskMarble` / `ZcTaskSIMDMarble` 一进来就按 `settings+0x44` 二选一调
`FUN_140196170`(实测 35 次,一块 tile 一次)或 `FUN_140196500`,两者都是:

    线性化查表 -> 3x3(每行和为 1,保白) -> 钳 14 位 -> 再编码查表

三张表由 `FUN_140193530` 惰性生成,静态文件里是空的;实机 dump 出来后认出:
A 路解码是 **sRGB EOTF**、B 路解码是 **x^2.2**、两路共用的编码表是 **x^(1/2.2)**。
矩阵把绿色差压掉约两成,正是 §7.6 里那个缺口。

用法: python gamut_check.py <ARW> [style]
"""
import os
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, "/home/jannchie/llr/apps/worker/src")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sat_probe import load_jpeg, render  # noqa: E402
from ycc_check import chroma_params, chroma_stats, rgb_to_ycc, srgb8, ycc_to_rgb  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sony_repro.sr2 import data_ifds  # noqa: E402

LOOK_ORDER = ("ST", "VV", "NT", "PT", "FL", "VV2", "IN", "SH", "BW", "SE")
FULL = 16383
SHIFT = 8192

# FUN_140196170:线性化用 sRGB,编码用 2.2 gamma
MATRIX_A = np.array([[5042, 3234, -84], [748, 6845, 599], [115, 595, 7481]]) / SHIFT
# FUN_140196500:两端都是 2.2 gamma
MATRIX_B = np.array([[7049, 1230, -87], [1046, 6522, 624], [161, 228, 7803]]) / SHIFT


def srgb_decode(c):
    c = np.clip(c, 0.0, 1.0)
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)


def gamut(rgb14, matrix, srgb_in):
    lin = srgb_decode(rgb14 / FULL) if srgb_in else np.clip(rgb14 / FULL, 0, 1) ** 2.2
    out = np.clip(lin @ matrix.T, 0.0, 1.0)
    return out ** (1 / 2.2) * FULL


def main():
    arw = Path(sys.argv[1])
    style = sys.argv[2] if len(sys.argv) > 2 else "VV2"
    p = chroma_params(data_ifds(arw)[LOOK_ORDER.index(style)])

    before8 = render(arw, style)
    rgb14 = before8.astype(np.float64) / 255.0 * FULL
    ycc14 = ycc_to_rgb(*rgb_to_ycc(rgb14, p))
    theirs = load_jpeg(arw.with_suffix(".JPG"), before8.shape)

    stages = {
        "只到曲线": before8,
        "加 YCC 段": srgb8(ycc14),
        "再加 A 路": srgb8(gamut(ycc14, MATRIX_A, srgb_in=True)),
        "再加 B 路": srgb8(gamut(ycc14, MATRIX_B, srgb_in=False)),
        "只加 A 路": srgb8(gamut(rgb14, MATRIX_A, srgb_in=True)),
    }
    print(f"{arw.name}  外观={style}\n\n与机内 JPEG 相比(1.0 = 完全对上):")
    for name, img in stages.items():
        chroma_stats(name, img, theirs)
    print()
    for name, img in list(stages.items()) + [("引擎", theirs)]:
        print(f"  {name:<10} 平均 RGB {img.reshape(-1, 3).mean(0).round(1)}")


if __name__ == "__main__":
    main()
