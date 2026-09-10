"""把 `rawnr_simd` 真正跑在一张照片上,再决定要不要把它接进生产路径。

用户要的是「和 Edit 的实现完全一致」,而转录已经完成、单测也有了,只差接线。
但接线之前有一个未决项必须先量:**绿色的滤波核 `0x3a0c30` 没解码**,
`denoise_phase_green` 借用的是 R/B 的核(`0x3a1b00`)。绿色贡献了亮度的大头,
这个借用要是不成立,结果会直接坏在最显眼的地方。

所以顺序是:先离线跑通、量出来,再决定接不接 —— 而不是先改生产代码再看效果。

量三件事:
  * **格纹**(轴向/对角,按频带)—— 用户能看见的那个
  * **与机内直出的差距** —— 复刻的目标
  * **细节保留** —— 降噪不能以糊掉为代价;Edit 的 RAW 级只掉约十分之一的细节

对照组是 llr 现在的 wavelet。

⚠️ 这里**不**再乘 `iso_strength`。抓到的阈值表与纯从 tag 算出的表在 ISO 100
与 ISO 1250 上都逐项相同(rawnr.py 的模块注释),若 ISO 插值乘在阈值上,
两个点不可能都对得上 —— 所以它不在这条路上,在这里再乘一次就是重复应用。

用法::

    python rawnr_simd_try.py [DSC03036 ...]
"""
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import numpy as np
import rawpy

sys.path.insert(0, "/home/jannchie/llr/sony_repro/tools")
sys.path.insert(0, "/home/jannchie/llr/apps/worker/src")

from aniso import TILE, flat_tiles, spectrum  # noqa: E402
from aniso_freq import RINGS, ring_ratio  # noqa: E402
from aniso_stage import W601, find_arw  # noqa: E402
from llr_worker.denoise import pack_bayer, unpack_bayer  # noqa: E402
from llr_worker.sony.rawnr import ENGINE_FULL_SCALE, noise_model  # noqa: E402
from llr_worker.sony import rawnr_simd as S  # noqa: E402

PAD = S.PHASE_MARGIN


def thresholds_from(model):
    """`filt` 要的 32768 项查表,直接来自相机写在 ARW 里的曲线。"""
    levels = np.arange(1 << 15, dtype=np.int64)
    return model.threshold(levels).astype(np.int32)


def denoise_planes(planes, thr):
    """四个相位平面进出,形状不变。

    `denoise_phase_*` 每边吃掉 `PHASE_MARGIN`,所以先做边缘反射填充再裁回来。
    反射而不是补零 —— 补零会在边上造一圈假边,sigma 滤波器会把整条边判成结构。
    """
    out = np.empty_like(planes)
    padded = [np.pad(planes[..., k], PAD, mode="reflect") for k in range(4)]
    # RGGB:0=R, 1=G, 2=G, 3=B。两个绿相走 `denoise_greens` —— 生产用的就是它。
    # ⚠️ 别退回逐相调 `denoise_phase_green`:那样得自己传 `phase=`,而这里原先漏了,
    # 两个绿相都按 phase 0 跑。两张跨平面基准表互换的代价是逐位 100% 掉到 56%,
    # 但它**不会报错**,只会让下面那几个数字悄悄失真。
    out[..., 0] = S.denoise_phase_rb(padded[0], thr)
    out[..., 3] = S.denoise_phase_rb(padded[3], thr)
    out[..., 1], out[..., 2] = S.denoise_greens(padded[1], padded[2], thr)
    return out


