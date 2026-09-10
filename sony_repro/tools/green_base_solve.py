"""在受控输入上枚举绿色核的比较基准与除数。

已经确定的:抽头集是自身平面的 5x5、行列间距 2、等权(`green_impulse_solve.py`),
另一绿平面对输出毫无影响(`green_other_role.py`)。所以绿色核与 R/B 仅剩的差异
只可能在**比较基准**和**除数**上,而这两样在噪声幅度超过阈值时才显形 ——
本捕获的幅度就是按这个挑的。

与 `green_base_test.py` 的区别:那边用真实画面,平坦区里候选彼此高度相关,
分不清谁是谁;这边的输入是白噪声,没有空间相关,枚举出的差别是真差别。

枚举两个维度:
  * base ∈ {centre, m9(3x3 间距2), m25(5x5 间距2), 以及它们与中心按 tbl3 混合}
  * 除数 ∈ {count(sigma 滤波器), 25(固定)}
判据是逐位相同率 —— 输入是我们自己灌的,没有上游误差可以推诿。
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


def run(ref, tbl0, blend, offset, base_kind, divisor, thr_from="centre"):
    """`thr_from` 是阈值表的索引源。

    R/B 用中心的 `ref` 去查(`0x3a1b00` 里的 max/min/cvttss2si 紧跟着中心那一路)。
    绿色的 `0x1403a12c5 vmulps ymm11` —— 也就是 25 抽头均值乘 1/25 —— 之后**紧跟**
    同一串 max/min/cvttss2si 加表取,所以它很可能是拿那个均值去索引的。
    """
    r = FILT_MARGIN
    centre = shifted(ref, 0, 0, r)
    offs = [k * S for k in range(-TAPS, TAPS + 1)]
    m9 = sum(shifted(ref, dy, dx, r) for dy in (-S, 0, S)
             for dx in (-S, 0, S)) / np.float32(9.0)
    m25 = sum(shifted(ref, dy, dx, r) for dy in offs
              for dx in offs) / np.float32(25.0)

    w = np.float32(blend / 1024.0)
    if base_kind == "m25pure":
        base = m25
    elif base_kind == "m9pure":
        base = m9
    else:
        base = w * centre + (np.float32(1.0) - w) * {
            "centre": centre, "m9": m9, "m25": m25}[base_kind]

    idx = {"centre": centre, "m25": m25, "base": base}[thr_from]
    thr_c = tbl0[np.clip(idx, 0, 16383).astype(np.int32)].astype(np.float32)

    total = np.zeros_like(base)
    count = np.zeros_like(base)
    for dy in offs:
        for dx in offs:
            v = shifted(ref, dy, dx, r)
            ok = (np.abs(base - v) < thr_c).astype(np.float32)
            total += ok * v
            count += ok
    if divisor == "25":
        mean = total / np.float32(25.0)
    else:
        mean = np.where(count > 0.0, total / np.maximum(count, 1.0), centre)
    # detail 已被清零,所以没有 boost 这一项。
    return np.clip(mean - np.float32(offset), 0.0, 262143.0), count


def main():
    for name in (sys.argv[1:] or ["green_impulse_fl_test_w0_noise.npz",
                                  "green_impulse_fl_test_w1_noise.npz"]):
        p = os.path.join(HERE, name)
        if not os.path.exists(p):
            print(f"{name}: 不存在")
            continue
        z = np.load(p)
        ref, out = z["ref"], z["out"]
        tbl0, tbl3 = z["tbl0"], z["tbl3"]
        off = float(z["offset"][0])
        h, w = ref.shape
        eng = out[W:h - W, W:w - W]
        sl = (slice(W - FILT_MARGIN, h - W - FILT_MARGIN),
              slice(W - FILT_MARGIN, w - W - FILT_MARGIN))

        print(f"\n=== {name}  幅度 {float(z['amp'][0]):.0f}  offset={off:.0f}")
        print(f"  tbl3:范围 [{tbl3.min()}, {tbl3.max()}]  "
              f"{'常数' if tbl3.min() == tbl3.max() else '非常数!'}  取 {int(tbl3[0])}")
        print(f"  tbl0:范围 [{tbl0.min()}, {tbl0.max()}]  "
              f"在 800 附近 {int(tbl0[800])}")
        best = None
        for bk in ("centre", "m9", "m25", "m9pure", "m25pure"):
            for tf in ("centre", "m25", "base"):
                got, cnt = run(ref, tbl0, int(tbl3[0]), off, bk, "count", tf)
                err = got[sl] - eng
                same = 100 * float(np.mean(err == 0))
                if best is None or same > best[0]:
                    best = (same, bk, tf)
                print(f"  base={bk:<8} 阈值索引={tf:<7} 逐位相同 {same:8.4f}%  "
                      f"|误差| 最大 {float(np.abs(err).max()):9.4g}  "
                      f"中位 {float(np.median(np.abs(err))):.4g}")
        # 固定除数已被排除,只留一行作对照。
        got, _ = run(ref, tbl0, int(tbl3[0]), off, "m25", "25")
        print(f"  (对照)除数固定 25:逐位相同 "
              f"{100*float(np.mean(got[sl] - eng == 0)):.4f}%")
        got, cnt = run(ref, tbl0, int(tbl3[0]), off, best[1], "count", best[2])
        c = cnt[sl]
        print(f"  最好:base={best[1]} 阈值索引={best[2]}  {best[0]:.4f}%")
        print(f"  该组合下 count 分布:最小 {c.min():.0f} 中位 {np.median(c):.0f} "
              f"最大 {c.max():.0f}  为 0 的占 {100*float(np.mean(c==0)):.4f}%")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
