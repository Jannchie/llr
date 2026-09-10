"""反推引擎在那 96 个点上的 base 落在哪,以及那点偏差跟什么量有关。

线索链走到这一步:

  * 剩下的错点全是"引擎多接纳了**最近的那个被拒抽头**"(`filt_accept_solve.py`,
    96/96);
  * 那个抽头离阈值 +1.03~+7.72,不是浮点边界够得着的;
  * 阈值不是查抽头均值、也不是加常数或乘系数 —— 三个都试过,全变差
    (`thr_source_test.py`);
  * 「至少接纳 K 个」也不成立:count=1 与 count=2 的点都只多接纳一个。

所有抽头的 gap 会被同一个量整体平移,所以剩下的解释就是**引擎的 base 与我们的
差了一点**。既然知道它接纳了谁、拒绝了谁,就能把它的 base 夹出一个区间:

    对每个被接纳的 v_i:  |base_eng − v_i| <  thr
    对每个被拒绝的 v_k:  |base_eng − v_k| >= thr

取交集,再和我们的 base 比,看差值有没有规律 —— 是常数,还是跟某个量成比例。
"""
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import numpy as np

from llr_worker.sony.rawnr_simd import FILT_MARGIN, TAP_SPACING, TAPS_PER_SIDE
from filt_outlier_probe import compute, sh  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))


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
    sl = (slice(W - r, h - W - r), slice(W - r, w - W - r))
    eng = z[f"{slot}_out"][W:h - W, W:w - W]

    err = got[sl] - eng
    sel = (err != 0) & (margin[sl] > 1.0)
    ys, xs = np.nonzero(sel)
    print(f"{name}  {slot}: {ys.size} 个点\n")
    if ys.size == 0:
        return 0

    offs = [k * TAP_SPACING for k in range(-TAPS_PER_SIDE, TAPS_PER_SIDE + 1)]
    taps = np.stack([sh(ref, dy, dx, r)[sl] for dy in offs for dx in offs], -1)
    boost = np.clip(sh(det, 0, 0, r) * np.float32(gain / 256.0), -limit, limit)
    mean_eng = eng.astype(np.float64) + off - boost[sl].astype(np.float64)

    b_ours, b_lo, b_hi, feats = [], [], [], []
    for k in range(ys.size):
        y, x = ys[k], xs[k]
        v = taps[y, x].astype(np.float64)
        t = float(thr[sl][y, x])
        m = float(mean_eng[y, x])
        c_ours = int(count[sl][y, x])
        # 引擎的接纳集:我们的 + 最近的被拒抽头。用 mean 反解出它是哪一个。
        a = np.abs(base[sl][y, x] - v) < t
        want = m * (c_ours + 1) - float(v[a].sum())
        j = np.argmin(np.abs(v - want))
        acc = a.copy()
        acc[j] = True
        # base_eng 必须让 acc 里的全部通过、acc 外的全部不通过。
        lo = max(v[acc].max() - t, -np.inf)
        hi = min(v[acc].min() + t, np.inf)
        for kk in np.nonzero(~acc)[0]:
            # 被拒的抽头把区间从一侧顶开
            if v[kk] < v[acc].min():
                lo = max(lo, v[kk] + t)
            elif v[kk] > v[acc].max():
                hi = min(hi, v[kk] - t)
        if lo > hi:
            continue
        b_ours.append(float(base[sl][y, x]))
        b_lo.append(lo)
        b_hi.append(hi)
        feats.append((float(centre[sl][y, x]), float(v.mean()), t,
                      float(mean[sl][y, x])))

    b_ours = np.array(b_ours)
    b_lo, b_hi = np.array(b_lo), np.array(b_hi)
    feats = np.array(feats)
    ok = b_lo <= b_hi
    print(f"  能夹出区间的 {int(ok.sum())} 个")
    # 我们的 base 离那个区间多远(在区间内就是 0)
    d = np.where(b_ours < b_lo, b_lo - b_ours,
                 np.where(b_ours > b_hi, b_ours - b_hi, 0.0))
    print(f"  我们的 base 落在区间内的 {int((d == 0).sum())} 个;"
          f"在外的偏差 中位 {np.median(d[d > 0]):.4f}  "
          f"范围 [{d[d > 0].min():.4f}, {d[d > 0].max():.4f}]")

    # 取区间中点当"引擎的 base",看差值跟什么量有关。
    mid = (np.maximum(b_lo, b_ours - 50) + np.minimum(b_hi, b_ours + 50)) / 2
    delta = mid - b_ours
    print(f"\n  用区间中点估的 base 差:  中位 {np.median(delta):+.4f}   "
          f"范围 [{delta.min():+.4f}, {delta.max():+.4f}]")
    names = ["centre", "抽头均值", "阈值 thr", "我们的 mean"]
    print("\n  这个差与各量的相关:")
    for i, nm in enumerate(names):
        rr = float(np.corrcoef(delta, feats[:, i])[0, 1])
        print(f"    {nm:<10} {rr:+.4f}")
    rr = float(np.corrcoef(delta, feats[:, 0] - b_ours)[0, 1])
    print(f"    centre − base  {rr:+.4f}")
    rr = float(np.corrcoef(delta, feats[:, 1] - b_ours)[0, 1])
    print(f"    抽头均值 − base {rr:+.4f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
