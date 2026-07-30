r"""`ZcTaskYNR` 的参考实现 + 自检。算法见 notes/static-ynr.md。

**没有对过引擎。** 与 `bsnr_ref.py` 同一定位:查的是从反汇编独立读出的性质。

这一级在中性档位(UI 50 → P = 0)是恒等变换,只有把滑块推过 50 才启动 ——
它是 Edit 在「你要求更多降噪」时才加上的那一档,不是常开级(常开的是
`bsnr_ref.py` 的 BSNR_Y)。

用法:
    python ynr_ref.py
"""
from __future__ import annotations

import numpy as np

#: 掩码读 y±1/x±1、5x5 中值读 ±2,引擎不做边界钳制,ROI 必须内缩。
MARGIN = 2

#: 构造函数默认值(`0x14014e580`):3x3 中值、Sobel 阈值 8191。
DEFAULT_MODE = 3
DEFAULT_T = 0x1FFF


def sobel_mask(a: np.ndarray, t: int) -> np.ndarray:
    """`0x14039d740`:标准 Sobel,硬阈值成 0 / 0xffff 的二值掩码。

    判据是 `(gx² + gy²) / 2 > T²`,也就是两个分量的**均方根**超过 T,
    不是模长 `sqrt(gx²+gy²)` —— 差一个 sqrt(2)。除以 2 是向零取整。
    """
    p = a.astype(np.int64)
    up, mid, dn = p[:-2], p[1:-1], p[2:]
    gy = ((dn[:, :-2] + 2 * dn[:, 1:-1] + dn[:, 2:])
          - (up[:, :-2] + 2 * up[:, 1:-1] + up[:, 2:]))
    gx = ((up[:, 2:] + 2 * mid[:, 2:] + dn[:, 2:])
          - (up[:, :-2] + 2 * mid[:, :-2] + dn[:, :-2]))
    mag = (gx * gx + gy * gy)
    mag = np.where(mag < 0, -((-mag) // 2), mag // 2)  # 向零取整
    out = np.zeros(a.shape, dtype=np.int64)
    out[1:-1, 1:-1] = np.where(mag > t * t, 0xFFFF, 0)
    return out


def median_filter(a: np.ndarray, size: int) -> np.ndarray:
    """`0x1401ce7f0`(3x3) / `0x1401ce980`(5x5):收集邻域 → 全排序 → 取中间项。

    引擎调的是 MSVC STL 的 introsort 然后读第 `count // 2` 项,所以是精确中值。
    """
    r = size // 2
    p = np.pad(a.astype(np.int64), r, mode="edge")
    taps = np.stack([p[dy:dy + a.shape[0], dx:dx + a.shape[1]]
                     for dy in range(size) for dx in range(size)], axis=0)
    taps.sort(axis=0)
    return taps[taps.shape[0] // 2]


def ynr(a: np.ndarray, p: int, t: int = DEFAULT_T, mode: int = DEFAULT_MODE) -> np.ndarray:
    """一趟 YNR。`p` 是混合百分比(recipe 设置键 0x8027);0 = 恒等。

    `p < 0` 会让 w 变负,也就是朝**远离**中值的方向外插 —— 即锐化。
    这是同一个字段在滑块负半边的行为,不是异常。
    """
    a = a.astype(np.int64)
    b = median_filter(a, 5 if mode == 2 else 3)
    c = sobel_mask(a, t)
    w = ((0xFFFF - c) * p) // 100
    out = (a * (0xFFFF - w) + b * w) // 0xFFFF
    valid = np.zeros(a.shape, dtype=bool)
    valid[MARGIN:-MARGIN, MARGIN:-MARGIN] = True
    return np.where(valid, out, a).astype(np.int32)


# ── 自检 ────────────────────────────────────────────────────────────────────


def _report(name: str, ok: bool, detail: str = "") -> bool:
    print(f"  [{'ok' if ok else '!!'}] {name}" + (f"  {detail}" if detail else ""))
    return ok


def main() -> int:
    rng = np.random.default_rng(0)
    good = True
    core = (slice(MARGIN, -MARGIN), slice(MARGIN, -MARGIN))

    base = np.full((96, 96), 1200, np.int32)
    noisy = np.clip(base + rng.normal(0, 30, base.shape), 0, 0x3FFF).astype(np.int32)

    # 1. P = 0 必须是恒等 —— 这是中性档位的行为。
    good &= _report("P=0 → 恒等", np.array_equal(ynr(noisy, 0), noisy))

    # 2. P = 100 在平坦区应当就是中值本身。
    o = ynr(noisy, 100)
    med = median_filter(noisy, 3)
    good &= _report("P=100 平坦区 = 中值",
                    np.array_equal(o[core], med[core].astype(np.int32)),
                    f"max|Δ| = {np.abs(o[core] - med[core]).max()}")

    # 3. 降噪量随 P 单调。
    print(f"\n  {'P':>5} {'噪声 std':>10} {'×':>7} {'脉冲残留':>9} {'掩码命中':>9}")
    print("  " + "-" * 45)
    imp = np.full((64, 64), 1200, np.int32)
    imp[32, 32] = 0x3FFF
    stds = []
    for p in (0, 25, 50, 75, 100):
        o = ynr(noisy, p)
        oi = ynr(imp, p)
        std = o[core].std()
        stds.append(std)
        left = (oi[32, 32] - 1200) / (0x3FFF - 1200) * 100
        hit = sobel_mask(imp, DEFAULT_T)[core].astype(bool).mean() * 100
        print(f"  {p:>5} {std:>10.2f} {std / noisy[core].std():>7.2f} {left:>8.1f}% {hit:>8.1f}%")
    good &= _report("降噪量随 P 单调下降", all(a >= b for a, b in zip(stds, stds[1:])))

    # 4. 5x5 应当比 3x3 更狠。
    o3 = ynr(noisy, 100, mode=3)
    o5 = ynr(noisy, 100, mode=2)
    good &= _report("5x5 比 3x3 更狠", o5[core].std() < o3[core].std(),
                    f"3x3 std={o3[core].std():.2f}  5x5 std={o5[core].std():.2f}")

    # 5. 阶跃:掩码应当在边上命中,两侧电平因此保住。
    step = np.full((64, 64), 500, np.int32)
    step[:, 32:] = 3000
    m = sobel_mask(step, DEFAULT_T)
    o = ynr(step, 100)
    lo_err = np.abs(o[MARGIN:-MARGIN, MARGIN:28] - 500).max()
    hi_err = np.abs(o[MARGIN:-MARGIN, 36:-MARGIN] - 3000).max()
    good &= _report("阶跃两侧电平保持", lo_err == 0 and hi_err == 0,
                    f"掩码在边上命中 {m[:, 30:34].astype(bool).any()}, "
                    f"暗侧 max|Δ|={lo_err}, 亮侧 max|Δ|={hi_err}")

    # 6. P < 0 是锐化方向 —— 对比度应当上升。
    o = ynr(noisy, -100)
    good &= _report("P<0 → 朝远离中值外插(锐化)", o[core].std() > noisy[core].std(),
                    f"std {noisy[core].std():.2f} → {o[core].std():.2f}")

    print("\n" + ("全部通过" if good else "有未通过项"))
    return 0 if good else 1


if __name__ == "__main__":
    raise SystemExit(main())
