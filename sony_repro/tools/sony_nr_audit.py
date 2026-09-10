"""`"sony"` 降噪的**全图**体检 —— 前面那批指标测不到的东西。

上一轮的判断是拿两个指标下的:平坦块的频谱各向异性,和结构块的细节量。两个都是
**统计量**,而且都只在挑出来的块上算 —— 零星的坏点、局部的色偏、某类边缘上的
崩塌,它们一个都测不到。用户看到的是"完全不行",所以要找的正是这类东西。

已知的头号嫌疑写在 `rawnr_simd.filt` 的注释里:`count == 0` 时输出 **0**。
那是转录如实保留的退化情形(`base` 落在两群之间,没有抽头够得着),在硬边上会
触发。单测记录了它,但从没在真实照片上量过它有多普遍 —— 如果面积可观,那就是
一地黑点,而黑点在"平坦块频谱"和"结构块细节"里都看不见。

逐项查:
  1. 输出里有多少个 0(以及接近 0 的极暗点),集中在哪
  2. 与不降噪相比,**最坏**的那些像素差多少,长什么样
  3. 逐通道的直流是否漂移(色偏)
  4. 与小波比,谁更接近机内直出
"""
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import numpy as np
import rawpy

sys.path.insert(0, "/home/jannchie/llr/sony_repro/tools")
sys.path.insert(0, "/home/jannchie/llr/apps/worker/src")

from aniso_stage import find_arw  # noqa: E402
from llr_worker.denoise import _plane_black_levels, _plane_colors, get_denoiser, pack_bayer  # noqa: E402
from llr_worker.sony.rawnr import detail_restore, noise_model  # noqa: E402


def main():
    stem = sys.argv[1] if len(sys.argv) > 1 else "DSC03036"
    arw = find_arw(stem)
    if arw is None:
        raise SystemExit(f"找不到 {stem}")

    with rawpy.imread(str(arw)) as raw:
        visible = raw.raw_image_visible
        h, w = visible.shape
        he, we = h - (h % 2), w - (w % 2)
        planes = pack_bayer(np.ascontiguousarray(visible[:he, :we])).astype(np.float32)
        sizes = raw.sizes
        rp = int(getattr(sizes, "top_margin", 0) or 0)
        cp = int(getattr(sizes, "left_margin", 0) or 0)
        black = _plane_black_levels(raw, rp, cp)
        cfa = _plane_colors(raw, rp, cp)
        white = float(raw.white_level)
        scale = np.maximum(white - black, 1.0)
        norm = np.clip((planes - black) / scale, 0.0, 1.0)

        curve = noise_model(arw)
        rest = detail_restore(arw)
        det = None if rest is None or curve is None else (
            rest.fraction, rest.limit_in_thresholds(curve))
        print(f"=== {stem}  平面 {norm.shape}  cfa={cfa}")
        print(f"  黑电平 {list(black)}  白电平 {white}")
        print(f"  噪声曲线 lo={curve.lo} hi={curve.hi} base={curve.base} slope={curve.slope}")
        print(f"  detail 元组 {det}  (gain={rest.gain} limit={rest.limit})")

        out = {}
        for name in ("sony", "wavelet"):
            out[name] = get_denoiser(name)(
                norm.copy(), None, cfa, curve, det, chroma_scale=1.0,
                sensor_levels=(black, white))

    for name, a in out.items():
        d = a - norm
        print(f"\n=== {name}")
        print(f"  输出范围 [{float(a.min()):.6f}, {float(a.max()):.6f}]")
        zero = float((a <= 0.0).mean()) * 100
        near = float((a < norm - 0.02).mean()) * 100
        print(f"  恰为 0 的点 {zero:.4f}%   比原图暗 0.02 以上的点 {near:.4f}%")
        print(f"  逐平面直流漂移 {[round(float(x), 6) for x in d.mean(axis=(0,1))]}")
        print(f"  |差| 均值 {float(np.abs(d).mean()):.6f}  "
              f"p99.9 {float(np.quantile(np.abs(d), 0.999)):.6f}  "
              f"最大 {float(np.abs(d).max()):.6f}")
        # 最坏的点扎堆在哪:按 128x128 网格统计
        bad = (np.abs(d).max(axis=2) > 0.05)
        if bad.any():
            g = 128
            hh, ww = bad.shape[0] // g * g, bad.shape[1] // g * g
            tiles = bad[:hh, :ww].reshape(hh // g, g, ww // g, g).mean(axis=(1, 3))
            k = int(np.argmax(tiles))
            print(f"  |差|>0.05 的点占 {100*float(bad.mean()):.4f}%,"
                  f"最集中的 128x128 块里占 {100*float(tiles.flat[k]):.1f}%"
                  f" @ ({k // tiles.shape[1] * g}, {k % tiles.shape[1] * g})")
        else:
            print("  没有 |差|>0.05 的点")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