def develop(path, mode):
    """mode: None / 'simd' / 'wavelet' —— 走同一条 postprocess,只换马赛克。"""
    with rawpy.imread(str(path)) as raw:
        if mode == "simd":
            model = noise_model(path)
            if model is None:
                raise SystemExit("这张片没有 Sony 噪声模型,SIMD 路径无从谈起")
            visible = raw.raw_image_visible
            h, w = visible.shape
            he, we = h - (h % 2), w - (w % 2)
            planes = pack_bayer(np.ascontiguousarray(visible[:he, :we])).astype(np.float32)
            # 引擎吃的是**黑电平未减**的原始 14 位值 —— 由 `rawnr_simd_domain.py`
            # 裁判:减过黑电平的话,绿色的 `ref = c − d − 512` 在暗部变负被 clip
            # 到 0,整片绿被抬高 148 个 level、改动率 38%,与 R/B 的 2.8~3.9% 差一
            # 个数量级;不减则四个相位的改动都落在 0.78~1.17%,均值漂移 ±0.3 以内,
            # 正在引擎实测的区间里(ISO100 0.46%,ISO8000 7.97%)。
            # `OFFSET_GREEN = −512` 与这台机器的黑电平数值相同,不是巧合:那一步
            # 就是引擎自己在做的对齐。
            eng = np.clip(planes, 0, ENGINE_FULL_SCALE)
            done = denoise_planes(eng, thresholds_from(model))
            visible[:he, :we] = unpack_bayer(
                np.rint(np.clip(done, 0, raw.white_level)).astype(visible.dtype))
        elif mode == "wavelet":
            from llr_worker.denoise import denoise_raw_inplace, get_denoiser
            from llr_worker.sony.rawnr import detail_restore
            curve = noise_model(path)
            rest = detail_restore(path) if curve is not None else None
            denoise_raw_inplace(
                raw, get_denoiser("wavelet"), noise=curve,
                detail=None if rest is None or curve is None
                else (rest.fraction, rest.limit_in_thresholds(curve)))
        return raw.postprocess(
            use_camera_wb=True, no_auto_bright=True,
            output_color=rawpy.ColorSpace.sRGB, gamma=(2.222, 4.5),
            output_bps=8).astype(np.float32)


def bands(y, tiles):
    specs = [spectrum(y[cy:cy + TILE, cx:cx + TILE]) for cy, cx in tiles]
    return [float(np.median([ring_ratio(s, lo, hi)[0] for s in specs]))
            for lo, hi in RINGS]


def detail_sigma(y, tiles):
    """结构块里最细一带的能量 —— 降噪糊没糊掉细节,看这个。"""
    vals = []
    for cy, cx in tiles:
        b = y[cy:cy + TILE, cx:cx + TILE]
        vals.append(float((b - np.mean(b)).std()))
    return float(np.median(vals))


def main():
    stems = sys.argv[1:] or ["DSC03036"]
    hdr = "  ".join(f"{f'{2/hi:.1f}-{2/lo:.1f}px':>11}" for lo, hi in RINGS)
    for stem in stems:
        arw = find_arw(stem)
        if arw is None:
            print(f"跳过 {stem}")
            continue
        print(f"\n=== {stem}")
        outs = {}
        for label, mode in [("不降噪", None), ("wavelet(现在上线的)", "wavelet"),
                            ("RawNRSIMD(Edit 的滤波器)", "simd")]:
            outs[label] = develop(arw, mode)

        base = outs["不降噪"] @ W601
        flat = flat_tiles(base, n=16)
        # 结构块:方差最高的那批,用来看细节有没有被糊掉。
        h, w = base.shape
        cand = sorted(((float(base[cy:cy+TILE, cx:cx+TILE].std()), cy, cx)
                       for cy in range(0, h - TILE, TILE * 4)
                       for cx in range(0, w - TILE, TILE * 4)), reverse=True)
        struct = [(cy, cx) for _s, cy, cx in cand[:24]]

        print(f"  {'版本':<26} {hdr}   {'结构块细节':>10}")
        for label, rgb in outs.items():
            y = rgb @ W601
            row = bands(y, flat)
            det = detail_sigma(y, struct)
            print(f"  {label:<26} " + "  ".join(f"{v:11.2f}" for v in row)
                  + f"   {det:10.2f}")
    print("\n  最右第二列(2.0-2.7px)是格纹所在,越接近 1.00 越各向同性。")
    print("  末列是结构块的细节量,不降噪那一版是上限 —— 掉得越少越好。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
