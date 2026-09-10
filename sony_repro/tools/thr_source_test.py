"""阈值是拿**谁**去查表的 —— 中心像素,还是 25 个抽头的均值。

`filt_accept_solve.py` 把剩下的错误点定死成一件事:引擎在那儿**多接纳了一个**
我们拒掉的抽头,而那个抽头离阈值 +1.03~+7.72(中位 +2.01)。差这么开,不是浮点
边界能解释的;而所有抽头的 gap 会被同一个量整体平移,所以这等价于说**引擎用的
阈值比我们大**。

`notes/static-rawnr.md` 6 早就记过一条,我先前把它读成了别的意思:`0x3a12c5` 的
`vmulps ymm11`(那个 1/25)算出的是 25 个抽头的均值,而它紧接着喂给
`vmaxps/vminps/vcvttss2si` 和一次**查表**。也就是说,查阈值用的下标是那个均值,
不是中心像素。

平坦区里两者几乎相等 —— 所以之前所有受控输入的实验都测不出这个差别(平面是常数
时中心就等于均值),偏偏在强边缘上差得最多,而剩下的错误点全在强边缘。

`vcvttss2si` 是**截断**取整,不是四舍五入,这里一并试。
"""
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import numpy as np

from llr_worker.sony.rawnr_simd import (
    BASE_GREEN_OTHER,
    BASE_GREEN_OWN,
    BASE_RB,
    FILT_MARGIN,
    LEVEL_MAX,
    TAP_SPACING,
    TAPS_PER_SIDE,
)

HERE = os.path.dirname(os.path.abspath(__file__))
SLOTS = ["rb0", "g0", "g1", "rb1"]


def sh(a, dy, dx, r):
    h, w = a.shape
    return a[r + dy:h - r + dy, r + dx:w - r + dx]


