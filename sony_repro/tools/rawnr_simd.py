r"""`ZcTaskRawNRSIMD` 的 R/B 支路,按反汇编写出来的复刻。

流水线(每个 CFA 相位一个半分辨率平面):

    0x3a2aa0  分析:  d   = (12*c - 2*N4 - diag) / 16          ← 与 RawNRHalf 逐字相同
                     ref = clamp(c - d + OFFSET, 0, 32767)
    0x3a1b00  滤波:  见下

绿色走 `0x3a26f0` / `0x3a0c30`(分析要同时用两个绿平面,权重 3,总和 24,除以 28),
这里只做 R/B。

滤波(`0x3a1b00`,常量都已核对:16383 / 1/9 / 1024 / 1/1024 / 1/256):

    thr = tbl0[ clamp(ref, 0, 16383) ]
    m9  = ref 的 3x3 均值(间距 S)
    w   = tbl3[ clamp(m9, 0, 16383) ] / 1024
    r   = w*ref + (1-w)*m9                       ← 比较基准是「中心低通与其邻域均值的混合」
    取 5x5(间距 S)共 25 个抽头,只累加 |r - tap| < thr 的
    out = clamp( sum/count + clamp(d*gain/256, -limit, +limit) - offset, 0, 16383 )

抽头取的是 **ref(低通)** 而不是原始像素 —— 这是与 RawNRHalf 的实质差别之一。

**几何是从反汇编读出来的,不是拟合的**:行索引 `y-4, y-2, y, y+2, y+4`
(`0x3a1c87..0x3a1d13`),列偏移 `-0x18,-0x10,-0x8,0,+0x8` 字节即同样的间距 2,
中心在 `-0x8`。5 行 × 5 列 = 25 个抽头,和收尾处累加的 25 个掩码向量对得上。

对实测的成绩(`rawnr_kern_probe.py` 抓的自带上下文捕获,fl_test.ARW ISO 1250):

| 比什么 | 结果 |
|---|---|
| `analysis()` 对引擎的 detail / ref | **100.00% 逐位相同**(零误差) |
| `filt()` 对引擎的 out | **99.9677% 逐位相同**(185481 点里 60 点不同) |
| 端到端 马赛克相位 → out | 同上,解释掉 100.00% |

那 60 个点(0.03%)是**一个抽头的取舍不同**:float32 比 float64 更贴引擎
(60 点 vs 3170 点,证实引擎就是 float32);两种精度下被接纳的抽头数完全一致,
所以不是我这边的阈值比较在翻转;这些点被接纳的抽头数中位只有 6(全体中位 21),
误差量级(中位 6.8 / 最大 13.8)恰好等于小样本均值里一个抽头进出的幅度。

> ⚠️ **不要跨工具按序号配对 tile。** tile 是多线程并行跑的,同一张图连抓两次,
> 「第 0 次调用」的平面尺寸都能不一样。早先按序号配对得出「R 相位只有 65%」,
> 是拿错了 tile,不是模型错。用 `rawnr_kern_probe.py` 的自带上下文捕获。

用法::

    python rawnr_simd.py             # 自检
    python rawnr_simd.py --verify    # 对 rawnr_kern_probe 的捕获逐位比对
"""
import os
import sys

import numpy as np

LEVEL_MAX = 16383
UNIT = 1024.0


def _shift(a, dy, dx, r):
    h, w = a.shape
    return a[r + dy:h - r + dy, r + dx:w - r + dx]


def analysis(plane, offset):
    """0x3a2aa0:返回 (细节 d, 参考电平 ref),都比输入四周各少 1 像素。"""
    a = plane.astype(np.float32)
    c = _shift(a, 0, 0, 1)
    n4 = (_shift(a, -1, 0, 1) + _shift(a, 1, 0, 1)
          + _shift(a, 0, -1, 1) + _shift(a, 0, 1, 1))
    diag = (_shift(a, -1, -1, 1) + _shift(a, -1, 1, 1)
            + _shift(a, 1, -1, 1) + _shift(a, 1, 1, 1))
    d = (np.float32(12.0) * c - np.float32(2.0) * n4 - diag) * np.float32(1.0 / 16.0)
    ref = np.clip(c - d + np.float32(offset), 0.0, 32767.0)
    return d, ref


