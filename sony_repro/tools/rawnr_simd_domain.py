"""引擎的平面是**减过黑电平**的还是没减的 —— 用绿色的偏置来裁判。

`rawnr.py` 的注释说"The plane is black-subtracted, so 0 is black"。但那是一句
断言,不是一条测量。反过来有一条硬证据:`OFFSET_GREEN = −512` 是从
`0x3a0018..0x3a0041` 读出来的,而这台机器的黑电平**正好是 512**。

若平面没减黑电平,`ref = c − d − 512` 就是"顺手把黑电平减掉",绿色因此与 R/B
(offset 0)对齐 —— 这解释了为什么四个相位里只有绿色带偏置。
若平面减过黑电平,同一个式子在暗部就是负数,被 clip 到 0,整片绿被抬回 +512
—— 这正是 `rawnr_simd_diag.py` 量到的:绿平面均值 393.9 → 538.6,改动 38.94%。

所以两种域给出的绿色行为差得很远,一跑就分得开。判据有三条,必须同时成立:
  * 绿平面的**均值不变**(降噪不是加法)
  * 绿的改动比例与 R/B 同量级(它们看的是同一片噪声)
  * clip 到 0 的点接近没有
"""
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import numpy as np
import rawpy

sys.path.insert(0, "/home/jannchie/llr/sony_repro/tools")
sys.path.insert(0, "/home/jannchie/llr/apps/worker/src")

from aniso_stage import find_arw  # noqa: E402
from llr_worker.denoise import pack_bayer  # noqa: E402
from llr_worker.sony.rawnr import ENGINE_FULL_SCALE, noise_model  # noqa: E402
from llr_worker.sony import rawnr_simd as S  # noqa: E402
from rawnr_simd_try import PAD, thresholds_from  # noqa: E402


def run(eng, thr, n=512):
    y0, x0 = eng.shape[0] // 2, eng.shape[1] // 2
    win = (slice(y0, y0 + n + 2 * PAD), slice(x0, x0 + n + 2 * PAD))
    # 两个绿相一起算,和生产同一条路。原先是逐相调 `denoise_phase_green` 且没传
    # `phase=`,于是 G1、G2 都按 phase 0 跑 —— 两张跨平面基准表本该是不同的。
    greens = dict(zip((1, 2), S.denoise_greens(
        eng[win[0], win[1], 1], eng[win[0], win[1], 2], thr)))
    rows = []
    for k, name in ((0, "R"), (1, "G1"), (2, "G2"), (3, "B")):
        a = eng[win[0], win[1], k]
        out = greens[k] if k in greens else S.denoise_phase_rb(a, thr)
        src = a[PAD:-PAD, PAD:-PAD]
        rows.append((name, float(src.mean()), float(out.mean()),
                     100 * float(np.abs(out - src).mean()) / max(float(src.mean()), 1),
                     100 * float((out == 0.0).mean())))
    return rows


def main():
    stem = sys.argv[1] if len(sys.argv) > 1 else "DSC03036"
    arw = find_arw(stem)
    with rawpy.imread(str(arw)) as raw:
        visible = raw.raw_image_visible
        h, w = visible.shape
        he, we = h - (h % 2), w - (w % 2)
        planes = pack_bayer(np.ascontiguousarray(visible[:he, :we])).astype(np.float32)
        black = float(np.min(raw.black_level_per_channel))
        white = float(raw.white_level)
        thr = thresholds_from(noise_model(arw))

        variants = {
            "A 减黑电平后缩放(原做法)":
                np.clip((planes - black) * (ENGINE_FULL_SCALE / max(white - black, 1.0)),
                        0, ENGINE_FULL_SCALE),
            "B 原始值,不减不缩放":
                np.clip(planes, 0, ENGINE_FULL_SCALE),
        }
        for label, eng in variants.items():
            print(f"\n=== {label}")
            print(f"  {'平面':<5} {'输入均值':>9} {'输出均值':>9} {'均值漂移':>9}"
                  f" {'改动%':>7} {'输出为0%':>8}")
            for name, mi, mo, pct, z in run(eng, thr):
                print(f"  {name:<5} {mi:9.1f} {mo:9.1f} {mo-mi:+9.1f} {pct:7.2f} {z:8.3f}")
    print("\n  判据:绿的均值漂移应接近 0,且改动% 与 R/B 同量级。")
    print("  黑电平 512 —— 与 OFFSET_GREEN 的 −512 数值相同,这正是要裁判的事。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