def run(z, slot, source="centre", rounding="trunc", delta=0.0, scale=1.0,
        base_clip=0.0, iterate="", member_clip=0.0, engine_order=False,
        always_centre=False, order="", acc64=False, tap_order=""):
    ref, det = z[f"{slot}_ref"], z[f"{slot}_detail"]
    tbl0 = z[f"{slot}_tbl0"]
    blend = int(z[f"{slot}_tbl3"][0])
    gain, limit = int(z[f"{slot}_gain"][0]), int(z[f"{slot}_limit"][0])
    off = float(z[f"{slot}_offset"][0])
    green = slot.startswith("g")
    base_own = BASE_GREEN_OWN if green else BASE_RB
    base_other = BASE_GREEN_OTHER[int(slot[1])] if green else ()
    other = z[f"{slot}_ref2"] if green else None

    r = FILT_MARGIN
    centre = sh(ref, 0, 0, r)
    n = len(base_own) + len(base_other)
    w = np.float32(blend / 1024.0)
    if member_clip:
        # **成员级**限幅:每个成员先相对中心钳一下,再平均。和 base_clip(对整体
        # 钳)完全不同 —— 平坦区一点影响没有(所以受控实验测不出来),强边缘上把
        # 远端成员拉回中心,正是 base_residual_solve 量到的那个形状
        # (偏差与 centre−base 相关 0.9456)。
        lim0 = tbl0[np.clip(np.trunc(centre), 0, LEVEL_MAX).astype(np.int32)]
        lim = lim0.astype(np.float32) * np.float32(member_clip)
        acc = np.zeros_like(centre)
        for tag, tbl in (("own", base_own), ("oth", base_other)):
            src = ref if tag == "own" else other
            for dy, dx in tbl:
                acc = acc + np.clip(sh(src, dy, dx, r) - centre, -lim, lim)
        members = acc + centre * np.float32(n)
    elif order:
        # base 的 25 个成员按什么顺序相加。float32 加法不满足结合律,而这两张表的
        # 排列顺序是我随手写的、不是引擎的 —— 红蓝(9 个成员)已经严格逐位,绿色
        # (25 个,跨两个平面)还差 0.006%,累加顺序是仅剩的差别之一。
        items = ([(dy, dx, 0) for dy, dx in base_own]
                 + [(dy, dx, 1) for dy, dx in base_other])
        if order == "行序":               # 按 (dy, dx) 排,两平面交错
            items.sort(key=lambda t: (t[0], t[1], t[2]))
        elif order == "平面优先":          # 先 own 全部,再 other,各自按行
            items.sort(key=lambda t: (t[2], t[0], t[1]))
        elif order == "逆序":
            items = items[::-1]
        acc = None
        for dy, dx, which in items:
            v = sh(ref if which == 0 else other, dy, dx, r)
            acc = v if acc is None else acc + v
        members = acc
    else:
        members = sum(sh(ref, dy, dx, r) for dy, dx in base_own)
        if base_other:
            members = members + sum(sh(other, dy, dx, r) for dy, dx in base_other)
    if engine_order:
        # 反汇编读出来的确切顺序(0x3a13d8..0x3a13f0):
        #   base = [(1024 − blend)·m25 + blend·centre] · (1/1024)
        # 常量 1024.0 与 1/1024 是从 0x4DF260 / 0x4DEA7C 读到的。数学上等于
        # w·centre+(1−w)·m25,但**浮点上不等价** —— 引擎先用整数尺度的 512 去乘、
        # 最后才乘 1/1024,而边界点正是被最低位决定的。
        # 归一化用**乘以 1/n**,不是除以 n:笔记记过 0x3a12c5 的 `vmulps ymm11`
        # 就是那个 1/25。1/25 = 0.04 在 float32 里不精确,`Σ*0.04` 与 `Σ/25`
        # 不等价,而边界点由最低位决定。
        c1 = np.float32(1024.0 - blend)
        c2 = np.float32(blend)
        base = ((c1 * (members * np.float32(1.0 / n)) + c2 * centre)
                * np.float32(1.0 / 1024.0))
    else:
        base = w * centre + (np.float32(1.0) - w) * (members / np.float32(n))

    offs = [k * TAP_SPACING for k in range(-TAPS_PER_SIDE, TAPS_PER_SIDE + 1)]
    tap_sum = sum(sh(ref, dy, dx, r) for dy in offs for dx in offs)
    tap_mean = tap_sum * np.float32(1.0 / 25.0)

    src = {"centre": centre, "tapmean": tap_mean, "base": base}[source]
    idx = np.trunc(src) if rounding == "trunc" else np.round(src)
    thr = tbl0[np.clip(idx, 0, LEVEL_MAX).astype(np.int32)].astype(np.float32)
    thr = thr * np.float32(scale) + np.float32(delta)
    # base 相对中心的限幅,以阈值为单位。`base_residual_solve.py` 量到剩下的错点
    # 上"引擎的 base 比我们的更靠近 centre",而且偏差与 (centre − base) 相关
    # 0.85 —— 限幅正好是这个形状:平坦区永远碰不到,只有强边缘才触发。
    if base_clip:
        lim = thr * np.float32(base_clip)
        base = centre + np.clip(base - centre, -lim, lim)

    # 逐个累加,和生产代码一模一样。
    #
    # ⚠️ 别改成 np.stack(...).sum(-1) 或 einsum:试过两次,绿色两路都会算出同一个
    # 错数(70.39%/68.34%),红蓝却纹丝不动 —— 那是这条向量化路径自己的毛病,不是
    # 被测假设的结论。曾经据此差点把「至少接纳 K 个」判成"明显不成立"。
    def sweep(b):
        # acc64:用 float64 累加。引擎若真有更高精度的中间量,这样应当**更近**;
        # 若反而更远,就说明它是纯 float32、我们这条路径是对的,剩下的只是不同
        # 实现的固有差异(反汇编里没有 FMA,累加也是一串线性 vaddps)。
        dt = np.float64 if acc64 else np.float32
        t = np.zeros(b.shape, dt)
        c = np.zeros(b.shape, dt)
        # 抽头的遍历顺序。float32 加法不结合,而反汇编里那串 vaddps 是分组的
        # (九个指针一段),说明引擎未必按行优先走 —— base 成员的顺序试过了,
        # 抽头累加的顺序一直没试。
        pairs = [(dy, dx) for dy in offs for dx in offs]
        if tap_order == "列优先":
            pairs = [(dy, dx) for dx in offs for dy in offs]
        elif tap_order == "逆序":
            pairs = pairs[::-1]
        elif tap_order == "由内而外":
            pairs.sort(key=lambda p: (max(abs(p[0]), abs(p[1])), p))
        elif tap_order == "由外而内":
            pairs.sort(key=lambda p: (-max(abs(p[0]), abs(p[1])), p))
        for dy, dx in pairs:
            if True:
                v = sh(ref, dy, dx, r)
                ok = (np.abs(b - v) < thr).astype(np.float32)
                # 反汇编里 vandnps/vmovmskps 各 **24** 组,而抽头有 **25** 个。
                # 若少的那个是中心、且它无条件进,就精确解释了"总是多接纳最近的
                # 那个被拒抽头"—— base 由 centre 主导,中心几乎总是最近的一个,
                # 而强边缘上 |base−centre| = (1−w)|m25−centre| 会超过阈值。
                if always_centre and dy == 0 and dx == 0:
                    ok = np.ones_like(ok)
                t += ok.astype(dt) * v.astype(dt)
                c += ok.astype(dt)
        return np.where(c > 0.0, t / np.maximum(c, 1.0),
                        centre.astype(dt)).astype(np.float32)

    mean = sweep(base)
    # 迭代:经典 sigma filter 常做两遍,拿第一遍的均值当第二遍的比较基准。它只在
    # count 很小的点上改动大 —— 而剩下的错点正是 count 中位为 2 的强边缘点。
    if iterate:
        b2 = {"mean": mean,
              "半中心": (centre + mean) * np.float32(0.5),
              "按 blend": w * centre + (np.float32(1.0) - w) * mean}[iterate]
        mean = sweep(b2)
    boost = np.clip(sh(det, 0, 0, r) * np.float32(gain / 256.0), -limit, limit)
    return np.clip(mean + boost - np.float32(off), 0.0, 262143.0)


