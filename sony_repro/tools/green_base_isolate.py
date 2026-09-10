"""把绿色核的**比较基准**从抽头筛选里单独隔离出来。

矛盾是这样来的:孤立点扫描(`green_threshold_solve.py`)说 base 含 m25,白噪声
与真实画面上的逐位比对说 base 是 m9 —— 因为**每一种输入都同时动了两者**。
孤立点一动,centre、m9、m25 同时变,跳变点里混着两个未知量。

`green_impulse.py --mode ring/inner` 喂的两级图案把它们拆开:

  ring  外环(Chebyshev 距离 4)的 16 个抽头抬高 D,内 3x3 一动不动
        -> m9 = dc(不动), m25 = dc + 0.64D
  inner 内环(距离 2)的 8 个抬高 E
        -> m9 = dc + 0.889E, m25 = dc + 0.32E

两种图案里中心恒为 dc,所以 thr = tbl0[dc] 是常数,跳变点可以直接读;而且同一组
抽头取值完全相同 —— 整组一起进、一起出,跳变是二值的,不像白噪声糊成一片。

读数就是**第一个跳变点在 |D| 的多少倍阈值处**。w=1/2 时三个候选各自预测:

              ring 外环     inner 内环
  base=m25     1.47 thr      1.19 thr
  base=m9      1.00 thr      1.80 thr
  base=centre  1.00 thr      1.00 thr

ring 把 m25 与其余两者分开,inner 把三者分开。两个图案互相印证 —— 只对上一个
的不算数。
"""
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
FILT_MARGIN = 4  # 核每边吃掉的圈数

#: 每种图案下,「全部 25 个抽头都被接纳」时 (out+off−dc)/D 应有的读数。
FULL = {"ring": 16.0 / 25.0, "inner": 8.0 / 25.0}
#: 抬高的是哪一组:图案里离中心多远的那一圈。
DIST = {"ring": 4, "inner": 2}


#: 各候选基准在两种图案下的两个系数:窗口均值相对 dc 的偏移 =
#: k_D * D + k_adj * adj。m9sp1 是间距 1 的 3x3 —— 它在 ring/inner 下都恒等于
#: dc(那两个图案动的是 ±4 和 ±2),所以只有抬高 ±1 邻点才分得出它和 centre。
KOEF: dict[str, dict[str, tuple[float, float]]] = {
    "ring": {
        "m25(间距2)": (16.0 / 25.0, 0.0),
        "m9(间距2)": (0.0, 0.0),
        "m9(间距1)": (0.0, 8.0 / 9.0),
        "centre": (0.0, 0.0),
    },
    "inner": {
        "m25(间距2)": (8.0 / 25.0, 0.0),
        "m9(间距2)": (8.0 / 9.0, 0.0),
        "m9(间距1)": (0.0, 8.0 / 9.0),
        "centre": (0.0, 0.0),
    },
}


def predictions(mode: str, w: float, adj: float,
                thr: float) -> list[tuple[str, float, float]]:
    """各候选预测的两侧跳变点 (正 D, 负 |D|),单位是绝对的 D。

    base = dc + (1−w)(k_D·D + k_adj·adj),被抬高那一组的 v = dc + D,判据
    |base − v| = thr。记 a = 1 − (1−w)k_D、b = (1−w)k_adj·adj:

        正 D:  a·D − b = thr  →  D  = (thr + b)/a
        负 D:  a·|D| + b = thr →  |D| = (thr − b)/a

    b 为 0 时两侧相等 —— 所以**两侧不对称就等于 base 含 ±1 邻域**,不需要知道
    阈值的精确值就能读出来。
    """
    out = []
    for label, (k_d, k_adj) in KOEF[mode].items():
        a = 1.0 - (1.0 - w) * k_d
        b = (1.0 - w) * k_adj * adj
        if a < 1e-9:
            out.append((label, float("inf"), float("inf")))
        else:
            out.append((label, (thr + b) / a, (thr - b) / a))
    return out


def transition(ad: np.ndarray, resp: np.ndarray,
               full: float) -> tuple[float, float, int]:
    """跳变点,直接夹逼 —— 不做分档中位数。

    响应本来就是二值的(整组接纳 full,整组被拒 0),所以「最大的仍接纳的 |D|」和
    「最小的已被拒的 |D|」就把跳变点夹住了,精度等于 D 的采样步长,比分档中位数
    高一个数量级。用 1%/99% 分位而不是极值,免得个别卡在浮点边界上的块把区间
    撑开。
    """
    m = ad > 1.0        # 小 |D| 上 resp 是 0/0,读数没有意义
    ad, resp = ad[m], resp[m]
    tol = 0.02 * full
    acc, rej = np.abs(resp - full) < tol, np.abs(resp) < tol
    # 既不接纳也不拒绝的块。若有一批,说明「整组同进同出」这个前提就不成立,
    # 后面所有读数都失效 —— 所以它必须被报出来,不能悄悄丢掉。
    odd = int((~acc & ~rej).sum())
    if not acc.any() or not rej.any():
        return float("nan"), float("nan"), odd
    return (float(np.quantile(ad[acc], 0.99)),
            float(np.quantile(ad[rej], 0.01)), odd)


