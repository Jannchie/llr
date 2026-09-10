"""从受控捕获里解出绿色核的 25 个抽头。

输入是 `green_impulse.py` 灌进去的两组独立随机噪声,detail 已清零。于是这一步
是纯线性的:`out = (Σ 25 个抽头)/25 − offset`,而候选之间没有相关性 —— 系数只能
是 1/25 或 0,不会像真实画面上那样在共线的邻居之间飘。

判据写在前面,免得事后找理由:
  * 系数应当**明显地二分**:一批贴着 0.04,其余贴着 0;
  * 贴着 0.04 的应当**正好 25 个**;
  * 拿这 25 个重算一遍,应当逐位复现 `out`。
第三条是真正的验收,前两条只是路标。
"""
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
CAP = "green_impulse_fl_test_w0.npz"
R = 8   # 候选池半径,比 5x5 间距 2 的支撑(±4)再宽一圈


def shifted(a, dy, dx, r):
    h, w = a.shape
    return a[r + dy:h - r + dy, r + dx:w - r + dx]


def main():
    z = np.load(os.path.join(HERE, sys.argv[1] if len(sys.argv) > 1 else CAP))
    ref, ref2, out = z["ref"], z["ref2"], z["out"]
    off = float(z["offset"][0])
    h, w = ref.shape
    print(f"平面 {ref.shape}  offset={off:.0f}  "
          f"ref 范围 [{ref.min():.2f}, {ref.max():.2f}]")
    print(f"out 范围 [{out.min():.2f}, {out.max():.2f}]  "
          f"两平面相关 {np.corrcoef(ref.ravel(), ref2.ravel())[0,1]:+.4f}")

    # 引擎只写 [5, n-5);候选池再留 R,取交集。
    W = 5
    m = max(W, R)
    y = (out[m:h - m, m:w - m].astype(np.float64) + off).ravel()

    cols, names = [], []
    for plane, pname in ((ref, "self"), (ref2, "other")):
        for dy in range(-R, R + 1):
            for dx in range(-R, R + 1):
                cols.append(shifted(plane, dy, dx, m).astype(np.float64).ravel())
                names.append((pname, dy, dx))
    A = np.stack(cols, axis=1)
    print(f"候选 {A.shape[1]} 个,样本 {A.shape[0]}")

    coef, *_ = np.linalg.lstsq(A, y, rcond=None)
    unit = 1.0 / 25.0
    hits = [i for i in range(len(coef)) if coef[i] > unit * 0.5]
    print(f"\n  系数和 {coef.sum():.5f}   1/25 = {unit:.5f}")
    print(f"  超过 1/50 的候选:{len(hits)} 个"
          f"{'  ✓ 正好 25' if len(hits) == 25 else '  ⚠️ 不是 25'}")
    rest = np.abs(np.delete(coef, hits))
    print(f"  其余 {len(coef)-len(hits)} 个的 |系数| 最大 {rest.max():.6f}"
          f"(与 {unit:.5f} 相比{'  ✓ 干净二分' if rest.max() < unit*0.2 else '  ⚠️ 不干净'})")

    print("\n  命中的抽头(按平面、行、列):")
    for pname in ("self", "other"):
        rows = {}
        for i in hits:
            p, dy, dx = names[i]
            if p == pname:
                rows.setdefault(dy, []).append((dx, coef[i]))
        if not rows:
            continue
        print(f"    {pname}:")
        for dy in sorted(rows):
            cells = "  ".join(f"{dx:+d}:{c:.4f}" for dx, c in sorted(rows[dy]))
            print(f"      行 {dy:+d}  ({len(rows[dy])} 列)  {cells}")

    # 验收:拿命中的抽头重算,看能不能复现 out。
    taps = [names[i] for i in hits]
    acc = np.zeros_like(shifted(ref, 0, 0, m), dtype=np.float64)
    for pname, dy, dx in taps:
        acc += shifted(ref if pname == "self" else ref2, dy, dx, m)
    pred = acc / len(taps) - off
    eng = out[m:h - m, m:w - m]
    err = pred - eng
    print(f"\n  用这 {len(taps)} 个抽头重算:逐位相同 {100*np.mean(err == 0):.4f}%  "
          f"|误差| 最大 {np.abs(err).max():.4g}  中位 {np.median(np.abs(err)):.4g}")
    print(f"  相对幅度 {np.abs(err).mean() / max(np.abs(eng).mean(), 1e-9):.3e}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
