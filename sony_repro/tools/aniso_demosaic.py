"""格纹是不是**去马赛克**留下的:同一张 RAW 换算法,量噪声的方向性。

用户先前问过"噪点有没有可能来自于去马赛克环节",当时没有量化的答案。现在有了
方向性指标,这个问题可以直接回答 —— 而且它是**上游**的问题,如果成立,下游怎么
调降噪都补不回来。

AHD 是嫌疑最大的:它对绿色通道做**方向性**插值(逐像素在水平与垂直之间二选一),
红蓝再按色差补。方向一旦选错,误差就沿着那一行或那一列拉开 —— 这正是"横纹竖纹"
的形状,而且水平垂直会同时出现,因为不同像素会选到不同方向。

对照组:
  * `linear`  双线性,无方向判决 —— 若它也轴向偏高,就不是方向判决的锅
  * `vng`     梯度阈值,弱方向性
  * `dht`     另一族方向性算法,用来分辨"方向性插值"与"AHD 这一个实现"
  * `half`    2x2 binning,**根本不插值** —— 这是基线,它的各向异性就是
              传感器噪声本身的各向异性

⚠️ 必须在**同一批 tile 位置**上量,而 half_size 的分辨率是别人的一半,所以它
单独挑自己的 tile,只当基线读,不与其他四组直接比数值。

用法::

    python aniso_demosaic.py DSC03036 [...]
"""
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import numpy as np
import rawpy

sys.path.insert(0, "/home/jannchie/llr/sony_repro/tools")
from aniso import TILE, directional, flat_tiles, spectrum  # noqa: E402

SRC_DIRS = [Path("/mnt/e/10960725"), Path("/mnt/e/temp_photo")]
W601 = np.array([0.299, 0.587, 0.114], np.float32)
NTILE = 16

ALGOS = [
    ("AHD(上线用的)", rawpy.DemosaicAlgorithm.AHD, False),
    ("linear 双线性", rawpy.DemosaicAlgorithm.LINEAR, False),
    ("VNG", rawpy.DemosaicAlgorithm.VNG, False),
    ("DHT", rawpy.DemosaicAlgorithm.DHT, False),
    ("2x2 binning(不插值)", rawpy.DemosaicAlgorithm.AHD, True),
]


def find_arw(stem):
    for d in SRC_DIRS:
        p = d / f"{stem}.ARW"
        if p.exists():
            return p
    return None


def develop(path, algo, half):
    """和生产路径同一组 postprocess 参数,只换算法。

    线性输出(gamma=(1,1))、不自动提亮、相机白平衡 —— 任何一项不同都会改变
    噪声与信号的相对量级,从而改变平坦 tile 的选择。
    """
    with rawpy.imread(str(path)) as raw:
        rgb = raw.postprocess(
            demosaic_algorithm=algo, use_camera_wb=True, no_auto_bright=True,
            output_color=rawpy.ColorSpace.raw, gamma=(1, 1), output_bps=16,
            half_size=half)
    return rgb.astype(np.float32) / 257.0   # 16 位 -> 0..255 的尺度


def measure(y, tiles):
    a = np.array([directional(spectrum(y[cy:cy + TILE, cx:cx + TILE]))
                  for cy, cx in tiles])
    return np.median(a, axis=0)


def main():
    stems = sys.argv[1:] or ["DSC03036"]
    for stem in stems:
        path = find_arw(stem)
        if path is None:
            print(f"跳过 {stem}:找不到 ARW")
            continue
        print(f"\n=== {stem}")
        print(f"  {'去马赛克':<22} {'轴向/对角':>9} {'竖纹':>7} {'横纹':>7}")
        tiles = None
        for name, algo, half in ALGOS:
            y = develop(path, algo, half) @ W601
            t = flat_tiles(y, n=NTILE) if half or tiles is None else tiles
            if not half and tiles is None:
                tiles = t
            ax, eh, ev = measure(y, t)
            mark = "   <- 自挑 tile,只作基线" if half else ""
            print(f"  {name:<22} {ax:9.2f} {eh:7.2f} {ev:7.2f}{mark}")
    print(f"\n  线性域,{NTILE} 块最平坦的 {TILE}x{TILE}。1.00 = 噪声各方向等量。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