def main() -> int:
    names = sys.argv[1:] or ["rawnr_full_fl_test.npz", "rawnr_full_DSC03036.npz"]
    r, W = FILT_MARGIN, 6
    for name in names:
        z = np.load(os.path.join(HERE, name))
        print(f"{name}")
        cases = [(f"查表用 {s:<8}{rd:<6}", {"source": s, "rounding": rd})
                 for s in ("centre", "tapmean", "base") for rd in ("trunc", "round")]
        # 阈值若有系统性偏移,一个常数或一个比例就该把那 96 个点收回来;
        # 若总体反而变差,说明不是全局的,而是某种局部兜底。
        cases += [(f"阈值 +{d:<16g}", {"delta": d}) for d in (0.5, 1, 2, 4)]
        cases += [(f"阈值 x{s:<16g}", {"scale": s}) for s in (1.02, 1.05, 1.1)]
        # 「至少接纳 K 个」不在这里试:`filt_accept_solve.py` 已经把它否掉了 ——
        # count=1 与 count=2 的点**都只多接纳一个**,不符合任何固定的 K。
        cases += [(f"base 限幅 {c:<12g}", {"base_clip": c})
                  for c in (0.5, 0.75, 1.0, 1.5, 2.0, 3.0)]
        cases += [(f"迭代二遍:{k:<11}", {"iterate": k})
                  for k in ("mean", "半中心", "按 blend")]
        cases += [(f"成员限幅 {c:<12g}", {"member_clip": c})
                  for c in (0.5, 1.0, 1.5, 2.0, 3.0, 4.0)]
        cases += [("中心无条件(基线)      ", {"always_centre": True})]
        cases += [(f"  +成员累加 {o:<9}", {"always_centre": True, "order": o})
                  for o in ("行序", "平面优先", "逆序")]
        cases += [("  +引擎的 base 顺序   ", {"always_centre": True,
                                          "engine_order": True}),
                  ("  +float64 累加      ", {"always_centre": True,
                                           "acc64": True})]
        cases += [(f"  +抽头顺序 {o:<9}", {"always_centre": True, "tap_order": o})
                  for o in ("列优先", "逆序", "由内而外", "由外而内")]
        for label, kw in cases:
            cells = []
            for slot in SLOTS:
                got = run(z, slot, **kw)
                h, w = z[f"{slot}_ref"].shape
                eng = z[f"{slot}_out"][W:h - W, W:w - W]
                sl = (slice(W - r, h - W - r), slice(W - r, w - W - r))
                cells.append(f"{100 * float(np.mean(got[sl] == eng)):8.4f}%")
            print(f"    {label} " + " ".join(cells))
        print(f"    {'':>22}" + " ".join(f"{s:>9}" for s in SLOTS) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
