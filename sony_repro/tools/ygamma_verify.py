r"""逐位验证 YGamma 的复刻式子。

从内存读出来的四个量(ygamma_probe.py)带进反汇编给出的公式:

    black_int = -((bl + (bl>>31 & 3)) >> 2)
    black     = black_int * (1/512) * 32767
    scale     = 512 / ((0x200 - wl) - black_int)
    y = clamp(((max(0, lut[Y]) - black) * scale - pivot) * contrast + pivot, 0, 16383)

实测三张的 bl = wl = pivot = 0,于是整段塌缩成 `clamp(lut[Y] * contrast, 0, 16383)`,
contrast = 1.0546875 = 135/128。这里拿引擎自己的 in/out 逐像素核对,不是估。

用法: 先 stage_frame.py 抓 ZcTaskYGamma:in ZcTaskYGamma:out,再跑本工具。
"""
from pathlib import Path

import numpy as np

TOOLS = Path(__file__).parent
K, K1, K2 = 512.0, 1.0 / 512.0, 32767.0     # 0x1404df248 / 0x140466794 / 0x1404667a0


def ygamma(y, lut, pivot, contrast, bl, wl):
    black_int = -((bl + ((bl >> 31) & 3)) >> 2)
    black = black_int * K1 * K2
    scale = K / ((0x200 - wl) - black_int)
    v = np.maximum(lut[np.clip(y, 0, lut.size - 1)].astype(np.float64), 0)
    return np.clip(((v - black) * scale - pivot) * contrast + pivot, 0, 16383)


def main():
    p = np.load(TOOLS / "ygamma_lut.npz")
    z = np.load(TOOLS / "stage_frames.npz")
    a, b = z["ZcTaskYGamma_in"], z["ZcTaskYGamma_out"]
    ok = a.max(-1) > 0

    got = ygamma(a[..., 0][ok].astype(np.int64), p["lut"],
                 float(p["pivot"]), float(p["contrast"]), int(p["bl"]), int(p["wl"]))
    want = b[..., 0][ok].astype(np.float64)
    # SIMD 路径用哪种取整不写在公式里,只能试:cvtps2dq 是就近偶数,cvttps2dq 是截断
    for name, f in (("就近", np.rint), ("截断", np.trunc), ("向上", np.ceil)):
        d = np.abs(f(got) - want)
        print(f"像素 {ok.sum()}  取整={name}  逐位相同 {100 * (d == 0).mean():7.4f}%   "
              f"差<=1 {100 * (d <= 1).mean():.4f}%   最大差 {d.max():.0f}")

    # 色度平面必须一动不动
    for k, nm in ((1, "Cr"), (2, "Cb")):
        print(f"  平面{k} ({nm}) 改动 {100 * (a[..., k][ok] != b[..., k][ok]).mean():.4f}%")


if __name__ == "__main__":
    main()
