r"""从注入的冲激图案里读出 SIMDSharpness 的冲激响应与传递曲线。

输入完全受控:平坦底色 BG 上摆孤立冲激,振幅逐格不同。于是
  * 冲激周围的 delta 图案 = 算子的冲激响应(除以振幅即归一化的核)
  * 中心的 delta 对振幅的曲线 = 传递函数(死区 / 斜率 / 削波)

用法: python sharp_kernel.py sharpprobe/inj_imp4000.npz [半径]
"""
import sys
from pathlib import Path

import numpy as np

SCR = Path(__file__).parent


def main():
    z = np.load(sys.argv[1] if len(sys.argv) > 1 else SCR / "sharpprobe/inj_imp4000.npz",
                allow_pickle=True)
    r = int(sys.argv[2]) if len(sys.argv) > 2 else 8
    a = z["inp"].astype(np.float64)
    b = z["out"].astype(np.float64)
    bg, sp, tile = z["meta"]
    amps = z["amps"]
    d = b - a
    h, w = a.shape
    print(f"底色 {bg}  间距 {sp}  tile {tile}   参数 {z['params']}")
    print(f"输出在平坦区的值: {np.unique(b[4:40, 4:40])[:5]}   (应当 == 底色)")

    # 冲激位置与振幅一一对应,和注入时的遍历顺序一致
    pos, n = [], 0
    for y in range(sp, h - sp, sp):
        for x in range(sp, w - sp, sp):
            pos.append((y, x, int(amps[n % len(amps)])))
            n += 1

    # --- 传递曲线:中心像素
    print("\n振幅 A -> 中心 delta,以及最近邻 delta")
    print(f"{'A':>7} {'n':>4} {'d(0,0)':>10} {'d(0,1)':>9} {'d(1,1)':>9} {'d(0,2)':>9} {'d(0,3)':>9}")
    by_amp = {}
    for y, x, A in pos:
        by_amp.setdefault(A, []).append((y, x))
    for A in sorted(by_amp, key=lambda v: (v < 0, abs(v))):
        cells = by_amp[A]
        g = [np.median([d[y + dy, x + dx] for y, x in cells])
             for dy, dx in [(0, 0), (0, 1), (1, 1), (0, 2), (0, 3)]]
        print(f"{A:>7} {len(cells):>4} {g[0]:>10.1f} {g[1]:>9.1f} {g[2]:>9.1f} "
              f"{g[3]:>9.1f} {g[4]:>9.1f}")

    # --- 冲激响应:用最大的正负振幅
    for A in (max(amps), min(amps)):
        cells = by_amp[int(A)]
        ker = np.zeros((2 * r + 1, 2 * r + 1))
        for dy in range(-r, r + 1):
            for dx in range(-r, r + 1):
                ker[dy + r, dx + r] = np.median([d[y + dy, x + dx] for y, x in cells])
        np.set_printoptions(precision=4, suppress=True, linewidth=220)
        print(f"\n=== A={A} 的响应 / A  (n={len(cells)})")
        print(ker / A)
        print(f"  和 {ker.sum() / A:+.5f}   中心 {ker[r, r] / A:+.5f}")


if __name__ == "__main__":
    main()
