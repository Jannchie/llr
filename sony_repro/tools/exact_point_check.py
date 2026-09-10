"""对那十来个差异点,用 float32 **标量**严格按顺序重算一遍。

绿色卡在 99.994%,七条证据都指向"float32 的固有差异"。但那些证据里有一个从没验过
的前提:**numpy 的 float32 数组运算等同于 AVX2 的 float32**。numpy 内部会向量化、
会生成临时数组,求值顺序未必和写出来的表达式一致。

所以对每个差异点,用 `np.float32` 标量一步一步算 —— 每一步都显式舍入到 float32,
顺序完全由代码决定,没有任何临时数组和向量化。

  * 标量结果 == 引擎  => 差异出在 numpy 的数组求值路径上,**可以修**;
  * 标量结果 == 数组结果 != 引擎 => 我们的运算序列本身与引擎不同,别再赖 numpy。
"""
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import numpy as np

from llr_worker.sony.rawnr_simd import (
    BASE_GREEN_OTHER,
    BASE_GREEN_OWN,
    FILT_MARGIN,
    TAP_SPACING,
    TAPS_PER_SIDE,
    filt,
)

HERE = os.path.dirname(os.path.abspath(__file__))
F = np.float32


def scalar_point(ref, other, tbl0, blend, gain, limit, off, base_own,
                 base_other, y, x, detail=None):
    """一个点的完整计算,全程 float32 标量。"""
    n = len(base_own) + len(base_other)
    centre = F(ref[y, x])
    acc = F(0.0)
    for dy, dx in base_own:
        acc = F(acc + F(ref[y + dy, x + dx]))
    for dy, dx in base_other:
        acc = F(acc + F(other[y + dy, x + dx]))
    # 与生产代码同一套顺序:乘 1/n(不是除 n),按 [(1024−blend)·m + blend·centre]/1024
    m = F(acc * F(F(1.0) / F(n)))
    base = F(F(F(F(1024.0) - F(blend)) * m) + F(F(blend) * centre))
    base = F(base * F(F(1.0) / F(1024.0)))

    idx = int(np.clip(centre, 0, tbl0.shape[0] - 1))
    thr = F(tbl0[idx])

    offs = [k * TAP_SPACING for k in range(-TAPS_PER_SIDE, TAPS_PER_SIDE + 1)]
    total = F(0.0)
    count = F(0.0)
    for dy in offs:
        for dx in offs:
            v = F(ref[y + dy, x + dx])
            ok = F(1.0) if (dy == 0 and dx == 0) else (
                F(1.0) if abs(F(base - v)) < thr else F(0.0))
            total = F(total + F(ok * v))
            count = F(count + ok)
    mean = F(total / count) if count > 0 else centre
    # boost 也要算进来 —— 差异点的 detail 全都非零,跳过它们就一个也查不到。
    b = F(0.0)
    if detail is not None:
        b = F(F(detail[y, x]) * F(F(gain) / F(256.0)))
        b = F(min(max(b, F(-limit)), F(limit)))
    v = F(F(mean + b) - F(off))
    return F(min(max(v, F(0.0)), F(262143.0)))


def main() -> int:
    name = sys.argv[1] if len(sys.argv) > 1 else "rawnr_full_fl_test.npz"
    slot = sys.argv[2] if len(sys.argv) > 2 else "g0"
    z = np.load(os.path.join(HERE, name))
    ref, other = z[f"{slot}_ref"], z[f"{slot}_ref2"]
    det, tbl0 = z[f"{slot}_detail"], z[f"{slot}_tbl0"]
    blend = int(z[f"{slot}_tbl3"][0])
    gain, limit = int(z[f"{slot}_gain"][0]), int(z[f"{slot}_limit"][0])
    off = float(z[f"{slot}_offset"][0])
    base_own = BASE_GREEN_OWN
    base_other = BASE_GREEN_OTHER[int(slot[1])]

    got = filt(det, ref, tbl0, blend=blend, gain=gain, limit=limit, offset=off,
               other=other, base_own=base_own, base_other=base_other)
    h, w = ref.shape
    r, W = FILT_MARGIN, 6
    eng = z[f"{slot}_out"][W:h - W, W:w - W]
    sl = (slice(W - r, h - W - r), slice(W - r, w - W - r))
    err = got[sl] - eng
    ys, xs = np.nonzero(err != 0)
    print(f"{name} {slot}: 差异点 {ys.size} 个\n")
    if ys.size == 0:
        return 0

    same_scalar = 0
    checked = 0
    print("  点(在 ref 坐标)      引擎        数组        标量      标量==引擎")
    for k in range(min(ys.size, 12)):
        yy, xx = int(ys[k]) + W, int(xs[k]) + W
        checked += 1
        s = float(scalar_point(ref, other, tbl0, blend, gain, limit, off,
                               base_own, base_other, yy, xx, det))
        e = float(eng[ys[k], xs[k]])
        a = float(got[sl][ys[k], xs[k]])
        hit = abs(s - e) < 1e-6
        same_scalar += hit
        print(f"    ({yy:4d},{xx:4d})   {e:10.4f}  {a:10.4f}  {s:10.4f}   "
              f"{'✓' if hit else '✗'}")
    print(f"\n  查了 {checked} 个(只取 detail==0 的),标量与引擎一致的 {same_scalar} 个")
    print("  一致 => 差异在 numpy 的数组求值路径上,可以修;"
          "\n  不一致 => 运算序列本身与引擎不同,不是 numpy 的问题。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
