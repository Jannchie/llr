r"""把每个外观的微调形状导出成 worker 用的 look_tuning.npz。

Highlights / Shadows / Contrast(负方向)对档位**严格线性**,于是只存一条单位形状:
`(档位 ±9 的曲线 - 基线) / ±9`,worker 侧 `base + value * shape` 就能还原任意档位。

**Contrast 的正方向不是这样。** 按 +9 的形状缩放去推 +3,残差 44/16384,而单位
幅度总共才 110 —— 连形状本身都随档位变,不只是幅度。所以正方向逐档实测,存成
(9, 8193) 的一叠绝对增量。

`looksweep/` 是临时产物(跑一次 look_sweep.py 就有),而 npz 是长期资产,所以这里
**并入**已有的 npz 而不是覆盖 —— 否则补测 Contrast 会把 Highlights/Shadows 抹掉。
"""
import os
import sys

import numpy as np

LOOKS = ["ST", "VV", "NT", "PT", "FL", "VV2", "IN", "SH", "BW", "SE"]
LINEAR_FIELDS = ["highlights", "shadows"]
LIMIT = 9
SCALE = 16384.0
SRC = "looksweep"


def _base(look):
    p = os.path.join(SRC, f"{look}_base.npy")
    return np.load(p).astype(np.float64)[:8193] if os.path.exists(p) else None


def _curve(look, field, v):
    p = os.path.join(SRC, f"{look}_{field}{v:+d}.npy")
    return np.load(p).astype(np.float64)[:8193] if os.path.exists(p) else None


def main(out="look_tuning.npz", dtype=np.float32):
    packed, report = {}, []
    if os.path.exists(out):
        with np.load(out) as z:
            packed = {k: z[k] for k in z.files}
        print(f"并入已有的 {out}({len(packed)} 条)")

    for look in LOOKS:
        base = _base(look)
        if base is None:
            print(f"{look}: 缺基线,跳过")
            continue
        for field in LINEAR_FIELDS + ["contrast"]:
            # 线性的:两端各一条单位形状
            for side, v in (("neg", -LIMIT), ("pos", LIMIT)):
                if field == "contrast" and side == "pos":
                    continue
                c = _curve(look, field, v)
                if c is None:
                    continue
                shape = (c - base) / v / SCALE
                packed[f"{look}_{field}_{side}"] = shape.astype(dtype)
                report.append((look, field, side, np.abs(shape).max() * SCALE))
        # Contrast 正方向:逐档的绝对增量
        steps = [_curve(look, "contrast", v) for v in range(1, LIMIT + 1)]
        if all(s is not None for s in steps):
            arr = np.stack([(s - base) / SCALE for s in steps])
            packed[f"{look}_contrast_steps"] = arr.astype(dtype)
            report.append((look, "contrast", "steps", np.abs(arr).max() * SCALE))

    np.savez_compressed(out, **packed)
    print(f"\n{len(packed)} 条 -> {out}  {os.path.getsize(out)/1024:.0f} KB")
    print("\n单位幅度(满刻度 16384 制):")
    for look in LOOKS:
        row = [f"{f[:2]}{s[:3]}={a:6.1f}" for lk, f, s, a in report if lk == look]
        if row:
            print(f"  {look:3s} " + "  ".join(row))
    return packed


def verify(packed):
    """用存下来的形状回推实测曲线,报告最大残差。"""
    worst = {}
    for look in LOOKS:
        base = _base(look)
        if base is None:
            continue
        for field in LINEAR_FIELDS:
            for side, v in (("neg", -LIMIT), ("pos", LIMIT)):
                c, key = _curve(look, field, v), f"{look}_{field}_{side}"
                if c is None or key not in packed:
                    continue
                pred = base + v * packed[key].astype(np.float64) * SCALE
                worst[field] = max(worst.get(field, 0), np.abs(pred - c).max())
        key = f"{look}_contrast_steps"
        if key in packed:
            for i, v in enumerate(range(1, LIMIT + 1)):
                c = _curve(look, "contrast", v)
                if c is None:
                    continue
                pred = base + packed[key][i].astype(np.float64) * SCALE
                worst["contrast+"] = max(worst.get("contrast+", 0), np.abs(pred - c).max())
        key = f"{look}_contrast_neg"
        if key in packed:
            for v in (-3, -6, -9):
                c = _curve(look, "contrast", v)
                if c is None:
                    continue
                pred = base + v * packed[key].astype(np.float64) * SCALE
                worst["contrast-"] = max(worst.get("contrast-", 0), np.abs(pred - c).max())
    print("\n回推残差(含 float32 量化),满刻度 16384:")
    for k, v in sorted(worst.items()):
        print(f"  {k:<10} 最大 {v:.3f}")


if __name__ == "__main__":
    verify(main(*sys.argv[1:2]))
