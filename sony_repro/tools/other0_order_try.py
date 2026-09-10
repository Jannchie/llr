"""试几种 `BASE_GREEN_OTHER[0]` 的累加次序 —— 有限枚举,整幅计分。

现状:`g1`(用 other[1])在三张图上全部逐位 100.0000%,`g0`(用 other[0])在 fl_test 与
DSC04568 上还差 0.002%,DSC03036 上已经是 100%。

可疑之处:own 表最终定下来的次序**不是**按 (dy,dx) 排序的(`(0,2) (-2,0) (0,-2) …`,
由反汇编 + 枚举定出),而两张 other 表我都填的排序次序。g1 能对上也许只是碰巧。

12! 无法枚举,所以只试结构化的几种。判优一律**整幅**计分 —— 只看差异点会过拟合,
`base_order_search.py` 上已经栽过一次。
"""
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import numpy as np

from llr_worker.sony.rawnr_simd import BASE_GREEN_OTHER, BASE_GREEN_OWN, FILT_MARGIN, filt

HERE = os.path.dirname(os.path.abspath(__file__))
#: 基线取**生产里正在用的那张表**,别手抄。手抄过一次,后来生产改成 +2 行在前,
#: 副本没跟上 —— 于是"当前"这一列指的是一张没人用的表,任何候选都会被拿去跟错误
#: 的基线比,报出虚假的改进。这个目录的全部纪律就是"整幅计分、别过拟合",基线错了
#: 计分再准也没用。
CUR = BASE_GREEN_OTHER[0]


def variants():
    s = list(CUR)
    out = {"当前(生产在用)": tuple(s), "逆序": tuple(s[::-1])}
    # 行内逆序,行间保持
    byrow = {}
    for dy, dx in s:
        byrow.setdefault(dy, []).append((dy, dx))
    out["行内逆序"] = tuple(p for dy in sorted(byrow) for p in byrow[dy][::-1])
    out["行序倒置"] = tuple(p for dy in sorted(byrow, reverse=True) for p in byrow[dy])
    # 列优先
    out["列优先"] = tuple(sorted(s, key=lambda p: (p[1], p[0])))
    # 模仿 own:把离中心最远的一行提到最前,其余按行
    out["远行提前"] = (s[8], s[9], s[10], s[11], s[0], s[1],
                   s[2], s[3], s[4], s[5], s[6], s[7])
    out["首尾对调"] = (s[-1], *s[1:-1], s[0])
    # 「按行升序」这个老基线单独留着:生产已经换成逐条钩 vaddps 读出来的次序
    # (+2 行整组在 +1 行之前),它不再是任何一个候选,但仍是有用的对照 ——
    # 当初就是它输了 99.9985% 对 100.0000%。
    out["旧基线(按行升序)"] = tuple(sorted(s))
    return out


def score(z, slot, cross):
    ref, other = z[f"{slot}_ref"], z[f"{slot}_ref2"]
    det, tbl0 = z[f"{slot}_detail"], z[f"{slot}_tbl0"]
    got = filt(det, ref, tbl0, blend=int(z[f"{slot}_tbl3"][0]),
               gain=int(z[f"{slot}_gain"][0]), limit=int(z[f"{slot}_limit"][0]),
               offset=float(z[f"{slot}_offset"][0]), other=other,
               base_own=BASE_GREEN_OWN, base_other=cross)
    h, w = ref.shape
    r, W = FILT_MARGIN, 6
    eng = z[f"{slot}_out"][W:h - W, W:w - W]
    sl = (slice(W - r, h - W - r), slice(W - r, w - W - r))
    return 100.0 * float(np.mean(got[sl] == eng))


def main() -> int:
    names = ["rawnr_full_fl_test.npz", "rawnr_full_DSC03036.npz",
             "rawnr_full_DSC04568.npz"]
    zs = [(n, np.load(os.path.join(HERE, n))) for n in names]
    print("只动 BASE_GREEN_OTHER[0],只影响 g0。整幅计分。\n")
    print("  次序              " + "  ".join(f"{n.split('_')[2][:8]:>9}" for n, _ in zs)
          + "     平均")
    rows = []
    for label, cross in variants().items():
        vals = [score(z, "g0", cross) for _, z in zs]
        rows.append((float(np.mean(vals)), label, vals))
    for avg, label, vals in sorted(rows, reverse=True):
        print(f"  {label:<16} " + "  ".join(f"{v:9.4f}" for v in vals)
              + f"  {avg:9.4f}")
    print("\n  ⚠️ 只有明显高于「当前」才值得换;差不多就别动 —— 那是噪声。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
