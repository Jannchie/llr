"""tbl3(混合权重表)在**每一张图**上都是常数吗。

`base_residual_solve.py` 在 7153 个点的大样本上给出:我们的 base 与引擎的可行区间
的偏差,与 `centre − base` 相关 **+0.9456**。这个形式只对应一件事 ——

    base_eng ≈ (1−k)·base + k·centre
             = [(1−k)w + k]·centre + (1−k)(1−w)·m25

也就是**引擎的混合权重 w 比我们用的大**。而 filt 一直把 w 当常数:
`blend = tbl3[0]`,依据是在 fl_test 上量到 tbl3 全表恒 512。

若别的图上 tbl3 不是常数,那 w 就该**逐像素查表**,只取第 0 项就是错的 —— 而且
错的方向正好是这个相关指的方向。三张捕获一起看。
"""
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
SLOTS = ["rb0", "g0", "g1", "rb1"]


def main() -> int:
    names = sys.argv[1:] or ["rawnr_full_fl_test.npz", "rawnr_full_DSC03036.npz",
                             "rawnr_full_DSC04568.npz"]
    for name in names:
        z = np.load(os.path.join(HERE, name))
        print(f"{name}")
        for slot in SLOTS:
            t = z[f"{slot}_tbl3"]
            u = np.unique(t)
            ref = z[f"{slot}_ref"]
            lo, hi = int(np.min(ref)), int(np.percentile(ref, 99.9))
            # 只看 ref 实际会落到的下标范围 —— 表的其余部分查不到,不算数。
            seg = t[max(lo, 0):min(hi + 1, t.size)]
            us = np.unique(seg)
            print(f"    {slot}: 全表取值种数 {u.size}  范围 [{t.min()}, {t.max()}]"
                  f"   |  ref 用到的下标 [{lo},{hi}] 内种数 {us.size}"
                  f"  取值 {us[:6].tolist()}{'...' if us.size > 6 else ''}")
        print()
    print("  用到的区间内种数 >1 => w 必须逐像素查 tbl3[centre],不能用 tbl3[0]。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
