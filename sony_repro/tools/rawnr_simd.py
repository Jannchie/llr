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

对实测的成绩(fl_test.ARW ISO 1250,tile12,见 `--score`):

* **B 相位:解释掉 95.0%**(残差 2.10,delta std 9.39)。
* R 相位只到 65.6%,而且要 offset≈768 才最好(B 要 0)。缺口未解 —— 但
  `(真值 - 均值项)` 与本文的 `d` 相关 0.92(B 是 0.9967),所以差在分析/均值那一侧,
  不在收尾公式。

用法::

    python rawnr_simd.py            # 自检
    python rawnr_simd.py --score    # 对 tile_dump/rawnr_probe 抓的真值打分
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
    """0x3a1b00。`spacing` 是抽头间距,`taps` 是每边的抽头数(2 -> 5x5)。"""
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
    return np.clip(mean + boost - np.float32(offset), 0.0, LEVEL_MAX), r + 1


def denoise_phase(plane, tbl0, tbl3, gain, limit, offset, spacing=2, taps=2):
    """一个相位平面进出;返回 (结果, 四周被吃掉的边距)。"""
    d, ref = analysis(plane, offset)
    out, margin = filt(d, ref, tbl0, tbl3, gain, limit, offset, spacing, taps)
    return out, margin


def _selfcheck():
    """常量场必须恒等:细节为 0,所有抽头都通过阈值,均值等于自身。"""
    flat = np.full((64, 64), 4000.0, dtype=np.float32)
    tbl0 = np.full(1 << 15, 50, np.int32)
    tbl3 = np.full(1 << 15, 512, np.int32)
    out, m = denoise_phase(flat, tbl0, tbl3, 256, 1023, 0)
    assert np.allclose(out, 4000.0, atol=1e-3), f"常量场被改动:{np.unique(out)[:5]}"
    print(f"常量场恒等(边距 {m})  ✓")


def _score(tiles="tiles_NR_fl1250.npz", tables="rawnr_tables_fl_test.npz", tile="12"):
    """对 `tile_dump.py` / `rawnr_probe.py` 抓的真值打分。

    **必须按有效矩形裁剪**(`task+0x30`):输出是新分配的缓冲区,矩形外从没写过。
    """
    scr = os.path.dirname(os.path.abspath(__file__))
    t = np.load(os.path.join(scr, tiles))
    k = np.load(os.path.join(scr, tables))
    tbl0, tbl3 = k["tbl0"], k["tbl3"]
    meta = t[f"t{tile}_meta"]
    x0, y0, x1, y1 = (int(v) for v in meta[12:16])
    src = t[f"t{tile}_in"][..., 0].astype(np.float32)[y0:y1, x0:x1]
    dst = t[f"t{tile}_out"][..., 0].astype(np.float32)[y0:y1, x0:x1]

    print(f"{'相位':>4} {'offset':>7} {'残差 std':>9} {'解释掉':>7} {'delta std':>10}")
    for py, px, name in ((0, 0, "R"), (1, 1, "B")):
        s, dd = src[py::2, px::2], dst[py::2, px::2]
        for offset in (0, 512, 768):
            out, mg = denoise_phase(s, tbl0, tbl3, 256, 1023, float(offset))
            truth = dd[mg:-mg, mg:-mg]
            resid = float((out - truth).std())
            dstd = float((dd - s)[mg:-mg, mg:-mg].std())
            print(f"{name:>4} {offset:>7} {resid:>9.3f} {1 - (resid / dstd) ** 2:>6.1%} "
                  f"{dstd:>10.3f}")


if __name__ == "__main__":
    if "--score" in sys.argv:
        _score()
    else:
        _selfcheck()
