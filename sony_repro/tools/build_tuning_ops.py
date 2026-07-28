r"""把逐外观的微调形状,改建成**与外观无关的输出域算子**。

**为什么换存法。** 原来每个外观各存一套 `(x -> 增量)` 的形状,60 条 599 KB。
实测发现那六套形状其实是同一个算子被各自的基线曲线拉伸出来的:把增量改成以
基线**输出值** y 为自变量重采样,十个外观塌到 0.15/16384(对比度正向 1.8,是
float32 量化),而按 x 直接比差 173。也就是说引擎干的是

    tweaked(x) = U(base(x))          # 微调作用在外观曲线**之后**

于是只需要存一份 U,而不是每个外观一份。跨机身也验过:highlights/shadows 两个
方向加 contrast 负向,7CM2 与 a7 V 差 0.07~0.57/16384 —— 是同一个算子。

**这么改顺带解决 FL2/FL3。** 那两个外观只有 a7 V 才有,7CM2 上是借来的曲线,
原来因为没有逐外观形状,三个滑块静默失效。算子与外观无关之后它们自动就有了,
不需要跨机身搬任何东西。

**contrast 正向是唯一的例外**,它跨机身**不**通用(差 182/16384,每档幅度都不同:
7CM2 的 +1 幅度 89.4,a7 V 是 116.9)。这里存的是 7CM2 实测值;换机身要重测。

用法::

    python build_tuning_ops.py [--write]
"""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

SCR = os.path.dirname(os.path.abspath(__file__))
WORKER = os.path.join(SCR, "..", "..", "apps", "worker")
# 输入是逐外观的原始实测存档,输出是 worker 用的算子 —— 两个不同的文件,
# 这样重跑不会把自己的输入吃掉。
MEASURED = os.path.join(SCR, "look_tuning_perlook_7cm2.npz")
SHIPPED = os.path.join(WORKER, "src", "llr_worker", "sony", "data", "look_tuning.npz")
REF = os.path.join(SCR, "..", "..", "samples", "DSC01157.ARW")   # 7CM2,取基线曲线用
GRID = 1025          # 输出域采样点数,精度由下面的回推残差把关
SCALE = 16384.0
LIMIT = 9            # 机内档位上限;线性字段存的是单位形状,报告时折算回满档
LINEAR = ("highlights_neg", "highlights_pos", "shadows_neg", "shadows_pos", "contrast_neg")


def to_output_domain(shape, base, grid):
    """把 (x -> 增量) 改写成 (基线输出值 y -> 增量);越界处留 NaN。"""
    order = np.argsort(base, kind="stable")
    uniq, first = np.unique(base[order], return_index=True)
    out = np.full(grid.shape, np.nan)
    inside = (grid >= uniq[0]) & (grid <= uniq[-1])
    out[inside] = np.interp(grid[inside], uniq, shape[order][first])
    return out


def _mean_fill(stack):
    """外观间取平均;两端没有任何外观覆盖到的点用最近的有效值补。"""
    avg = np.nanmean(stack, axis=0)
    idx = np.arange(avg.size)
    ok = ~np.isnan(avg)
    return np.interp(idx, idx[ok], avg[ok])


def build():
    sys.path.insert(0, os.path.join(WORKER, "src"))
    from llr_worker.sony.sr2 import look_calibrations
    from llr_worker.sony.tone import LOOK_ORDER, base_curve

    with np.load(MEASURED) as z:
        shapes = {k: z[k].astype(np.float64) for k in z.files}
    cals = look_calibrations(REF)
    grid = np.linspace(0.0, 1.0, GRID)
    bases = {lk: base_curve(cals[i], 8193) for i, lk in enumerate(LOOK_ORDER) if i < len(cals)}

    ops, spread = {}, {}
    for field in LINEAR:
        st = np.stack([to_output_domain(shapes[f"{lk}_{field}"], b, grid)
                       for lk, b in bases.items() if f"{lk}_{field}" in shapes])
        ops[field] = _mean_fill(st)
        spread[field] = np.nanmax(np.nanmax(st, axis=0) - np.nanmin(st, axis=0)) * SCALE

    steps = []
    n = max(shapes[f"{lk}_contrast_steps"].shape[0] for lk in bases
            if f"{lk}_contrast_steps" in shapes)
    for i in range(n):
        st = np.stack([to_output_domain(shapes[f"{lk}_contrast_steps"][i], b, grid)
                       for lk, b in bases.items() if f"{lk}_contrast_steps" in shapes])
        steps.append(_mean_fill(st))
        spread[f"contrast_step{i+1}"] = np.nanmax(np.nanmax(st, axis=0)
                                                  - np.nanmin(st, axis=0)) * SCALE
    ops["contrast_steps"] = np.stack(steps)

    print(f"输出域网格 {GRID} 点。外观间离散度(该算子与外观无关的直接证据,/16384):")
    for k, v in spread.items():
        print(f"  {k:18} {v:6.2f}")
    return ops, bases, shapes, grid


def verify(ops, bases, shapes, grid):
    """用算子回推每个外观的原始形状 —— 这是换存法的验收标准。"""
    # 线性字段存的是**单位**形状,残差要乘满档才是用户实际会看到的误差。
    print(f"\n回推残差(算子 -> 逐外观形状,已折算到满档 ±{LIMIT},/16384):")
    worst = 0.0
    for field in LINEAR:
        e = max(np.abs(np.interp(b, grid, ops[field]) - shapes[f"{lk}_{field}"]).max()
                for lk, b in bases.items() if f"{lk}_{field}" in shapes) * SCALE * LIMIT
        worst = max(worst, e)
        print(f"  {field:18} 最大 {e:6.2f}")
    e = 0.0
    for lk, b in bases.items():
        key = f"{lk}_contrast_steps"
        if key not in shapes:
            continue
        for i in range(ops["contrast_steps"].shape[0]):
            e = max(e, np.abs(np.interp(b, grid, ops["contrast_steps"][i])
                              - shapes[key][i]).max() * SCALE)
    worst = max(worst, e)
    print(f"  {'contrast_steps':18} 最大 {e:6.2f}")
    print(f"\n整体最坏 {worst:.2f}/16384")
    return worst


def main():
    ops, bases, shapes, grid = build()
    worst = verify(ops, bases, shapes, grid)
    if "--write" not in sys.argv:
        print("\n(只看不写。加 --write 才覆盖 look_tuning.npz)")
        return
    if worst > 3.0:
        raise SystemExit(f"回推残差 {worst:.2f}/16384 超过实测线性残差 3 —— 不写")
    np.savez_compressed(SHIPPED, **{k: v.astype(np.float32) for k, v in ops.items()})
    print(f"\n{len(ops)} 条 -> {os.path.abspath(SHIPPED)}"
          f"  {os.path.getsize(SHIPPED)/1024:.0f} KB")


if __name__ == "__main__":
    main()
