r"""从注入图案里读 Vatr 的传递函数,并与参数块里那条 103 点曲线对照。

block 图案:每块内部远离边界的地方,邻域 == 自身,所以量到的就是**纯逐像素**的
输入->输出关系。patch 图案:小方块 V 摆在背景 B 上,同一个 V 在不同 B 下的输出
差多少,就是空间项的大小。

用法: python vatr_curve.py vatrprobe/inj_block.npz [vatrparam/DSC02857.bin]
"""
import os
import sys

import numpy as np

SCR = os.path.dirname(os.path.abspath(__file__))


def main():
    z = np.load(sys.argv[1])
    a, b = z["inp"].astype(np.float64), z["out"].astype(np.float64)
    lv, meta = z["levels"], z["meta"]
    bg, bs, ps, tile = meta
    h, w = a.shape
    print(f"图案 bg={bg} 块={bs} patch={ps} tile={tile}   输出形状 {b.shape}")

    # 每块中心 1/3 的区域(彻底躲开边界)
    print(f"\n{'输入':>7} {'n':>7} {'out R':>10} {'out G':>10} {'out B':>10} "
          f"{'增益':>8} {'log2 增量':>10}")
    n = 0
    acc = {}
    for by in range(0, h, bs):
        for bx in range(0, w, bs):
            v = int(lv[n % len(lv)]); n += 1
            y0, y1 = by + bs // 3, min(by + 2 * bs // 3, h)
            x0, x1 = bx + bs // 3, min(bx + 2 * bs // 3, w)
            if y1 <= y0 or x1 <= x0:
                continue
            if not (a[y0:y1, x0:x1] == v).all():
                continue
            acc.setdefault(v, []).append(b[y0:y1, x0:x1].reshape(-1, 3))
    for v in sorted(acc):
        s = np.concatenate(acc[v])
        m = s.mean(0)
        g = m[1] / v if v else np.nan
        print(f"{v:>7} {len(s):>7} {m[0]:>10.2f} {m[1]:>10.2f} {m[2]:>10.2f} "
              f"{g:>8.4f} {np.log2(g) if g > 0 else 0:>10.4f}")

    if len(sys.argv) > 2:
        p = np.fromfile(sys.argv[2], dtype="<f4")
        cur = p[0x7c8 // 4: 0x7c8 // 4 + 103]
        print("\n参数块 P+0x7c8 的 103 点曲线(x = (i+1)/8,单位 log2):")
        for i in (7, 15, 23, 31, 39, 47, 55, 63, 71, 79, 87, 95, 102):
            x = (i + 1) / 8
            print(f"  i={i:>3} 输入 log2={x:>6.3f} (={2**x:>9.1f})  "
                  f"输出 {cur[i]:>7.4f}  增量 {cur[i] - x:+.4f}  "
                  f"增益 {2 ** (cur[i] - x):.4f}")


if __name__ == "__main__":
    main()
