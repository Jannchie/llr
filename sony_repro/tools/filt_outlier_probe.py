"""那些**离阈值很远却仍然算错**的点,到底是什么。

`filt_float_variants.py` 把残差按"最近的抽头离阈值多远"分了档:边界档的错误率
明显更高(g0 在 [0,0.001) 档是 6.59%),这支持"浮点最低位把抽头翻进翻出"的说法。
可它没归零 —— g0 还有 96 个点离阈值 1.0 以上照样错。1.0 的间隔,浮点翻不动。

所以那 96 个是另一回事,而且它们是 99.847% 通向 100% 的路上剩下的东西。这里把
它们单独挑出来看:接纳了几个抽头、错了多少、错在哪个方向。

已知的头号嫌疑是 `count == 0`:那时引擎的行为是从捕获里读出来的(取中心),只有
三个样本作依据(`count_zero_probe.py`),本来就薄。
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


def compute(z, slot):
    ref, det = z[f"{slot}_ref"], z[f"{slot}_detail"]
    tbl0 = z[f"{slot}_tbl0"]
    blend = int(z[f"{slot}_tbl3"][0])
    gain, limit = int(z[f"{slot}_gain"][0]), int(z[f"{slot}_limit"][0])
    off = float(z[f"{slot}_offset"][0])
    green = slot.startswith("g")
    base_own = BASE_GREEN_OWN if green else BASE_RB
    base_other = BASE_GREEN_OTHER[int(slot[1])] if green else ()
    other = z[f"{slot}_ref2"] if green else None

    r = FILT_MARGIN
    thr = sh(tbl0[np.clip(ref, 0, LEVEL_MAX).astype(np.int32)].astype(np.float32),
             0, 0, r)
    members = sum(sh(ref, dy, dx, r) for dy, dx in base_own)
    if base_other:
        members = members + sum(sh(other, dy, dx, r) for dy, dx in base_other)
    n = len(base_own) + len(base_other)
    centre = sh(ref, 0, 0, r)
    w = np.float32(blend / 1024.0)
    base = w * centre + (np.float32(1.0) - w) * (members / np.float32(n))

    total = np.zeros_like(base)
    count = np.zeros_like(base)
    margin = np.full(base.shape, np.inf, np.float32)
    offs = [k * TAP_SPACING for k in range(-TAPS_PER_SIDE, TAPS_PER_SIDE + 1)]
    for dy in offs:
        for dx in offs:
            v = sh(ref, dy, dx, r)
            gap = np.abs(base - v) - thr
            # 中心不参与判据(2.19.8)。margin 也要把它排除,否则"最近的抽头离阈值
            # 多远"会被一个根本不受判据约束的抽头带偏。
            if dy == 0 and dx == 0:
                total += v
                count += np.float32(1.0)
                continue
            ok = (gap < 0).astype(np.float32)
            total += ok * v
            count += ok
            margin = np.minimum(margin, np.abs(gap))
    mean = np.where(count > 0.0, total / np.maximum(count, 1.0), centre)
    boost = np.clip(sh(det, 0, 0, r) * np.float32(gain / 256.0), -limit, limit)
    got = np.clip(mean + boost - np.float32(off), 0.0, 262143.0)
    return got, count, margin, centre, base, mean, thr


def main() -> int:
    name = sys.argv[1] if len(sys.argv) > 1 else "rawnr_full_fl_test.npz"
    z = np.load(os.path.join(HERE, name))
    r, W = FILT_MARGIN, 6
    print(f"{name}\n")
    for slot in SLOTS:
        got, count, margin, centre, base, mean, thr = compute(z, slot)
        h, w = z[f"{slot}_ref"].shape
        eng = z[f"{slot}_out"][W:h - W, W:w - W]
        sl = (slice(W - r, h - W - r), slice(W - r, w - W - r))
        err = got[sl] - eng
        bad = err != 0
        far = margin[sl] > 1.0
        sel = bad & far
        nfar = int(sel.sum())
        print(f"  {slot}: 错 {int(bad.sum())} 个,其中离阈值 1.0 以上的 {nfar} 个")
        if nfar == 0:
            continue
        c = count[sl][sel]
        e = err[sel]
        print(f"    这些点的 count:  最小 {int(c.min())}  最大 {int(c.max())}  "
              f"中位 {int(np.median(c))}   count==0 的有 {int((c == 0).sum())} 个"
              f",count==25 的有 {int((c == 25).sum())} 个")
        print(f"    误差:  中位 {float(np.median(e)):+.4g}   "
              f"范围 [{float(e.min()):+.4g}, {float(e.max()):+.4g}]   "
              f"偏正的占 {100 * float((e > 0).mean()):.1f}%")
        # 输出是否顶到了 clip 的边界 —— 那是另一种"不是浮点"的错法。
        at0 = int((got[sl][sel] == 0).sum())
        print(f"    输出恰为 0 的 {at0} 个;引擎那边恰为 0 的 "
              f"{int((eng[sel] == 0).sum())} 个")
        # 与 count 的关系:若集中在某个 count 值上,那就是那条分支还没解对。
        vals, cnts = np.unique(c.astype(np.int32), return_counts=True)
        top = sorted(zip(cnts, vals), reverse=True)[:5]
        print("    count 的分布(前 5):  " +
              "  ".join(f"count={v}:{n}个" for n, v in top))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
