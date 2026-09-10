"""解绿色滤波核 `0x3a0c30` 的抽头集 —— 在**平坦区**上做,让阈值选择失效。

为什么之前解不动:sigma 滤波器的抽头是被阈值挑过的,直接对全图回归,拟合的是
"挑选"而不是"几何"。`static-rawnr.md` §6 卡在这里(套 R/B 几何只到 40.9%,
回归只能确认"等权"和"自身平面的显著项在偶数偏移上")。

绕开的办法:只在**局部足够平坦**的地方解。那里所有抽头都在阈值内,选择恒等于
全通过,于是

    mean = (Σ 抽头) / 25          (系数 1/25 是从反汇编读出来的)
    out  = clamp(mean + clamp(d*gain/256, ±limit) − offset, 0, 262143)

反解出 mean,再对候选抽头池做最小二乘 —— 这时候拟合的就是纯几何了。

候选池取两个绿平面各自的间距 1 与间距 2 的邻域,不预设哪一半来自谁:
`ref2` 在捕获里就是另一绿平面,而两个绿相位在全分辨率上错开半个像素,
所以另一平面的合理抽头未必落在偶数偏移上,不能照搬自身平面的间距。

⚠️ 判据不是 R²。等权平均在平坦区里**天然**拟合得好 —— 邻域各点本来就几乎相等,
任何一组和为 1 的权重都能得高 R²。真正的判据是:解出的系数是否**成群地落在
1/25 = 0.04 上**,以及把这组抽头拿回**全图**(带阈值选择)跑一遍能解释多少。
后者才是 §6 里 40.9% 那个数字的可比对象。
"""
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
CAP = "rawnr_kern_fl_test_g_s0w0.npz"
W = 5           # 引擎写入区域的边距
R = 6           # 候选池的最大半径,留够余量
TAP_COEFF = 1.0 / 25.0


def shifted(a, dy, dx, r):
    h, w = a.shape
    return a[r + dy:h - r + dy, r + dx:w - r + dx]


def main():
    z = np.load(os.path.join(HERE, CAP))
    d, ref, ref2, out = z["detail"], z["ref"], z["ref2"], z["out"]
    gain, limit = int(z["gain"][0]), int(z["limit"][0])
    off = float(z["offset"][0])
    h, w = ref.shape

    # 反解 mean:out 是钳位过的,所以只用没触到钳位的点。
    boost = np.clip(d * np.float32(gain / 256.0), -limit, limit)
    mean_full = out - boost + np.float32(off)
    unclipped = (out > 0.5) & (out < 262142.0)

    # 平坦:5x5(间距2)邻域的极差远小于阈值,才敢说"所有抽头都通过"。极差小于
    # 阈值是全通过的充分条件(base 落在极差区间内),取一半是留余量。
    # 这份捕获的阈值表是 11..53,而 ref 的均值 809 处大约是 51。
    stack = np.stack([shifted(ref, dy, dx, R) for dy in (-4, -2, 0, 2, 4)
                      for dx in (-4, -2, 0, 2, 4)])
    spread = stack.max(axis=0) - stack.min(axis=0)
    thr_here = float(np.median(z["tbl0"][np.clip(ref, 0, 16383).astype(np.int32)]))
    cut = thr_here * 0.8
    print(f"  阈值中位 {thr_here:.0f},平坦判据取极差 < {cut:.0f}")
    print(f"  极差分位 p1={np.quantile(spread,0.01):.0f} p5={np.quantile(spread,0.05):.0f}"
          f" p25={np.quantile(spread,0.25):.0f} 中位={np.median(spread):.0f}")
    flat = spread < cut

    core = (slice(R, h - R), slice(R, w - R))
    # R > W,所以 core 已经整个落在引擎写过的区域里,不必再裁一次。
    # (早先这里按 `W - R` 又裁了一刀,那是个负数,把 mask 砍成了最后一行。)
    mask = flat & unclipped[core]
    print(f"平坦且未钳位的样本:{int(mask.sum())} / {mask.size} "
          f"({100 * mask.mean():.2f}%)")
    if mask.sum() < 5000:
        print("样本太少,换判据或换捕获")
        return 1

    y = mean_full[core][mask].astype(np.float64)

    cols, names = [], []
    for plane, pname in ((ref, "self"), (ref2, "other")):
        for dy in range(-4, 5):
            for dx in range(-4, 5):
                if abs(dy) > 4 or abs(dx) > 4:
                    continue
                cols.append(shifted(plane, dy, dx, R)[mask].astype(np.float64))
                names.append(f"{pname}({dy:+d},{dx:+d})")
    A = np.stack(cols, axis=1)
    print(f"候选 {A.shape[1]} 个,样本 {A.shape[0]}")

    coef, *_ = np.linalg.lstsq(A, y, rcond=None)
    order = np.argsort(-np.abs(coef))
    print(f"\n  系数和 {coef.sum():.4f}  (等权 25 抽头应为 1.0)")
    print(f"  1/25 = {TAP_COEFF:.4f}\n")
    print("  最大的 32 个:")
    for i in order[:32]:
        bar = "#" * int(round(abs(coef[i]) / TAP_COEFF * 8))
        print(f"    {names[i]:<16} {coef[i]:+.4f}  {bar}")

    near = [i for i in range(len(coef)) if abs(coef[i] - TAP_COEFF) < TAP_COEFF * 0.35]
    print(f"\n  落在 1/25 ±35% 的:{len(near)} 个")
    for i in sorted(near, key=lambda j: names[j]):
        print(f"    {names[i]:<16} {coef[i]:+.4f}")
    self_n = sum(1 for i in near if names[i].startswith("self"))
    print(f"  其中 self {self_n} 个,other {len(near) - self_n} 个"
          f"  (§6 说 25 个抽头必然含另一绿平面)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
