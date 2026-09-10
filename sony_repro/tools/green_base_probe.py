"""把绿色核比较基准的**权重图逐点测出来**,不预设它是哪种窗口。

`green_base_isolate.py` 已经定死了两件事:外环(间距 2、距离 4 的 16 个抽头)完全
不进基准 —— 它的跳变点精确等于 thr;而 ±1 邻点进,权重 0.12。可是没有一种标准
窗口能同时给出这两个数:间距 1 的 5x5 要求 0.16,7x7 要求 0.082,间距 2 的 3x3
(红蓝用的那个)要求 0.444。所以不再猜。

`green_impulse.py --mode probe` 每个块只抬高**一个非抽头位置** adj,同时外环扫
D。非抽头位置不进累加,只可能经由基准影响输出,于是

    跳变点 D* = thr + 权重 x adj   ->   权重 = (D* − thr) / adj

一个块一个 (位置, D 档) 组合,读出来的就是基准的权重图本身。

⚠️ 只测得了非抽头位置。中心和 25 个抽头的权重这条路测不到 —— 抬高它们会同时
改变累加,两个效应分不开。中心的权重要靠「其余权重之和」反推。
"""
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))


def transition(d: np.ndarray, resp: np.ndarray,
               full: float) -> tuple[float, float, int]:
    """跳变点夹逼。响应是二值的,所以最大的接纳与最小的拒绝就把它夹住了。"""
    tol = 0.02 * full
    acc, rej = np.abs(resp - full) < tol, np.abs(resp) < tol
    odd = int((~acc & ~rej).sum())
    if not acc.any() or not rej.any():
        return float("nan"), float("nan"), odd
    return (float(np.quantile(d[acc], 0.99)),
            float(np.quantile(d[rej], 0.01)), odd)


def scan(name: str) -> tuple[list, float, float]:
    """一个捕获里每个探测位置的跳变点。返回 (rows, thr, adj)。"""
    z = np.load(os.path.join(HERE, name))
    ref, out, tbl0 = z["ref"], z["out"], z["tbl0"]
    # 探测点打在哪个平面上,就从哪个平面认它。
    probe_plane = z["ref2"] if "other" in name else ref
    off, dc, adj = float(z["offset"][0]), float(z["dc"][0]), float(z["adj"][0])
    bp = int(z["pitch"][0])
    pos = [tuple(int(v) for v in p) for p in z["probe_pos"]]
    h, wd = ref.shape
    thr = float(tbl0[int(dc)])
    # 探的若是抽头位置(坐标全为偶数),adj 大到让它被拒,它就退出累加 —— 那时
    # 「全接纳」只剩 24 个抽头,响应是 16/24 而不是 16/25。
    on_taps = ("other" not in name
               and all(p[0] % 2 == 0 and p[1] % 2 == 0 for p in pos))
    full = 16.0 / (24.0 if on_taps else 25.0)

    # 每个块:外环给出 D,被抬高的那个非抽头位置给出「探的是哪儿」。位置从 ref
    # 里认,而不是从块编号重算 —— 重算要跟 JS 的循环边界完全一致,认值不会错。
    buckets: dict[tuple[int, int], list[tuple[float, float]]] = {p: [] for p in pos}
    skipped = 0
    for cy in range(bp, h - bp, bp):
        for cx in range(bp, wd - bp, bp):
            d = float(ref[cy + 4, cx]) - dc
            if d <= 0 or abs(float(ref[cy, cx]) - dc) > 1e-3:
                continue
            hits = [p for p in pos
                    if abs(float(probe_plane[cy + p[0], cx + p[1]]) - dc - adj) < 1e-3]
            if len(hits) != 1:
                skipped += 1     # 图案被邻块踩到,或写入被截断
                continue
            buckets[hits[0]].append((d, (float(out[cy, cx]) + off - dc) / d))

    rows = []
    for p in pos:
        arr = np.array(buckets[p])
        if arr.shape[0] < 8:
            rows.append((p, float("nan"), float("nan"), arr.shape[0], 0))
            continue
        lo, hi, odd = transition(arr[:, 0], arr[:, 1], full)
        rows.append((p, (lo + hi) / 2 if not np.isnan(lo) else float("nan"),
                     (hi - lo) / 2 if not np.isnan(lo) else float("nan"),
                     arr.shape[0], odd))
    print(f"  {name}: {len(pos)} 个位置,丢掉 {skipped} 个块(探测点不唯一)")
    return rows, thr, adj


def main() -> int:
    names = sys.argv[1:] or ["green_impulse_fl_test_w0_probeadj600b0.npz"]
    allrows: list = []
    thr = adj = 0.0
    for n in names:
        rows, thr, adj = scan(n)
        allrows += rows
    print(f"\n  thr={thr:.0f}  adj={adj:g}  合计 {len(allrows)} 个位置")

    wmap = {p: (mid - thr) / adj for p, mid, _, _, _ in allrows
            if not np.isnan(mid)}
    print("\n  权重图(x1000;T=抽头,这条路测不到;·=0;? =没读到跳变):")
    print("        " + "".join(f"{dx:+5d}" for dx in range(-4, 5)) + "   ← dx")
    for dy in range(-4, 5):
        cells = []
        for dx in range(-4, 5):
            # 测到的一律显示,包括抽头位置 —— T 只留给「是抽头且没测」。
            if (dy, dx) in wmap:
                v = wmap[(dy, dx)] * 1000
                cells.append("    ·" if abs(v) < 2.0 else f"{v:5.1f}")
            elif dy % 2 == 0 and dx % 2 == 0:
                cells.append("    T")
            else:
                cells.append("    ?")
        print(f"  dy={dy:+d} " + "".join(cells))

    nz = {p: v for p, v in wmap.items() if abs(v) > 0.002}
    print(f"\n  非零位置 {len(nz)} 个,权重和 {sum(nz.values()):.4f}")
    if nz:
        vals = np.array(list(nz.values()))
        print(f"  非零权重:中位 {np.median(vals):.4f}  "
              f"范围 [{vals.min():.4f}, {vals.max():.4f}]")
        # (1−w)/N 的形式:w=1/2 时 0.02 就是 N=25。
        print(f"  若每个都是 (1−w)/N 且 w=0.5 → N = {0.5/np.median(vals):.2f}")

    # 对称位置必须读出同一个权重。读不出就是实验有问题,而不是核各向异性。
    print("\n  对称检验(四个象限两两应当相等):")
    bad = 0
    for (dy, dx), v in sorted(nz.items()):
        for q in ((-dy, dx), (dy, -dx), (-dy, -dx)):
            if q in wmap and abs(wmap[q] - v) > 0.005:
                print(f"    ({dy:+d},{dx:+d}) {v:+.4f}  vs  "
                      f"({q[0]:+d},{q[1]:+d}) {wmap[q]:+.4f}   ✗")
                bad += 1
    print("    全部一致 ✓" if not bad else f"    {bad} 处不一致")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
