"""孤立点扫描:绿色核的筛选判据到底是什么。

平面是常数 `dc`,每隔 16 个像素放一个偏离 `delta` 的孤立点,delta 扫过一段范围。
抽头支撑只有 ±4,所以各点互不干扰,一次调用就是一整条曲线。

在孤立点**自己**那一格上,25 个抽头里有 24 个等于 dc,一个等于 dc+delta。于是:

    delta 小 -> 那个抽头也被接纳 -> mean = dc + delta/25
    delta 大 -> 它被拒 -> mean = dc(24 个相同的抽头)

跳变点由判据决定,而不同的 base 给出不同的跳变点:

    base = 中心与 m25 各半 -> |base − v| = |delta|(1 − 1/50) -> 跳变在 thr/0.98
    base = m25 纯          -> |delta|(1 − 1/25)             -> thr/0.96
    base = 中心            -> |delta|                        -> thr

thr 在 dc 处约 24,三者的跳变点分别是 24.5 / 25.0 / 24.0 —— 分得开,但要求
delta 的采样够密。同时还能读出判据是 `<` 还是 `<=`,以及是否对称。

⚠️ 孤立点的**邻居**格子也带信息:那里 25 个抽头里有一个是 dc+delta,而中心是
dc,base 略偏。若邻居格的行为与中心格不一致,说明 base 不是位置无关的那几种。
"""
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
PITCH = 16
W = 5


def main():
    name = sys.argv[1] if len(sys.argv) > 1 else "green_impulse_fl_test_w0_impulse.npz"
    z = np.load(os.path.join(HERE, name))
    ref, out = z["ref"], z["out"]
    tbl0 = z["tbl0"]
    off = float(z["offset"][0])
    dc = float(z["dc"][0])
    h, w = ref.shape
    thr = float(tbl0[int(dc)])
    print(f"dc={dc:.0f}  offset={off:.0f}  阈值 tbl0[{int(dc)}]={thr:.0f}")

    # 收集每个孤立点:它的 delta,以及它自己那一格的 out
    rows, deltas, outs = [], [], []
    for y in range(8, h - W, PITCH):
        for x in range(8, w - W, PITCH):
            if y < W or x < W:
                continue
            d = float(ref[y, x]) - dc
            if abs(d) < 1e-6:
                continue
            deltas.append(d)
            outs.append(float(out[y, x]))
    deltas = np.array(deltas)
    outs = np.array(outs)
    print(f"孤立点 {deltas.size} 个,delta 范围 [{deltas.min():.2f}, {deltas.max():.2f}]")

    base_const = dc - off          # 那个抽头被拒时的输出
    order = np.argsort(np.abs(deltas))
    ad = np.abs(deltas)[order]
    ao = outs[order]
    # 被接纳时 out = dc + delta/25 − off,与被拒时差 delta/25
    accepted = np.abs(ao - (dc + deltas[order] / 25.0 - off)) < 1e-3
    rejected = np.abs(ao - base_const) < 1e-3

    print(f"  与「接纳」一致的点:{100*accepted.mean():.2f}%")
    print(f"  与「拒绝」一致的点:{100*rejected.mean():.2f}%")
    print(f"  两者都不是:{100*(~accepted & ~rejected).mean():.2f}%")

    if accepted.any() and rejected.any():  # noqa: SIM102 — 两条路都要走
        cut_lo = ad[accepted].max()
        cut_hi = ad[rejected].min()
        print(f"\n  跳变区间:接纳的最大 |delta| = {cut_lo:.3f},"
              f"拒绝的最小 |delta| = {cut_hi:.3f}")
        for label, factor in (("base = 中心与 m25 各半", 1 - 1 / 50),
                              ("base = m25 纯", 1 - 1 / 25),
                              ("base = 中心", 1.0),
                              ("base = 中心与 m9 各半", 1 - 1 / 18)):
            pred = thr / factor
            hit = cut_lo <= pred <= cut_hi
            print(f"    {label:<24} 预测跳变 {pred:6.3f}"
                  f"{'   ✓ 落在区间内' if hit else ''}")
    # 整条曲线:out 相对「那个抽头被拒」的基准偏了多少,除以 delta/25 归一化。
    # 读数 1.0 = 完全接纳,0.0 = 完全拒绝,中间值说明还有别的抽头在进出。
    print("\n  |delta| 分档(读数 1=接纳, 0=拒绝):")
    resp = (outs - base_const) / (deltas / 25.0)
    edges = np.arange(0, 84, 4.0)
    for lo, hi in zip(edges, edges[1:]):
        m = (np.abs(deltas) >= lo) & (np.abs(deltas) < hi)
        if not m.any():
            continue
        r = resp[m]
        print(f"    [{lo:4.0f},{hi:4.0f})  n={m.sum():3d}  "
              f"响应 中位 {np.median(r):+7.4f}  范围 [{r.min():+.4f}, {r.max():+.4f}]"
              f"  out 相对基准 {np.median(outs[m] - base_const):+9.5f}")
    print(f"\n  阈值 thr={thr:.0f};若判据是 |base−v|<thr 且 base 含 1/50 的自拉,"
          f"跳变应在 |delta| ≈ {thr/(1-1/50):.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
