"""绿色核与 R/B 的差别是不是只在 `base` 的那个均值 —— 3x3 还是 5x5。

`green_kern_solve.py` 的回归说抽头集就是自身平面的 5x5(间距 2),25 个,等权,
与 R/B **同一套几何**(前 20 名里 self 占 19 个,全在偶数偏移;另一平面的项系数
小且带负号,是共线性残留)。那 §6 里"套 R/B 几何只到 40.9%"的差别只能在别处。

笔记自己记了那一条:绿色核**邻域均值的系数是 `1/25`,不是 `1/9`**。R/B 的
`base = w*centre + (1−w)*mean3x3`,而绿色若是 `mean5x5`(间距 2,即抽头集本身
的均值),两者在噪声上的差别不大,在**边缘**上很大 —— 而边缘正是彩边出现的地方。

逐位比对四种组合,让捕获裁决。
"""
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
S, TAPS = 2, 2
FILT_MARGIN = S * TAPS
W = 5


def shifted(a, dy, dx, r):
    h, w = a.shape
    return a[r + dy:h - r + dy, r + dx:w - r + dx]


def filt(d, ref, tbl0, blend, gain, limit, offset, kind):
    """`kind` 挑的是两处未定的选择:`base` 用哪个均值,以及分母是什么。

    反汇编在绿色核里读到 `1/25`,当初记成"邻域均值的系数"。但那个 25 也可能是
    **抽头平均的除数** —— 即绿色无条件除以 25 而不是除以通过阈值的个数,
    那样它就不是 sigma 滤波器,而是一个固定的 5x5 等权低通。两种读法在捕获上
    分得开,所以让捕获说话。
    """
    r = FILT_MARGIN
    thr_c = shifted(tbl0[np.clip(ref, 0, 16383).astype(np.int32)].astype(np.float32), 0, 0, r)
    centre = shifted(ref, 0, 0, r)
    offs = [k * S for k in range(-TAPS, TAPS + 1)]
    m9 = sum(shifted(ref, dy, dx, r) for dy in (-S, 0, S)
             for dx in (-S, 0, S)) / np.float32(9.0)
    m25 = sum(shifted(ref, dy, dx, r) for dy in offs
              for dx in offs) / np.float32(25.0)
    # 反汇编读出来的形状:`0x1403a1206..0x12be` 是 25 个 vaddps 的累加链,
    # 收尾 `vmulps ymm11` 就是 §6 记的那个 1/25,而紧接着的 max/min/cvttss2si
    # + `[r8+rcx*4]` 是**拿它去查阈值表** —— 所以这个均值扮演的是 base。
    # 行偏移在 `0x1403a0e04..0e81` 读到是 y-4,-2,0,+2,+4(间距 2),
    # 列位移在累加链里是 -0x10..0,即 -4..0 个 float(**间距 1**,不是 2)。
    m25r = sum(shifted(ref, dy, dx, r) for dy in offs
               for dx in (-4, -3, -2, -1, 0)) / np.float32(25.0)
    w = np.float32(blend / 1024.0)
    if "b25r" in kind:
        m = m25r
    elif "b25" in kind:
        m = m25
    else:
        m = m9
    base = w * centre + (np.float32(1.0) - w) * m

    if kind == "flat25":
        # 完全不筛选:25 个抽头的等权平均,就是 m25 本身。
        mean = m25
    else:
        total = np.zeros_like(base)
        count = np.zeros_like(base)
        for dy in offs:
            for dx in offs:
                v = shifted(ref, dy, dx, r)
                ok = (np.abs(base - v) < thr_c).astype(np.float32)
                total += ok * v
                count += ok
        if "d25" in kind:
            # 筛选照做,但分母固定 25 —— 被拒的抽头当 0 计入。
            mean = total / np.float32(25.0)
        else:
            mean = np.where(count > 0.0, total / np.maximum(count, 1.0), centre)
    boost = np.clip(shifted(d, 0, 0, r) * np.float32(gain / 256.0), -limit, limit)
    return np.clip(mean + boost - np.float32(offset), 0.0, 262143.0)


def main():
    for cap, name in (("rawnr_kern_fl_test_rb_s0w0.npz", "R/B"),
                      ("rawnr_kern_fl_test_g_s0w0.npz", "绿")):
        z = np.load(os.path.join(HERE, cap))
        d, ref, out = z["detail"], z["ref"], z["out"]
        tbl0, tbl3 = z["tbl0"], z["tbl3"]
        gain, limit = int(z["gain"][0]), int(z["limit"][0])
        off = float(z["offset"][0])
        h, w = ref.shape
        eng = out[W:h - W, W:w - W]
        sl = (slice(W - FILT_MARGIN, h - W - FILT_MARGIN),
              slice(W - FILT_MARGIN, w - W - FILT_MARGIN))
        base_ref = eng - ref[W:h - W, W:w - W]

        print(f"\n=== {cap} ({name},offset={off:.0f})")
        for kind in ("m9", "b25", "b25r", "flat25", "d25"):
            got = filt(d, ref, tbl0, int(tbl3[0]), gain, limit, off, kind)[sl]
            err = got - eng
            same = 100 * float(np.mean(err == 0))
            expl = 1 - (err.std() / base_ref.std()) ** 2
            print(f"  {kind:<8} 逐位相同 {same:8.4f}%  解释 {expl:8.2%}  "
                  f"|误差| 最大 {float(np.abs(err).max()):.4g}  "
                  f"中位 {float(np.median(np.abs(err))):.4g}")
    print("\n  R/B 那一组是对照:它已知用 m9,若 m25 在那里也不差,"
          "说明这份平场捕获分不开两者。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
