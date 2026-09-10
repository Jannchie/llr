"""中心像素在比较基准里占多大权重 —— 这是 probe 那条路测不到的最后一块。

`green_base_probe.py` 把 ±4 内**非抽头**位置的权重全测出来了:只有 8 个非零,
每个 0.0202。抽头位置(含中心)测不到,因为抬高它们会同时改变累加集,两个效应
分不开。可中心的权重恰恰是判定 base 形式的关键 —— w=1/2 的
`0.5·centre + 0.5·m25` 要求中心占 0.5+0.02=0.52,而按「权重和为 1」倒推出来的
却是 0.74 上下。

`--mode ringctr` 让外环扫 D、中心固定抬高 adj。中心虽然进累加,但**外环的跳变
点**只由基准决定:

    |base − (dc+D)| = thr,  base = dc + w_c·adj   ->   D* = thr + w_c·adj

adj 取小值(20),让 tbl0 的查表下标留在同一个常数段 [784,843] 内,阈值不跟着动;
也让内环 8 个抽头不会因 base 偏移而被拒,输出的两个状态才干净:

    外环还在:  mean = dc + (16D + adj)/25
    外环被拒:  mean = dc + adj/9      (剩内环 8 个 dc 与中心 dc+adj)
"""
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))


def main() -> int:
    name = sys.argv[1] if len(sys.argv) > 1 else "green_impulse_fl_test_w0_ringctradj20.npz"
    z = np.load(os.path.join(HERE, name))
    ref, out, tbl0, tbl3 = z["ref"], z["out"], z["tbl0"], z["tbl3"]
    off, dc, adj = float(z["offset"][0]), float(z["dc"][0]), float(z["adj"][0])
    bp = int(z["pitch"][0])
    h, wd = ref.shape
    thr = float(tbl0[int(dc)])
    w = float(tbl3[0]) / 1024.0
    print(f"{name}\n  dc={dc:.0f}  thr={thr:.0f}  中心抬高 adj={adj:g}  "
          f"块间距={bp}  w={w:.4f}")
    print(f"  阈值查表下标 dc+adj={dc + adj:.0f} → tbl0={float(tbl0[int(dc + adj)]):.0f}"
          f"(必须与 {thr:.0f} 相同,否则 adj 太大)")

    ds, ys = [], []
    for cy in range(bp, h - bp, bp):
        for cx in range(bp, wd - bp, bp):
            d = float(ref[cy + 4, cx]) - dc
            if d <= 1.0 or abs(float(ref[cy, cx]) - dc - adj) > 1e-3:
                continue
            ds.append(d)
            ys.append(float(out[cy, cx]) + off - dc)
    ds, ys = np.array(ds), np.array(ys)
    if ds.size == 0:
        print("  没有完好的块")
        return 1

    # 两个状态都是 D 的函数,所以要逐点比对而不是看一个常数。
    keep = 0.64 * ds + adj / 25.0     # 外环仍被接纳
    drop = np.full_like(ds, adj / 9.0)  # 外环被拒,只剩内环 8 个与中心
    acc = np.abs(ys - keep) < 0.02 * np.maximum(keep, 1.0)
    rej = np.abs(ys - drop) < 0.02 * np.maximum(drop, 1.0)
    odd = int((~acc & ~rej).sum())
    print(f"  完好的块 {ds.size} 个;与「外环仍在」一致 {int(acc.sum())},"
          f"与「外环被拒」一致 {int(rej.sum())},两者都不是 {odd}")
    if odd > ds.size * 0.05:
        print("  ⚠️ 两者都不是的块太多 —— 状态模型就不对,下面的读数不能信")
    if not acc.any() or not rej.any():
        print("  没观察到跳变,D 的范围不够")
        return 1

    lo = float(np.quantile(ds[acc], 0.99))
    hi = float(np.quantile(ds[rej], 0.01))
    mid = (lo + hi) / 2
    wc = (mid - thr) / adj
    print(f"\n  跳变夹在 D ∈ [{lo:.2f}, {hi:.2f}],中点 {mid:.2f}")
    print(f"  中心权重 w_c = (D*−thr)/adj = {wc:.4f} "
          f"±{(hi - lo) / 2 / adj:.4f}")
    print("\n  对照:")
    for label, pred in (("0.5·centre + 0.5·m25 (m25 含中心)", w + (1 - w) / 25.0),
                        ("0.5·centre + 0.5·m25 (m25 不含中心)", w),
                        ("0.5·centre + 0.5·m9", w + (1 - w) / 9.0),
                        ("纯 25 点均值", 1 / 25.0),
                        ("纯 49 点均值", 1 / 49.0)):
        hit = "   ← 对上" if lo <= thr + pred * adj <= hi else ""
        print(f"    {label:<36} w_c={pred:.4f}  D*={thr + pred * adj:6.2f}{hit}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
