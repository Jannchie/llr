r"""拿引擎自己的前后两份逐像素验 YCC 段 —— 不再靠整幅统计量猜。

`stage_frame.py` 能把任一阶段边界上的整幅画面拼出来(tile 位置在 task 的
`+0x48..0x54`),于是 RGB2YCC 的入口和出口是**同一批像素**,对齐问题不存在。
§7.6 那个「色度过两成」的判断是拿复刻整幅比机内 JPEG 得来的,分不清错在哪一段;
这里一段一段地对。

用法: python ycc_exact.py <ARW> <ycc_frames.npz> [style]
"""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sony_repro.sr2 import data_ifds  # noqa: E402
from sony_repro.ycc import chroma_params, rgb_to_ycc, unpack_chroma, ycc_to_rgb  # noqa: E402

LOOK_ORDER = ("ST", "VV", "NT", "PT", "FL", "VV2", "IN", "SH", "BW", "SE")
CENTER = 0x8000


def report(name, ours, theirs, scale=1.0):
    d = ours.astype(np.float64) - theirs.astype(np.float64)
    print("  %-22s 逐位相同 %6.2f%%  |差| 中位 %7.2f  p99 %8.1f  最大 %8.1f"
          % (name, (np.abs(d) <= 0.5).mean() * 100,
             np.median(np.abs(d)) * scale, np.percentile(np.abs(d), 99) * scale,
             np.abs(d).max() * scale))


def main():
    arw = Path(sys.argv[1])
    z = np.load(sys.argv[2])
    style = sys.argv[3] if len(sys.argv) > 3 else "VV2"
    p = chroma_params(data_ifds(arw)[LOOK_ORDER.index(style)])
    cross, gain = unpack_chroma(p)
    print("%s  外观=%s\n八参数 %s\n  交叉 %s\n  增益 %s\n"
          % (arw.name, style, p.tolist(),
             [round(float(c), 4) for c in cross], [round(float(g), 4) for g in gain]))

    src = z["ZcTaskRGB2YCC_in"].astype(np.float64)
    dst = z["ZcTaskRGB2YCC_out"].astype(np.float64)
    ok = src.max(-1) > 0                       # 拼接留下的空白不参与
    print("有效像素 %d / %d" % (ok.sum(), ok.size // 1))

    y, cb, cr = rgb_to_ycc(src[ok], p)
    print("\nRGB2YCC:")
    report("Y  对平面0", y, dst[ok][:, 0])
    report("Cr 对平面1", cr + CENTER, dst[ok][:, 1])
    report("Cb 对平面2", cb + CENTER, dst[ok][:, 2])

    # 平面1 = Cr(红色差,YCC2RGB 里乘 1.402),平面2 = Cb(蓝色差,乘 1.772)
    ye = z["ZcTaskYGamma_out"][ok][:, 0].astype(np.float64)   # Y 在 YGamma 里还会再过一条曲线
    cre, cbe = dst[ok][:, 1] - CENTER, dst[ok][:, 2] - CENTER
    back = ycc_to_rgb(ye, cbe, cre)
    print("\nYCC2RGB(用引擎自己的 YCC 回算,对引擎的最终 RGB):")
    report("R", back[:, 0], z["ZcTaskYCC2RGB_out"][ok][:, 0])
    report("G", back[:, 1], z["ZcTaskYCC2RGB_out"][ok][:, 1])
    report("B", back[:, 2], z["ZcTaskYCC2RGB_out"][ok][:, 2])

    yg_in, yg_out = z["ZcTaskYGamma_in"][ok], z["ZcTaskYGamma_out"][ok]
    print("\nYGamma 只动平面0:  平面1 变了 %.4f%%   平面2 变了 %.4f%%"
          % ((yg_in[:, 1] != yg_out[:, 1]).mean() * 100,
             (yg_in[:, 2] != yg_out[:, 2]).mean() * 100))
    print("  平面0 均值 %.1f -> %.1f" % (yg_in[:, 0].mean(), yg_out[:, 0].mean()))


if __name__ == "__main__":
    main()
