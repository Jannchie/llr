r"""把每个外观的微调单位形状导出成 worker 用的 look_tuning.npz。

单位形状 = (档位 ±9 的曲线 - 基线) / ±9,归一化到满刻度 16384。
worker 侧只要 `base + value * shape` 就能还原任意档位。
"""
import os

import numpy as np

LOOKS = ["ST", "VV", "NT", "PT", "FL", "VV2", "IN", "SH", "BW", "SE"]
FIELDS = ["highlights", "shadows"]
LIMIT = 9
SCALE = 16384.0
SRC = "looksweep"


def main(out="look_tuning.npz", dtype=np.float32):
    packed, report = {}, []
    for look in LOOKS:
        bp = os.path.join(SRC, f"{look}_base.npy")
        if not os.path.exists(bp):
            print(f"{look}: 缺基线")
            continue
        base = np.load(bp).astype(np.float64)[:8193]
        for field in FIELDS:
            for side, v in (("neg", -LIMIT), ("pos", LIMIT)):
                p = os.path.join(SRC, f"{look}_{field}{v:+d}.npy")
                if not os.path.exists(p):
                    print(f"{look} {field}{v:+d}: 缺")
                    continue
                shape = (np.load(p).astype(np.float64)[:8193] - base) / v / SCALE
                packed[f"{look}_{field}_{side}"] = shape.astype(dtype)
                report.append((look, field, side, np.abs(shape).max() * SCALE))
    np.savez_compressed(out, **packed)
    size = os.path.getsize(out)
    print(f"\n{len(packed)} 条形状 -> {out}  {size/1024:.0f} KB")

    print("\n单位幅度(满刻度 16384 制):")
    for look in LOOKS:
        row = [f"{f[:1]}{s[:1]}={a:6.1f}" for lk, f, s, a in report if lk == look]
        if row:
            print(f"  {look:3s} " + "  ".join(row))
    return packed


def verify(packed):
    """用形状回推各外观的实测曲线,报告最大残差。"""
    worst = 0.0
    for look in LOOKS:
        bp = os.path.join(SRC, f"{look}_base.npy")
        if not os.path.exists(bp):
            continue
        base = np.load(bp).astype(np.float64)[:8193]
        for field in FIELDS:
            for side, v in (("neg", -LIMIT), ("pos", LIMIT)):
                p = os.path.join(SRC, f"{look}_{field}{v:+d}.npy")
                key = f"{look}_{field}_{side}"
                if not os.path.exists(p) or key not in packed:
                    continue
                pred = base + v * packed[key].astype(np.float64) * SCALE
                e = np.abs(pred - np.load(p).astype(np.float64)[:8193]).max()
                worst = max(worst, e)
    print(f"\n形状回推残差(含 float32 量化) 最大 {worst:.3f}/16384")


if __name__ == "__main__":
    verify(main())
