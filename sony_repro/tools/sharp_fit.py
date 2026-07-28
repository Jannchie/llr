r"""用注入的白噪声帧最小二乘拟合 SIMDSharpness 的核 —— 死区关掉后它是严格线性的。

孤立冲激能看清核的形状,但最外圈系数只有 1~2 个 LSB,读不准。噪声帧每个像素都是
一次独立观测,同样一次运行就能把系数定到 1e-4。残差(应当只剩截断的 0.5)顺便
证明「除了死区之外没有别的非线性」。

用法: python sharp_fit.py sharpprobe/inj_noise.npz [半径] [amp]
"""
import sys
from pathlib import Path

import numpy as np

SCR = Path(__file__).parent


def main():
    z = np.load(sys.argv[1] if len(sys.argv) > 1 else SCR / "sharpprobe/inj_noise.npz",
                allow_pickle=True)
    r = int(sys.argv[2]) if len(sys.argv) > 2 else 4
    amp = float(sys.argv[3]) if len(sys.argv) > 3 else None
    a = z["inp"].astype(np.float64)
    b = z["out"].astype(np.float64)
    d = b - a
    h, w = a.shape
    print(f"参数 {z['params']}   底色/幅度 {z['meta']}")
    print(f"delta: 改动 {100 * (d != 0).mean():.2f}%  std {d.std():.2f}  "
          f"|d|max {np.abs(d).max():.0f}")

    pad = 40
    ys, xs = np.mgrid[pad:h - pad, pad:w - pad]
    ys, xs = ys.ravel(), xs.ravel()
    cols = [a[ys + dy, xs + dx] for dy in range(-r, r + 1) for dx in range(-r, r + 1)]
    A = np.stack(cols, 1)
    y = d[ys, xs]
    k, *_ = np.linalg.lstsq(A, y, rcond=None)
    resid = y - A @ k
    K = k.reshape(2 * r + 1, 2 * r + 1)
    np.set_printoptions(precision=6, suppress=True, linewidth=250)
    print(f"\n拟合核 {2*r+1}x{2*r+1}   残差 std {resid.std():.4f} "
          f"(纯截断应当 ~0.29)  最大 |残差| {np.abs(resid).max():.1f}   核和 {K.sum():+.6f}")
    print(K)
    if amp:
        print(f"\n除以 amp={amp}:")
        print(K / amp)
        print("  x1024:")
        print(np.round(K / amp * 1024, 2))


if __name__ == "__main__":
    main()
