"""40% 逐位相同,是几何错了还是浮点顺序不同 —— 这两者读数完全不同。

孤立点实验已经把绿色核的形状定死了(`green_threshold_solve.py`):抽头是自身
平面的 5x5 间距 2、等权,base 是中心与 25 抽头均值各半,除数是通过阈值的个数。
两个跳变点(|delta| ≈ 46.2 与 50)都被这一组预测中。

那么在白噪声捕获上只有 40% 逐位相同,只剩两种可能:

  * **几何仍有错** —— 那么误差应当**遍布**,与"某个抽头离阈值多近"无关;
  * **只是浮点累加顺序不同** —— 那么误差必须**只出现在**至少有一个抽头
    卡在阈值边界的点上,因为顺序只影响最低几位,只有边界能把它放大成
    一个抽头的进出。

所以判据是:把每个点按「最接近阈值的那个抽头离阈值多远」分档,看误差率随之
怎么变。若误差率在边界档接近 100%、在远离档接近 0%,几何就是对的。
"""
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
S, TAPS = 2, 2
FILT_MARGIN = S * TAPS
W = 5


def shifted(a, dy, dx, r):
    h, w = a.shape
    return a[r + dy:h - r + dy, r + dx:w - r + dx]


def main():
    name = sys.argv[1] if len(sys.argv) > 1 else "green_impulse_fl_test_w0_noise.npz"
    z = np.load(os.path.join(HERE, name))
    ref, out, tbl0, tbl3 = z["ref"], z["out"], z["tbl0"], z["tbl3"]
    off = float(z["offset"][0])
    h, w = ref.shape
    r = FILT_MARGIN

    centre = shifted(ref, 0, 0, r)
    offs = [k * S for k in range(-TAPS, TAPS + 1)]
    m25 = sum(shifted(ref, dy, dx, r) for dy in offs
              for dx in offs) / np.float32(25.0)
    blend = np.float32(float(tbl3[0]) / 1024.0)
    base = blend * centre + (np.float32(1.0) - blend) * m25
    thr_c = tbl0[np.clip(centre, 0, 16383).astype(np.int32)].astype(np.float32)

    total = np.zeros_like(base)
    count = np.zeros_like(base)
    margin = np.full_like(base, np.inf)
    for dy in offs:
        for dx in offs:
            v = shifted(ref, dy, dx, r)
            gap = np.abs(base - v) - thr_c        # <0 接纳,>0 拒绝
            ok = (gap < 0).astype(np.float32)
            total += ok * v
            count += ok
            margin = np.minimum(margin, np.abs(gap))
    mean = np.where(count > 0.0, total / np.maximum(count, 1.0), centre)
    got = np.clip(mean - np.float32(off), 0.0, 262143.0)

    sl = (slice(W - r, h - W - r), slice(W - r, w - W - r))
    eng = out[W:h - W, W:w - W]
    err = got[sl] - eng
    mg = margin[sl]
    bad = err != 0

    print(f"{name}")
    print(f"  逐位相同 {100*float(np.mean(~bad)):.4f}%  "
          f"|误差| 最大 {float(np.abs(err).max()):.4g}  "
          f"中位 {float(np.median(np.abs(err))):.4g}")
    print(f"\n  按「最近的抽头离阈值多远」分档:")
    edges = [0, 1e-3, 1e-2, 0.1, 0.5, 1.0, 2.0, 5.0, np.inf]
    for lo, hi in zip(edges, edges[1:]):
        m = (mg >= lo) & (mg < hi)
        if not m.any():
            continue
        print(f"    [{lo:7.3g}, {hi:7.3g})  n={int(m.sum()):7d}  "
              f"错的占 {100*float(bad[m].mean()):7.3f}%")
    print("\n  边界档接近 100%、远离档接近 0% => 几何是对的,差别只是浮点累加顺序。")
    print("  若各档都差不多 => 几何仍有错,别拿浮点当借口。")

    far = mg > 1.0
    if far.any():
        print(f"\n  离阈值 1.0 以上的点里,错的占 "
              f"{100*float(bad[far].mean()):.4f}% ({int(bad[far].sum())} 个)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
