"""tbl0 / tbl3 在用到的那一段到底是什么值。

`green_base_isolate.py` 一直拿 `tbl3[0]` 当混合权重 w,依据是笔记里「tbl3 是常数
512」。但 tbl3 有 32768 项,而滤波核每个点是**按自己的 ref 值查表**的 —— 若
tbl3 在 dc=800 附近不是 512,那 w 就一直用错,由它反推出来的窗口尺寸也全错。
"""
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))


def main() -> int:
    name = sys.argv[1] if len(sys.argv) > 1 else "green_impulse_fl_test_w0_ring.npz"
    z = np.load(os.path.join(HERE, name))
    dc = int(z["dc"][0])
    for key in ("tbl0", "tbl3"):
        t = z[key]
        print(f"{key}: 长度 {t.size}  取值种数 {np.unique(t).size}  "
              f"范围 [{t.min()}, {t.max()}]")
        lo, hi = max(0, dc - 4), min(t.size, dc + 5)
        print(f"  [{lo}..{hi}) = {t[lo:hi].tolist()}")
        # 常数段有多长:从 dc 往两边走,直到值变化。
        v = t[dc]
        a = dc
        while a > 0 and t[a - 1] == v:
            a -= 1
        b = dc
        while b + 1 < t.size and t[b + 1] == v:
            b += 1
        print(f"  t[{dc}] = {v};以它为值的常数段 [{a}, {b}]")
        idx = np.linspace(0, t.size - 1, 12).astype(int)
        print(f"  抽样 {[(int(i), int(t[i])) for i in idx]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
