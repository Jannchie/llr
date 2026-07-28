r"""离线复算 SIMDSharpness,与引擎抓下来的真实 tile 逐像素比。

解出来的算子(注入白噪声 + 最小二乘,残差 std 0.286 = 纯截断):

    m   = outer([1,6,15,20,15,6,1], [1,6,15,20,15,6,1])        # 可分离二项式模糊 x4096
    K   = 25.6*δ - 0.0025*m - 3.84*N4                          # N4 = 上下左右四邻
        = 10.24*(δ - B) + 15.36*(δ - C4)                       # B = m/4096, C4 = 四邻均值
    hp  = K ⊛ y
    d   = |hp| < 40.96*c ? 0 : floor(amp * hp)                  # 硬死区,阈值只看 hp
    out = clamp(y + d, 0, 16383)

    amp = (lvl + 100) * 0.5 * tbl1060 * (rng / 50) / 1024
    lvl = 10 * (Sony:0x2006 Sharpness - 4)        # 机内档位 0..9,非法值按 +4
    rng = 10 * (Sony:0x2035 SharpnessRange) + 20  # 机内档位 0..5,负值 -> 0(完全不锐化)
    c   = v < 0 ? (v + 100) * 0.25 : v + 25       # v = opts[0x214],机内图恒为 0 -> c = 25
    tbl1060 实测恒为 1.7

用法: python sharp_check.py [tiles_SIMDSharpness.npz] [--amp 0.0830078125] [--c 25]
"""
import sys
from pathlib import Path

import numpy as np

SCR = Path(__file__).parent
BINOM = np.array([1, 6, 15, 20, 15, 6, 1], np.float64)


def kernel():
    m = np.outer(BINOM, BINOM)
    K = -0.0025 * m
    K[3, 3] += 25.6
    for dy, dx in ((0, 1), (0, -1), (1, 0), (-1, 0)):
        K[3 + dy, 3 + dx] -= 3.84
    return K


def highpass(y):
    K = kernel()
    pad = np.pad(y, 3, mode="edge")
    out = np.zeros_like(y, dtype=np.float64)
    for dy in range(-3, 4):
        for dx in range(-3, 4):
            c = K[dy + 3, dx + 3]
            if c:
                out += c * pad[3 + dy:3 + dy + y.shape[0], 3 + dx:3 + dx + y.shape[1]]
    return out


def apply(y, amp, c=25.0, hi=32767):   # 上钳是 32767(DAT_1404667a0),不是 16383
    hp = highpass(y.astype(np.float64))
    d = np.floor(amp * hp)
    d[np.abs(hp) < 40.96 * c] = 0
    return np.clip(y + d, 0, hi)


def _opt(flag, default):
    return float(sys.argv[sys.argv.index(flag) + 1]) if flag in sys.argv else default


def main():
    path = sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith("--") \
        else SCR / "tiles_SIMDSharpness.npz"
    amp = _opt("--amp", 85 / 1024)
    c = _opt("--c", 25.0)
    z = np.load(path)
    tiles = sorted(int(k[1:].split("_")[0]) for k in z if k.endswith("_in"))
    print(f"amp={amp}  c={c}  hp 阈值={40.96 * c}")
    for t in tiles:
        y = z[f"t{t}_in"][..., 0].astype(np.int64)
        eng = z[f"t{t}_out"][..., 0].astype(np.int64)
        ours = apply(y, amp, c).astype(np.int64)
        m = np.zeros(y.shape, bool)
        m[8:-8, 8:-8] = True
        diff = (ours - eng)[m]
        same = 100.0 * (diff == 0).mean()
        print(f"  tile {t:>3}: 逐位相同 {same:8.4f}%   最大差 {np.abs(diff).max():>5}  "
              f"|差|>1 的比例 {100 * (np.abs(diff) > 1).mean():.4f}%  "
              f"(引擎改动 {100 * ((eng - y)[m] != 0).mean():.1f}%)")


if __name__ == "__main__":
    main()