def main() -> int:
    name = sys.argv[1] if len(sys.argv) > 1 else "green_impulse_fl_test_w0_ring.npz"
    mode = "ring" if "ring" in name else "inner"
    z = np.load(os.path.join(HERE, name))
    ref, out, tbl0, tbl3 = z["ref"], z["out"], z["tbl0"], z["tbl3"]
    off, dc = float(z["offset"][0]), float(z["dc"][0])
    h, wd = ref.shape
    thr = float(tbl0[int(dc)])
    w = float(tbl3[0]) / 1024.0
    # 块间距从捕获里读回,不在这边另写一份 —— 两边走偏的话跳变点是假的。
    bp = int(z["pitch"][0]) if "pitch" in z else 32
    adj = float(z["adj"][0]) if "adj" in z else 0.0
    print(f"{name}\n  图案={mode}  dc={dc:.0f}  offset={off:.0f}  "
          f"thr=tbl0[{int(dc)}]={thr:.0f}  w=tbl3/1024={w:.4f}  块间距={bp}  "
          f"±1 邻点抬高={adj:g}")

    dd = DIST[mode]
    ds, ys, intact = [], [], 0
    for cy in range(bp, h - bp, bp):
        for cx in range(bp, wd - bp, bp):
            d = float(ref[cy + dd, cx]) - dc
            if abs(d) < 1e-6:
                continue
            # 图案完好性:被抬高那一圈的四个正交点必须都等于 dc+d,中心必须是 dc。
            # 边界块或写入被截断的话读数就是假的,宁可丢掉。
            ring_ok = all(abs(float(ref[cy + sy * dd, cx + sx * dd]) - dc - d) < 1e-3
                          for sy, sx in ((1, 0), (-1, 0), (0, 1), (0, -1)))
            if not ring_ok or abs(float(ref[cy, cx]) - dc) > 1e-3:
                continue
            intact += 1
            ds.append(d)
            ys.append(float(out[cy, cx]))
    ds, ys = np.array(ds), np.array(ys)
    if ds.size == 0:
        print("  没有完好的块 —— 图案没写进去,或者块间距对不上")
        return 1
    resp = (ys + off - dc) / ds
    full = FULL[mode]
    print(f"  完好的块 {intact} 个,|D| 范围 [{np.abs(ds).min():.2f}, "
          f"{np.abs(ds).max():.2f}];全接纳时响应应为 {full:.4f}")

    # 正负两侧分开量。判据取的是绝对值,所以 base 不含 ±1 邻域时两侧必然相等;
    # 一旦不等,差的一半就是 base 被 ±1 邻点抬走的量。
    sides = {"D > 0": ds > 0, "D < 0": ds < 0}
    got: dict[str, tuple[float, float]] = {}
    for side, m in sides.items():
        lo, hi, odd = transition(np.abs(ds[m]), resp[m], full)
        got[side] = (lo, hi)
        note = f"  ⚠️ 有 {odd} 个块既非全接纳也非全拒" if odd else ""
        if np.isnan(lo):
            print(f"  {side}: 没观察到跳变 —— |D| 扫得不够远,"
                  f"或者那一组从未被拒。{note}")
        else:
            print(f"  {side}: 跳变夹在 |D| ∈ [{lo:.2f}, {hi:.2f}] "
                  f"= [{lo/thr:.3f}, {hi/thr:.3f}] × thr{note}")

    # 两侧不对称的量直接读出 base 被 ±1 邻点抬走了多少,不必先知道 thr。
    (plo, phi), (nlo, nhi) = got["D > 0"], got["D < 0"]
    if not (np.isnan(plo) or np.isnan(nlo)):
        pos, neg = (plo + phi) / 2, (nlo + nhi) / 2
        print(f"\n  两侧中点 {pos:.2f} / {neg:.2f}"
              f"  →  base 偏移 b=(正−负)/2={(pos - neg) / 2:+.3f}"
              f"   有效阈值 (正+负)/2={(pos + neg) / 2:.3f}(表值 {thr:.0f})")
        if adj and "other" in name:
            # refOther 整体抬高:偏移 = (1−w)·(k/25)·adj,k 是 m25 里落在另一个
            # 绿平面上的点数。
            k = (pos - neg) / 2 / adj / (1 - w) * 25.0
            print(f"      b/adj = {(pos - neg) / 2 / adj:.4f}"
                  f"  →  m25 里有 k = {k:.2f} 个点落在**另一个绿平面**上")
        elif adj:
            print(f"      b/adj = {(pos - neg) / 2 / adj:.4f}"
                  f"   ÷(1−w) = {(pos - neg) / 2 / adj / (1 - w):.4f}"
                  f"  ← 这就是 ±1 那 8 个邻点在基准里占的总权重")

    print("\n  各候选基准的预测(正 D / 负 D 两侧):")
    for label, p_pos, p_neg in sorted(predictions(mode, w, adj, thr),
                                      key=lambda t: t[1]):
        marks = []
        for side, pred in (("D > 0", p_pos), ("D < 0", p_neg)):
            lo, hi = got[side]
            marks.append("✓" if not np.isnan(lo) and lo <= pred <= hi else "✗")
        tail = "   ← 两侧都对上" if marks == ["✓", "✓"] else ""
        print(f"    base = {label:<10} 预测 {p_pos:7.2f} {marks[0]} / "
              f"{p_neg:7.2f} {marks[1]}{tail}")
    if adj == 0.0:
        print("\n  ⚠️ adj=0:m9(间距1) 与 centre 在这个图案下读数相同,分不开。"
              "\n     要分开就带 --adj 抬高 ±1 邻点,再看两侧是否还对称。")
    print("\n  ⚠️ 只对上一个图案不算数:ring 与 inner 必须指向同一个候选。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