def filt(d, ref, tbl0, tbl3, gain, limit, offset, spacing=2, taps=2):
    """0x3a1b00。`spacing` 是抽头间距,`taps` 是每边的抽头数(2 -> 5x5)。

    返回 (结果, 自身吃掉的边距)。**边距只算这一步的** —— 早先这里返回的是
    `r+1`(把分析那一层也算了进去),单独调用 `filt` 验证时就对不齐了。
    """
    r = spacing * taps
    idx = np.clip(ref, 0, LEVEL_MAX).astype(np.int32)
    thr = tbl0[idx].astype(np.float32)

    # 3x3 均值(间距 spacing),1/9 来自常量 0x4697f8
    m9 = np.zeros_like(_shift(ref, 0, 0, r))
    for dy in (-spacing, 0, spacing):
        for dx in (-spacing, 0, spacing):
            m9 = m9 + _shift(ref, dy, dx, r)
    m9 = m9 * np.float32(1.0 / 9.0)

    centre = _shift(ref, 0, 0, r)
    w = tbl3[np.clip(m9, 0, LEVEL_MAX).astype(np.int32)].astype(np.float32) / np.float32(UNIT)
    base = w * centre + (np.float32(1.0) - w) * m9

    thr_c = _shift(thr, 0, 0, r)
    total = np.zeros_like(base)
    count = np.zeros_like(base)
    offs = [k * spacing for k in range(-taps, taps + 1)]
    for dy in offs:
        for dx in offs:
            v = _shift(ref, dy, dx, r)
            ok = (np.abs(base - v) < thr_c).astype(np.float32)
            total += ok * v
            count += ok

    mean = total / np.maximum(count, 1.0)
    boost = np.clip(_shift(d, 0, 0, r) * np.float32(gain / 256.0), -limit, limit)
    return np.clip(mean + boost - np.float32(offset), 0.0, LEVEL_MAX), r


def denoise_phase(plane, tbl0, tbl3, gain, limit, offset, spacing=2, taps=2):
    """一个相位平面进出;返回 (结果, 四周被吃掉的边距)。

    边距是分析的 1 加上滤波的 `spacing*taps`。
    """
    d, ref = analysis(plane, offset)
    out, margin = filt(d, ref, tbl0, tbl3, gain, limit, offset, spacing, taps)
    return out, margin + 1


def _selfcheck():
    """常量场必须恒等:细节为 0,所有抽头都通过阈值,均值等于自身。"""
    flat = np.full((64, 64), 4000.0, dtype=np.float32)
    tbl0 = np.full(1 << 15, 50, np.int32)
    tbl3 = np.full(1 << 15, 512, np.int32)
    out, m = denoise_phase(flat, tbl0, tbl3, 256, 1023, 0)
    assert np.allclose(out, 4000.0, atol=1e-3), f"常量场被改动:{np.unique(out)[:5]}"
    print(f"常量场恒等(边距 {m})  ✓")


def _verify(cap="rawnr_kern_fl_test_s0w0.npz"):
    """对 `rawnr_kern_probe.py` 的捕获逐位比对。

    捕获里马赛克和内核的 detail/ref/out 来自**同一次调用**,所以不存在配对问题。
    引擎只写 `[5, n-5)`,比对区域按此裁剪。
    """
    scr = os.path.dirname(os.path.abspath(__file__))
    z = np.load(os.path.join(scr, cap))
    mos = z["mosaic"].astype(np.float32)
    d, ref, out = z["detail"], z["ref"], z["out"]
    tbl0, tbl3 = z["tbl0"], z["tbl3"]
    gain, limit, off = int(z["gain"][0]), int(z["limit"][0]), int(z["offset"][0])
    h, w = ref.shape
    W = 5

    phase = None
    for py in (0, 1):
        for px in (0, 1):
            ph = mos[py::2, px::2]
            if ph.shape != ref.shape:
                continue
            my_d, my_ref = analysis(ph, float(off))
            if np.array_equal(my_ref, ref[1:-1, 1:-1]) and np.array_equal(my_d, d[1:-1, 1:-1]):
                phase = (py, px)
                print(f"analysis: 相位{phase} 的 detail 与 ref 都 100% 逐位相同  ✓")
    if phase is None:
        print("analysis: 没有相位逐位对上 —— 捕获与马赛克可能不同源")
        return

    got, mg = filt(d, ref, tbl0, tbl3, gain, limit, float(off))
    sl = (slice(W - mg, h - W - mg), slice(W - mg, w - W - mg))
    err = got[sl] - out[W:h - W, W:w - W]
    n = int(np.count_nonzero(err))
    print(f"filt:     逐位相同 {100 * np.mean(err == 0):.4f}%  "
          f"({n} / {err.size} 点不同,|误差| 最大 {np.abs(err).max():.4g})")

    e2e, mg2 = denoise_phase(mos[phase[0]::2, phase[1]::2], tbl0, tbl3, gain, limit, float(off))
    sl2 = (slice(W - mg2, h - W - mg2), slice(W - mg2, w - W - mg2))
    err2 = e2e[sl2] - out[W:h - W, W:w - W]
    base = (out - ref)[W:h - W, W:w - W]
    print(f"端到端:   逐位相同 {100 * np.mean(err2 == 0):.4f}%  "
          f"解释掉 {1 - (err2.std() / base.std()) ** 2:.2%}")


if __name__ == "__main__":
    if "--verify" in sys.argv:
        _verify()
    else:
        _selfcheck()
