"""对剩下的差异点,逐一试各种成员累加次序,看哪种能同时对上引擎。

`exact_point_check.py` 已经证明:用 float32 标量按正确的次序算,结果能和引擎逐位
一致。把累加改成"连续累加"之后,绿色从 99.9940% 到 99.9978%,还剩 4 个点 —— 那 4 个
点标量与数组一致、都与引擎不同,说明**次序还差一点**。

次序在反汇编里看不到(base 的成员累加在更靠前的位置,基址运行时才算出来),但可以
反过来搜:候选次序不多,对每个差异点用标量算一遍,哪种次序能把这几个点全对上,哪种
就是引擎用的。

⚠️ 只用**差异点**判优会过拟合 —— 一种次序可能修好这 4 个却弄坏原本对的几万个。
所以每种次序都要用**整幅**重算一遍作最终确认,不能只看这几个点。
"""
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import numpy as np

from llr_worker.sony.rawnr_simd import (
    BASE_GREEN_OTHER,
    BASE_GREEN_OWN,
    BLEND_UNIT,
    FILT_MARGIN,
    TAP_SPACING,
    TAPS_PER_SIDE,
)

HERE = os.path.dirname(os.path.abspath(__file__))
F = np.float32


def orders(own, cross):
    """候选次序:(标签, [(dy, dx, 0=own/1=cross), ...])。"""
    o = [(dy, dx, 0) for dy, dx in own]
    c = [(dy, dx, 1) for dy, dx in cross]
    out = {"own→cross(当前)": o + c, "cross→own": c + o}
    # 按行交错:引擎按行推进,同一行里两个平面都要读。
    both = o + c
    out["按行 own先"] = sorted(both, key=lambda t: (t[0], t[2], t[1]))
    out["按行 cross先"] = sorted(both, key=lambda t: (t[0], -t[2], t[1]))
    out["按行列 混排"] = sorted(both, key=lambda t: (t[0], t[1], t[2]))
    out["逆序"] = (o + c)[::-1]
    return out


def scalar(ref, other, tbl0, blend, gain, limit, off, seq, det, y, x):
    n = len(seq)
    centre = F(ref[y, x])
    acc = F(0.0)
    for dy, dx, which in seq:
        src = ref if which == 0 else other
        acc = F(acc + F(src[y + dy, x + dx]))
    m = F(acc * F(F(1.0) / F(n)))
    base = F(F(F(F(BLEND_UNIT) - F(blend)) * m) + F(F(blend) * centre))
    base = F(base * F(F(1.0) / F(BLEND_UNIT)))
    thr = F(tbl0[int(np.clip(centre, 0, tbl0.shape[0] - 1))])
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
    mean = F(total / count)
    b = F(F(det[y, x]) * F(F(gain) / F(256.0)))
    b = F(min(max(b, F(-limit)), F(limit)))
    return F(min(max(F(F(mean + b) - F(off)), F(0.0)), F(262143.0)))


def main() -> int:
    name = sys.argv[1] if len(sys.argv) > 1 else "rawnr_full_fl_test.npz"
    slot = sys.argv[2] if len(sys.argv) > 2 else "g0"
    z = np.load(os.path.join(HERE, name))
    ref, other = z[f"{slot}_ref"], z[f"{slot}_ref2"]
    det, tbl0 = z[f"{slot}_detail"], z[f"{slot}_tbl0"]
    blend = int(z[f"{slot}_tbl3"][0])
    gain, limit = int(z[f"{slot}_gain"][0]), int(z[f"{slot}_limit"][0])
    off = float(z[f"{slot}_offset"][0])
    cross = BASE_GREEN_OTHER[int(slot[1])]

    from llr_worker.sony.rawnr_simd import filt
    got = filt(det, ref, tbl0, blend=blend, gain=gain, limit=limit, offset=off,
               other=other, base_own=BASE_GREEN_OWN, base_other=cross)
    h, w = ref.shape
    r, W = FILT_MARGIN, 6
    eng = z[f"{slot}_out"][W:h - W, W:w - W]
    sl = (slice(W - r, h - W - r), slice(W - r, w - W - r))
    ys, xs = np.nonzero(got[sl] - eng != 0)
    print(f"{name} {slot}: 差异点 {ys.size} 个\n")
    if ys.size == 0:
        return 0

    print("  次序                 这几个点对上几个")
    for label, seq in orders(BASE_GREEN_OWN, cross).items():
        hit = 0
        for k in range(ys.size):
            yy, xx = int(ys[k]) + W, int(xs[k]) + W
            s = float(scalar(ref, other, tbl0, blend, gain, limit, off, seq,
                             det, yy, xx))
            if abs(s - float(eng[ys[k], xs[k]])) < 1e-6:
                hit += 1
        print(f"    {label:<18} {hit}/{ys.size}")
    print("\n  ⚠️ 有次序把这几个点全对上,也要用整幅重算确认 —— 只看差异点会过拟合。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
