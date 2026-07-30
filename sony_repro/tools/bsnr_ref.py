r"""`ZcTaskBSNR_Y` 的参考实现 + 自检。算法见 notes/static-bsnr-y.md。

**没有对过引擎。** 查的是从反汇编独立读出的性质(平坦区恒等、t_w 两端的极限、
边界内缩范围),以及一个我不敢先断言的问题:这一级到底能不能去掉孤立脉冲。
`rawnr_ref.py` 同样是这个定位。

两张表来自 `rawnr_tables_<stem>.npz` 的 `tbl1` / `tbl4`(**不是** tbl0/tbl3 ——
表基址是 `calib+0xd0` 而不是 `calib+0x200d0`,见 notes §1)。

用法:
    python bsnr_ref.py                       # 只跑合成自检
    python bsnr_ref.py rawnr_tables_DSC03036.npz
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent

#: 引擎在 `0x14039c960` 两个循环里都只写 [1, h-2] x [1, w-2],再往里 9x9 box 要
#: ±4、3x3 邻居要 ±1,所以核心的 ROI 必须比原图内缩这么多。引擎不做边界钳制,
#: 越界读是调用方的责任。
MARGIN = 5

#: 细节回注的定标。GAIN 满刻度 256(`sar 8`),机身给的值略小于 256,所以这一级
#: 只吃掉约一成细节 —— 与 RawNR 同一对字段 calib[+0xc00ec] / [+0xc00f8],
#: 在 SR2 里是 tag 0x78CC.. / 0x78CF..(apps/worker 的 sony/rawnr.py 已经在读)。
GAIN_UNIT = 256

_NEIGHBOURS = tuple((dy, dx) for dy in (-1, 0, 1) for dx in (-1, 0, 1) if (dy, dx) != (0, 0))


def lowpass(y: np.ndarray) -> np.ndarray:
    """3x3 二项式低通,忠实复现 `0x14039c960` 的第一个循环。

    引擎的累加顺序是 `2*(2*C + L+R+U+D) + UL+UR+DL+DR`,展开就是核

        1 2 1
        2 4 2
        1 2 1

    整数累加之后**乘 float32 的 0.0625 再截断**(`0x1404deab0`),不是整数
    `>> 4`。两者在这里恰好同值(和一定是非负整数),但保留原形以防将来
    改到有符号输入上。
    """
    a = y.astype(np.int32)
    inner = 2 * a[1:-1, 1:-1] + a[1:-1, :-2] + a[1:-1, 2:] + a[:-2, 1:-1] + a[2:, 1:-1]
    total = 2 * inner + a[:-2, :-2] + a[:-2, 2:] + a[2:, :-2] + a[2:, 2:]
    out = np.zeros_like(a)
    out[1:-1, 1:-1] = (total.astype(np.float32) * np.float32(0.0625)).astype(np.int32)
    return out


def highpass(y: np.ndarray, low: np.ndarray) -> np.ndarray:
    """`(int16)(Y - low)`,16 位环绕减法(引擎用的是 `sub r8w`)。

    Y 和 low 都在 [0, 0x3fff] 里,所以差落在 [-0x3fff, 0x3fff],实际不会环绕;
    但读回时是 `movsx`(有符号),所以结果必须当 int16 用而不是 uint16。
    """
    d = (y.astype(np.int32) - low.astype(np.int32)) & 0xFFFF
    return d.astype(np.uint16).view(np.int16).astype(np.int32)


def box_mean_9x9(low: np.ndarray) -> np.ndarray:
    """9x9 box 和除以 81,向零取整(`0x1948b0fd` + `sar 3`,已验 n=80/81/162/6561)。

    积分图做,省掉 81 次移位加。low 非负,所以 `//` 与引擎的截断等价。
    """
    p = np.pad(low.astype(np.int64), 5, mode="constant")
    s = p.cumsum(0).cumsum(1)
    h, w = low.shape
    # s[y0:y0+9, x0:x0+9] 的和,y0 = y-4 → 在 padded 坐标里是 y+1
    y0, x0 = np.arange(h)[:, None] + 1, np.arange(w)[None, :] + 1
    total = (s[y0 + 8, x0 + 8] - s[y0 - 1, x0 + 8]
             - s[y0 + 8, x0 - 1] + s[y0 - 1, x0 - 1])
    return (total // 81).astype(np.int32)


def bsnr_y(y: np.ndarray, thr_table: np.ndarray, weight_table: np.ndarray,
           gain: int, limit: int, strength: float = 0.0) -> np.ndarray:
    """一趟完整的 BSNR_Y。`y` 是 14 位 Y 平面(0..0x3fff)。

    `strength` 是淡出权重,压在**原图**那一侧:0 = 完全滤波,1 = 原图不动
    (notes §2 —— 我第一遍把这个方向读反了)。
    """
    y = y.astype(np.int32)
    low = lowpass(y)
    high = highpass(y, low)

    mean = box_mean_9x9(low)
    t_w = weight_table[np.clip(mean, 0, weight_table.size - 1)].astype(np.int32)
    ref = (low * t_w + (1024 - t_w) * mean) >> 10
    thr = thr_table[np.clip(low, 0, thr_table.size - 1)].astype(np.int32)

    total = low.copy()
    count = np.ones_like(low)
    for dy, dx in _NEIGHBOURS:
        n = np.roll(np.roll(low, -dy, axis=0), -dx, axis=1)
        ok = np.abs(n - ref) < thr
        total += np.where(ok, n, 0)
        count += ok
    avg = total // count  # idiv 向零取整; total >= 0 所以与 // 等价

    # sar 8 对负数是向下取整,不是向零 —— 这里必须用算术右移而不是整除。
    d = (high * gain) >> 8
    out = avg + np.clip(d, -limit, limit)
    out = np.clip(out, 0, 0x3FFF)

    if strength != 0.0:
        s = np.float32(strength)
        out = (y.astype(np.float32) * s + out.astype(np.float32) * (np.float32(1.0) - s)).astype(np.int32)

    # 引擎只写内缩后的 ROI,外圈保持调用方给的内容。这里显式标出来,避免
    # 拿边界上的垃圾去比对。
    valid = np.zeros(y.shape, dtype=bool)
    valid[MARGIN:-MARGIN, MARGIN:-MARGIN] = True
    return np.where(valid, out, y)


# ── 自检 ────────────────────────────────────────────────────────────────────


def _tables(path: Path | None):
    """真实抓取优先;没有就用 notes 里 DSC03036 的形状合成一份。"""
    if path is not None and path.exists():
        z = np.load(path, allow_pickle=True)
        return z["tbl1"].astype(np.int32), z["tbl4"].astype(np.int32), f"{path.name} tbl1/tbl4"
    # DSC03036: 13 → 61, 拐点 idx 52 / 2521, 之后持平
    i = np.arange(0x8000)
    thr = np.clip(((np.clip(i, 52, 2521) - 52) * 61 // (2521 - 52)) + 13, 0, 0x3FFF)
    return thr.astype(np.int32), np.full(0x8000, 512, np.int32), "合成(DSC03036 形状)"


def _report(name: str, ok: bool, detail: str = "") -> bool:
    print(f"  [{'ok' if ok else '!!'}] {name}" + (f"  {detail}" if detail else ""))
    return ok


def main() -> int:
    arg = Path(sys.argv[1]) if len(sys.argv) > 1 else HERE / "rawnr_tables_DSC03036.npz"
    thr_t, w_t, src = _tables(arg)
    print(f"表来源: {src}")
    print(f"  阈值 tbl1: {thr_t.min()} .. {thr_t.max()}    权重 tbl4: "
          f"{'常量 ' + str(w_t[0]) if np.unique(w_t).size == 1 else 'non-const'}")

    gain, limit = 250, 512  # 机身给的 GAIN 略小于 256
    rng = np.random.default_rng(0)
    good = True

    # 1. 平坦区必须恒等: 低通不变、高通为 0、所有邻居都过阈值。
    flat = np.full((64, 64), 1000, np.int32)
    out = bsnr_y(flat, thr_t, w_t, gain, limit)
    good &= _report("平坦区恒等", np.array_equal(out, flat),
                    f"max|Δ| = {np.abs(out - flat).max()}")

    # 2. t_w 的两端: 1024 → ref 就是低通像素本身; 0 → ref 是 9x9 均值。
    ramp = np.tile(np.linspace(200, 3000, 64).astype(np.int32), (64, 1))
    noisy = np.clip(ramp + rng.normal(0, 40, ramp.shape), 0, 0x3FFF).astype(np.int32)
    o_c = bsnr_y(noisy, thr_t, np.full_like(w_t, 1024), gain, limit)
    o_m = bsnr_y(noisy, thr_t, np.full_like(w_t, 0), gain, limit)
    core = (slice(MARGIN, -MARGIN), slice(MARGIN, -MARGIN))
    good &= _report("t_w 两端给出不同结果", not np.array_equal(o_c, o_m),
                    f"以中心为参考 std={o_c[core].std():.1f}, 以均值为参考 std={o_m[core].std():.1f}")

    # 3. 强度混合的方向: strength=1 必须完全等于原图。
    o1 = bsnr_y(noisy, thr_t, w_t, gain, limit, strength=1.0)
    good &= _report("strength=1 → 原图", np.array_equal(o1, noisy),
                    f"max|Δ| = {np.abs(o1 - noisy).max()}")

    # 4. 降噪量: 平坦噪声场上残留多少。
    base = np.full((96, 96), 1200, np.int32)
    n = np.clip(base + rng.normal(0, 30, base.shape), 0, 0x3FFF).astype(np.int32)
    o = bsnr_y(n, thr_t, w_t, gain, limit)
    before, after = n[core].std(), o[core].std()
    good &= _report("平坦噪声场有被降", after < before,
                    f"std {before:.1f} → {after:.1f}  (×{after / before:.2f})")

    # 5. 孤立脉冲 —— 这条是量的,不是断言的。低通已经把脉冲摊到 3x3 并衰减到
    #    1/4,而高通回注又会把它加回来(GAIN 接近 256),所以这一级到底能不能
    #    去掉脉冲,我事先不知道。
    imp = np.full((64, 64), 1200, np.int32)
    imp[32, 32] = 0x3FFF
    o = bsnr_y(imp, thr_t, w_t, gain, limit)
    print(f"  [--] 孤立脉冲: 输入 {imp[32, 32]} (背景 1200) → 输出 {o[32, 32]}"
          f", 残留幅度 {(o[32, 32] - 1200) / (0x3FFF - 1200) * 100:.1f}%")
    ring = [o[32 + dy, 32 + dx] for dy, dx in _NEIGHBOURS]
    print(f"       周边 8 点: {ring}  (背景 1200)")

    # 6. 边缘保持: 阶跃两侧的电平不应被拉平。
    step = np.full((64, 64), 500, np.int32)
    step[:, 32:] = 3000
    o = bsnr_y(step, thr_t, w_t, gain, limit)
    lo_err = np.abs(o[MARGIN:-MARGIN, MARGIN:28] - 500).max()
    hi_err = np.abs(o[MARGIN:-MARGIN, 36:-MARGIN] - 3000).max()
    good &= _report("阶跃两侧电平保持", lo_err <= 1 and hi_err <= 1,
                    f"暗侧 max|Δ|={lo_err}, 亮侧 max|Δ|={hi_err}")

    print("\n" + ("全部通过" if good else "有未通过项"))
    return 0 if good else 1


if __name__ == "__main__":
    raise SystemExit(main())
