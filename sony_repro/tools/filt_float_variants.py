"""剩下那 0.15% 到底是不是浮点语义 —— 别再当假设用。

端到端已经 99.85%~99.998%,误差中位是 0、最大十几个 level,一直被记作"浮点累加
顺序不同"。可那从来只是**说法**:没验过,也就谈不上能不能做到逐位 100%。

两件事要分开:

  1. **错的点是不是都卡在阈值边界上。** 若是,几何与权重就都对了,差别只可能来自
     最低几位的舍入 —— 边界把 1 ulp 放大成一个抽头的进出。若不是,那还有别的
     没解出来,不能拿浮点当借口(这个判据在 green_edge_check.py 用过一次,当时
     正是它戳穿了"只是浮点"的说法)。
  2. **哪一种浮点语义能对上。** AVX2 有 FMA:`a*b+c` 只舍入一次,而 numpy 分开
     算乘和加要舍入两次。base 那一步 `w*centre + (1-w)*mean` 正是这个形状。

所以这里枚举几种写法,逐位比一比。用 `rawnr_full_probe.py` 的四路捕获。
"""
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import numpy as np

from llr_worker.sony.rawnr_simd import (
    BASE_GREEN_OTHER,
    BASE_GREEN_OWN,
    BASE_RB,
    FILT_MARGIN,
    LEVEL_MAX,
    TAP_SPACING,
    TAPS_PER_SIDE,
)

HERE = os.path.dirname(os.path.abspath(__file__))
SLOTS = ["rb0", "g0", "g1", "rb1"]


def sh(a, dy, dx, r):
    h, w = a.shape
    return a[r + dy:h - r + dy, r + dx:w - r + dx]


def run(z, slot, *, base_f64=False, acc_f64=False, le=False, mean_f64=False):
    """一份可配置的 filt。每个开关对应一种可能的浮点语义。"""
    ref = z[f"{slot}_ref"]
    det = z[f"{slot}_detail"]
    tbl0 = z[f"{slot}_tbl0"]
    blend = int(z[f"{slot}_tbl3"][0])
    gain = int(z[f"{slot}_gain"][0])
    limit = int(z[f"{slot}_limit"][0])
    off = float(z[f"{slot}_offset"][0])
    green = slot.startswith("g")
    base_own = BASE_GREEN_OWN if green else BASE_RB
    base_other = BASE_GREEN_OTHER[int(slot[1])] if green else ()
    other = z[f"{slot}_ref2"] if green else None

    r = FILT_MARGIN
    thr = sh(tbl0[np.clip(ref, 0, LEVEL_MAX).astype(np.int32)].astype(np.float32),
             0, 0, r)
    dt = np.float64 if base_f64 else np.float32
    members = sum(sh(ref, dy, dx, r).astype(dt) for dy, dx in base_own)
    if base_other:
        members = members + sum(sh(other, dy, dx, r).astype(dt)
                                for dy, dx in base_other)
    n = len(base_own) + len(base_other)
    centre = sh(ref, 0, 0, r)
    w = dt(blend / 1024.0)
    base = w * centre.astype(dt) + (dt(1.0) - w) * (members / dt(n))
    base = base.astype(np.float32)

    at = np.float64 if acc_f64 else np.float32
    total = np.zeros(base.shape, at)
    count = np.zeros(base.shape, at)
    offs = [k * TAP_SPACING for k in range(-TAPS_PER_SIDE, TAPS_PER_SIDE + 1)]
    margin = np.full(base.shape, np.inf, np.float32)
    for dy in offs:
        for dx in offs:
            v = sh(ref, dy, dx, r)
            gap = np.abs(base - v) - thr
            ok = (gap <= 0 if le else gap < 0).astype(at)
            total += ok * v.astype(at)
            count += ok
            margin = np.minimum(margin, np.abs(gap))
    mt = np.float64 if mean_f64 else np.float32
    mean = np.where(count > 0, total.astype(mt) / np.maximum(count, 1).astype(mt),
                    centre.astype(mt)).astype(np.float32)
    boost = np.clip(sh(det, 0, 0, r) * np.float32(gain / 256.0), -limit, limit)
    got = np.clip(mean + boost - np.float32(off), 0.0, 262143.0)
    return got, margin


def main() -> int:
    name = sys.argv[1] if len(sys.argv) > 1 else "rawnr_full_fl_test.npz"
    z = np.load(os.path.join(HERE, name))
    r, W = FILT_MARGIN, 6
    print(f"{name}\n")

    print("  1. 错的点是不是都卡在阈值边界上(按「最近的抽头离阈值多远」分档)")
    for slot in SLOTS:
        got, margin = run(z, slot)
        h, w = z[f"{slot}_ref"].shape
        eng = z[f"{slot}_out"][W:h - W, W:w - W]
        sl = (slice(W - r, h - W - r), slice(W - r, w - W - r))
        bad = got[sl] != eng
        mg = margin[sl]
        line = []
        for lo, hi in ((0, 1e-3), (1e-3, 1e-2), (1e-2, 0.1), (0.1, 1.0), (1.0, np.inf)):
            m = (mg >= lo) & (mg < hi)
            line.append(f"[{lo:g},{hi:g}) {100 * float(bad[m].mean()) if m.any() else 0:6.2f}%")
        far = mg > 1.0
        print(f"    {slot}: 总错 {100 * float(bad.mean()):.4f}%   " + "  ".join(line))
        print(f"          离阈值 1.0 以上的点里错的占 "
              f"{100 * float(bad[far].mean()) if far.any() else 0:.4f}%"
              f"({int(bad[far].sum())} 个)")

    print("\n  2. 换浮点语义(逐位相同率)")
    variants = [
        ("当前(全 float32)", {}),
        ("base 用 float64 中间量", {"base_f64": True}),
        ("累加用 float64", {"acc_f64": True}),
        ("均值除法用 float64", {"mean_f64": True}),
        ("判据取 <=", {"le": True}),
        ("base+累加+均值都 float64", {"base_f64": True, "acc_f64": True,
                                 "mean_f64": True}),
    ]
    for label, kw in variants:
        cells = []
        for slot in SLOTS:
            got, _ = run(z, slot, **kw)
            h, w = z[f"{slot}_ref"].shape
            eng = z[f"{slot}_out"][W:h - W, W:w - W]
            sl = (slice(W - r, h - W - r), slice(W - r, w - W - r))
            cells.append(f"{100 * float(np.mean(got[sl] == eng)):7.4f}%")
        print(f"    {label:<24} " + "  ".join(cells))
    print("    " + " " * 24 + "  ".join(f"{s:>8}" for s in SLOTS))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
