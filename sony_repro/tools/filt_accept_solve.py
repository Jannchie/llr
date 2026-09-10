"""反推引擎在那些点上到底接纳了哪几个抽头。

`filt_outlier_probe.py` 把剩下的错误点收窄成一类:**count 很小**(g0 的中位是 2)、
而且所有抽头都离阈值 1.0 以上。后一条很要紧 —— 接纳集本该是板上钉钉的,mean 也
就该是确定值,浮点动不了它。可误差有 +3.8。那就只剩一种解释:**引擎接纳的抽头和
我们不一样**。

既然 mean = 接纳集的平均,就可以反着算:

    引擎的 mean = out + offset − boost

拿它去试"多接纳一个被我们拒掉的抽头"或"少接纳一个我们接了的抽头",看哪一种能把
数对上。对上了,就知道引擎在这些点上把判据放宽/收紧到了哪里 —— 也就知道 base
或阈值在强边缘处还差了什么。
"""
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import numpy as np

from llr_worker.sony.rawnr_simd import FILT_MARGIN, TAP_SPACING, TAPS_PER_SIDE
from filt_outlier_probe import compute, sh  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
TOL = 1e-3


def main() -> int:
    name = sys.argv[1] if len(sys.argv) > 1 else "rawnr_full_fl_test.npz"
    slot = sys.argv[2] if len(sys.argv) > 2 else "g0"
    z = np.load(os.path.join(HERE, name))
    got, count, margin, centre, base, mean, thr = compute(z, slot)
    ref, det = z[f"{slot}_ref"], z[f"{slot}_detail"]
    gain, limit = int(z[f"{slot}_gain"][0]), int(z[f"{slot}_limit"][0])
    off = float(z[f"{slot}_offset"][0])
    h, w = ref.shape
    r, W = FILT_MARGIN, 6
    eng_full = z[f"{slot}_out"]
    sl = (slice(W - r, h - W - r), slice(W - r, w - W - r))
    eng = eng_full[W:h - W, W:w - W]

    err = got[sl] - eng
    sel = (err != 0) & (margin[sl] > 1.0)
    ys, xs = np.nonzero(sel)
    print(f"{name}  {slot}: 要查的点 {ys.size} 个\n")

    boost = np.clip(sh(det, 0, 0, r) * np.float32(gain / 256.0), -limit, limit)
    mean_eng_all = eng.astype(np.float64) + off - boost[sl].astype(np.float64)

    offs = [k * TAP_SPACING for k in range(-TAPS_PER_SIDE, TAPS_PER_SIDE + 1)]
    taps = np.stack([sh(ref, dy, dx, r)[sl] for dy in offs for dx in offs], axis=-1)
    ok = np.stack([(np.abs(base - sh(ref, dy, dx, r)) < thr)[sl]
                   for dy in offs for dx in offs], axis=-1)

    tally = {"加一个被拒的": 0, "去一个已接的": 0, "全部 25 个": 0,
             "只有中心": 0, "对不上": 0}
    extra_gap = []
    for k in range(ys.size):
        y, x = ys[k], xs[k]
        v = taps[y, x].astype(np.float64)
        a = ok[y, x]
        m = float(mean_eng_all[y, x])
        c = int(a.sum())
        tot = float(v[a].sum())
        hit = None
        # 多接纳一个:解出那个抽头该是多少,看是不是某个被拒的抽头。
        if c >= 0:
            want = m * (c + 1) - tot
            cand = np.nonzero(~a & (np.abs(v - want) < max(TOL, abs(want) * 1e-6)))[0]
            if cand.size:
                hit = "加一个被拒的"
                # 它离阈值有多远 —— 若都很近,那还是边界问题。
                j = cand[0]
                extra_gap.append(float(np.abs(base[sl][y, x] - v[j]) - thr[sl][y, x]))
        if hit is None and c >= 2:
            want = m * (c - 1)
            for j in np.nonzero(a)[0]:
                if abs(tot - v[j] - want) < max(TOL, abs(want) * 1e-6):
                    hit = "去一个已接的"
                    break
        if hit is None and abs(m - v.mean()) < TOL:
            hit = "全部 25 个"
        if hit is None and abs(m - v[12]) < TOL:
            hit = "只有中心"
        tally[hit or "对不上"] += 1

    print("  引擎的 mean 与哪种接纳集吻合:")
    for k, n in sorted(tally.items(), key=lambda t: -t[1]):
        print(f"    {k:<14} {n:5d} 个   {100 * n / max(ys.size, 1):5.1f}%")
    if extra_gap:
        g = np.array(extra_gap)
        print(f"\n  「加一个被拒的」那些:那个抽头离阈值 "
              f"中位 {np.median(g):+.4f}  范围 [{g.min():+.4f}, {g.max():+.4f}]")
        print("  若普遍只差一点点,说明判据边界还差一个常数;"
              "\n  若差得很开,说明引擎在这里用的阈值本身就不同。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
