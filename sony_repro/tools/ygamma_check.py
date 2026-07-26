r"""YGamma 到底动了多少?它是不是「系统性偏饱和」的来源?

YGamma(RVA 0x36f030)在 YCC 段里、ChromaSuppres 之后 YCC2RGB 之前,只动平面0(Y)。
我们的管线完全没有这一段。假说:它抬高 Y 而不动 Cb/Cr,于是引擎的画面相对更不饱和,
我们不做就偏饱和 5~11%。

但基线里我们的亮度与机内 JPEG 只差 ±0.008,与「Y 被抬 5%」矛盾 —— 所以要实测,
不能推断。用 stage_frame.py 抓 ZcTaskYGamma:in/out 之后跑这个。
"""
import sys
from pathlib import Path

import numpy as np

NPZ = Path(__file__).parent / "stage_frames.npz"


def main():
    z = np.load(sys.argv[1] if len(sys.argv) > 1 else NPZ)
    a, b = z["ZcTaskYGamma_in"].astype(np.float64), z["ZcTaskYGamma_out"].astype(np.float64)
    ok = a.max(-1) > 0                      # 拼不满的边缘不算

    print("平面   均值(前 -> 后)        改动比例      中位增益")
    for k, name in enumerate(("Y", "Cr", "Cb")):
        x, y = a[..., k][ok], b[..., k][ok]
        changed = (x != y).mean() * 100
        # 色度平面以 8192 为零点,增益要绕开零点算
        if k:
            g = np.median((y[np.abs(x - 8192) > 200] - 8192)
                          / (x[np.abs(x - 8192) > 200] - 8192))
        else:
            g = np.median(y[x > 200] / x[x > 200])
        print(f"  {name:<3} {x.mean():9.1f} -> {y.mean():9.1f}   {changed:6.2f}%   {g:.4f}")

    # Y 的传递曲线:它是不是一条单调映射?若是,离线只要一张 LUT 就能复刻
    x, y = a[..., 0][ok].astype(np.int32), b[..., 0][ok].astype(np.int32)
    lut = np.full(16384, -1, np.int32)
    lut[x] = y
    seen = lut >= 0
    mono = np.all(np.diff(lut[seen]) >= 0)
    print(f"\nY 的映射: 覆盖 {seen.sum()} 个输入值, 单调={mono}, "
          f"一对多={'是' if len(set(zip(x.tolist(), y.tolist()))) > seen.sum() else '否'}")
    for v in (0, 512, 1024, 2048, 4096, 8192, 12288, 16000):
        near = np.abs(x - v) < 32
        if near.any():
            print(f"   Y {v:>5} -> {y[near].mean():8.1f}  (x{y[near].mean() / max(v, 1):.4f})")


if __name__ == "__main__":
    main()
