"""绿色滤波核 `0x3a0c30` 的抽头集 —— 有捕获就别纯读反汇编。

`static-rawnr.md` §6 把骨架读出来了:25 个抽头、与 R/B 同构的收尾、邻域均值的
系数是 `1/25`(R/B 是 `1/9`)、输出钳位 262143。卡住的是抽头集本身,原因是核把
抽头值预载进寄存器,只看内存操作数定不下来。

但抓过的核上下文就在手边(`rawnr_kern_fl_test_g_s0w0.npz`)。有输入有输出,
抽头集就是一个**可解的问题**,不必逐条回溯寄存器。

先探查:这份捕获里到底有什么、形状多大、平面怎么排。
"""
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import numpy as np

HERE = Path("/home/jannchie/llr/sony_repro/tools")


def main():
    names = sys.argv[1:] or [
        "rawnr_kern_fl_test_g_s0w0.npz",
        "rawnr_kern_fl_test_rb_s0w0.npz",
        "rawnr_kern_fl_test_c0.npz",
    ]
    for n in names:
        p = HERE / n
        if not p.exists():
            print(f"{n}: 不存在")
            continue
        z = np.load(p, allow_pickle=True)
        print(f"\n=== {n}")
        for k in z.files:
            a = z[k]
            if a.dtype == object or a.ndim == 0 or a.dtype.kind in "USO":
                print(f"  {k:<24} {a.dtype}  {a!r}"[:200])
                continue
            print(f"  {k:<24} {str(a.dtype):<10} {str(a.shape):<18}"
                  f" min {float(a.min()):10.2f} max {float(a.max()):10.2f}"
                  f" mean {float(a.mean()):9.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
