"""另一个绿平面在滤波核里到底做什么。

抽头几何已经解出来了(`green_impulse_solve.py`):自身平面的 5x5、行列都间距 2、
等权,**另一平面一个抽头都不占**。但签名里确实传了它
(`0x3a0c30(dst, detail, refOwn, refOther, ...)`),所以它一定用在别处。

这一份捕获把自身平面压成常数、只给另一平面灌噪声。于是判据非常干净:

  * 若 `out` 恒等于 `DC − offset`,另一平面对结果**没有影响** —— 那它要么没被
    用,要么只在某个此处未触发的分支里用;
  * 若 `out` 随另一平面变,它就进了比较基准或阈值索引,而两者可以再分开:
    进基准会改变**哪些抽头被接纳**,但自身平面是常数、所有抽头都相等,
    接纳与否不改变均值 —— 除非全被拒。所以**只要 out 偏离常数,就说明它
    参与了某个能改变输出的环节**,值得顺着往下读。
"""
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))


def main():
    name = sys.argv[1] if len(sys.argv) > 1 else "green_impulse_fl_test_w0_own-flat.npz"
    z = np.load(os.path.join(HERE, name))
    ref, ref2, out = z["ref"], z["ref2"], z["out"]
    off = float(z["offset"][0])
    dc = float(z["dc"][0])
    W = 5
    h, w = ref.shape
    core = (slice(W, h - W), slice(W, w - W))
    o = out[core].astype(np.float64)
    r2 = ref2[core].astype(np.float64)

    print(f"自身平面 ref:范围 [{ref.min():.4f}, {ref.max():.4f}]  "
          f"(应当是常数 {dc:.0f})")
    print(f"另一平面 ref2:范围 [{ref2.min():.2f}, {ref2.max():.2f}]  "
          f"标准差 {ref2.std():.3f}")
    expect = dc - off
    print(f"\n若另一平面无影响,out 应恒为 {expect:.1f}")
    print(f"  实测 out:范围 [{o.min():.4f}, {o.max():.4f}]  "
          f"标准差 {o.std():.6f}  均值 {o.mean():.4f}")
    dev = np.abs(o - expect)
    print(f"  偏离常数:均值 {dev.mean():.6f}  最大 {dev.max():.6f}  "
          f"非零占 {100*float(np.mean(dev > 1e-6)):.3f}%")

    if dev.max() < 1e-4:
        print("\n  => 另一平面**不影响**这一路的输出。")
        print("     结合抽头几何(它不占抽头),绿色核在这条路径上与 R/B 同构;")
        print("     借用 R/B 只有 51.7% 的原因必须到别处找 —— 先看 offset 与")
        print("     阈值索引的用法,那是两者仅剩的差异。")
    else:
        c = np.corrcoef(o.ravel(), r2.ravel())[0, 1]
        print(f"\n  => 另一平面**有影响**。out 与 ref2 中心的相关 {c:+.4f}")
        # 它若进基准,影响应当随位置有结构;报几个偏移的相关看形状
        for dy, dx in ((0, 0), (-1, 0), (1, 0), (0, -1), (0, 1), (-1, -1)):
            s = ref2[W + dy:h - W + dy, W + dx:w - W + dx].astype(np.float64)
            print(f"     偏移({dy:+d},{dx:+d}) 相关 "
                  f"{np.corrcoef(o.ravel(), s.ravel())[0,1]:+.4f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
