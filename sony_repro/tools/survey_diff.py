r"""比较两次 survey_batch 的结果 —— 一次改动到底让哪些图变好、哪些变差。

只看总体中位数会把「19 张大幅改善 + 46 张纹丝不动」摊平成一个不起眼的小数,
所以这里按每张的变化排序,并单独给出**实际发生变化的那批**的统计。

用法: python survey_diff.py before.json after.json [--key 分组字段]
"""
import json
import sys
from pathlib import Path

import numpy as np

KEYS = ("chroma", "hue", "luma", "rmse")


def main():
    before, after = (json.loads(Path(p).read_text()) for p in sys.argv[1:3])
    a = {r["stem"]: r for r in before}
    b = {r["stem"]: r for r in after}
    both = sorted(set(a) & set(b))
    if not both:
        raise SystemExit("两边没有共同的图")

    # 色相绝对值变化最大的排前面 —— 那才是这次改动瞄准的东西
    moved = [s for s in both if abs(a[s]["hue"] - b[s]["hue"]) > 0.05
             or abs(a[s]["chroma"] - b[s]["chroma"]) > 0.002]
    print(f"{len(both)} 张共同,其中 {len(moved)} 张有变化\n")

    for s in sorted(moved, key=lambda s: -abs(a[s]["hue"] - b[s]["hue"])):
        print("%-10s %-4s 色相 %+6.2f -> %+6.2f   色度 %.3f -> %.3f   RMSE %5.1f -> %5.1f"
              % (s, b[s]["look"], a[s]["hue"], b[s]["hue"],
                 a[s]["chroma"], b[s]["chroma"], a[s]["rmse"], b[s]["rmse"]))

    for label, group in (("全部", both), ("有变化的", moved)):
        if not group:
            continue
        print(f"\n=== {label} ({len(group)} 张) 中位绝对误差")
        for k in KEYS:
            # 色度的"无误差"是 1.0,其余是 0
            ref = 1.0 if k == "chroma" else 0.0
            va = np.median([abs(a[s][k] - ref) for s in group])
            vb = np.median([abs(b[s][k] - ref) for s in group])
            arrow = "改善" if vb < va - 1e-9 else ("变差" if vb > va + 1e-9 else "不变")
            print(f"  {k:<7} {va:.4f} -> {vb:.4f}   {arrow}")


if __name__ == "__main__":
    main()
