r"""到底是节点矩阵错了,还是色相索引挑错了段?

`linear_matrix.py` 说展开表曾与引擎实机 dump 逐位相同,那节点和展开都没问题;
可 `matrix_check.py` 又量到 B-G 差 45%。剩下的嫌疑只有色相索引 —— 它是
「真 atan2 + 一张拟合出来的校准 LUT」,本来就是近似。

这里固定我们的 1024 张矩阵,对每个像素在 1024 段里搜出**实际**最贴合引擎输出的那一段,
再和我们算出来的索引对。两者若系统性错开,问题就锁死在索引上。

用法: python hue_index_check.py <ARW> <matrix_frames.npz> [style]
"""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "apps/worker/src"))
from llr_worker.sony.linear_matrix import N_INDEX, SegmentedMatrix, hue_index  # noqa: E402
from llr_worker.sony.sr2 import look_calibrations, unpack_param_block  # noqa: E402
from llr_worker.sony.tone import LOOK_ORDER  # noqa: E402

WHITE = 8192
SAMPLE = 30000


def main():
    arw = Path(sys.argv[1])
    z = np.load(sys.argv[2])
    style = sys.argv[3] if len(sys.argv) > 3 else "VV2"
    coeff = unpack_param_block(look_calibrations(arw)[LOOK_ORDER.index(style)].param_block)
    seg = SegmentedMatrix(coeff)

    src = z["ZcTaskSIMDLinearMatrix16_in"].astype(np.float64)
    mid = z["ZcTaskSIMDLinearMatrix16_out"].astype(np.float64)
    ok = (src.max(-1) > 200) & (mid.max(-1) > 0)
    x, y = src[ok] / WHITE, mid[ok] / WHITE
    u, v = x[:, 0] - x[:, 1], x[:, 2] - x[:, 1]
    keep = np.hypot(u, v) > 0.01
    x, y = x[keep], y[keep]
    rng = np.random.default_rng(0)
    pick = rng.choice(len(x), min(SAMPLE, len(x)), replace=False)
    x, y = x[pick], y[pick]
    print("抽样 %d 像素" % len(x))

    ours = hue_index(x.astype(np.float32))
    # (1024,3,3) x (N,3) -> (1024,N,3),逐段算残差,取最小的那一段
    pred = np.einsum("kij,nj->kni", seg.table.astype(np.float64), x)
    err = np.abs(pred - y[None]).sum(-1)
    best = err.argmin(0)
    resid_best = err[best, np.arange(len(x))]
    resid_ours = err[ours, np.arange(len(x))]

    d = (best.astype(int) - ours.astype(int) + N_INDEX // 2) % N_INDEX - N_INDEX // 2
    print("\n实际用的段 - 我们算的段:")
    print("  中位 %+d   平均 %+.1f   p10 %+d   p90 %+d   |差|<8 的比例 %.1f%%"
          % (np.median(d), d.mean(), np.percentile(d, 10), np.percentile(d, 90),
             (np.abs(d) < 8).mean() * 100))
    print("  残差 用最优段 中位 %.5f   用我们的段 中位 %.5f"
          % (np.median(resid_best), np.median(resid_ours)))

    print("\n给索引加一个统一偏移,扫一遍看哪档最好:")
    rows = np.arange(len(x))
    scores = [(float(err[(ours + s) % N_INDEX, rows].mean()), s) for s in range(N_INDEX)]
    scores.sort()
    for e, s in scores[:5]:
        print("  偏移 %+4d  平均残差 %.5f" % (s if s < N_INDEX // 2 else s - N_INDEX, e))
    print("  偏移    0  平均残差 %.5f   <- 当前" % err[ours, rows].mean())

    print("\n按我们的索引分 16 段看偏差:")
    for k in range(16):
        sel = (ours // 64) == k
        if sel.sum() < 200:
            continue
        print("  段 %2d  %6d 像素   偏差中位 %+6d   残差 %.5f -> %.5f"
              % (k, sel.sum(), int(np.median(d[sel])),
                 np.median(resid_ours[sel]), np.median(resid_best[sel])))


if __name__ == "__main__":
    main()
