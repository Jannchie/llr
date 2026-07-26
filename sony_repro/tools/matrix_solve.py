r"""用引擎自己的输入输出反解 LinearMatrix16 的 16 个节点矩阵。

`matrix_check.py` 定位到:R-G 对得上(比值 1.014),B-G 差 45%(比值 0.682) ——
只有蓝色行错。这里把真值解出来,好知道错在系数摆法还是色相索引。

**按色相分桶逐桶解 3x3 是行不通的**:一个色相段里 (R-G, B-G) 本来就近乎共线,
两个系数分不开,解出来是病态的(试过,末几段会给出 -0.55/+0.55 这种互相抵消的值)。
正确做法是全局解:色相索引已知,插值权重已知,于是输出对 16 个节点的系数是**线性**的,
一次最小二乘解出全部 96 个未知数。

用法: python matrix_solve.py <ARW> <matrix_frames.npz> [style]
"""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "apps/worker/src"))
from llr_worker.sony.linear_matrix import (  # noqa: E402
    KNOT_STEP, N_KNOT, OFFDIAG, hue_index, matrices_from_coeff,
)
from llr_worker.sony.sr2 import look_calibrations, unpack_param_block  # noqa: E402
from llr_worker.sony.tone import LOOK_ORDER  # noqa: E402

WHITE = 8192


def design(idx, u, v):
    """每个像素只压在相邻两个节点上,权重就是线性插值的那两个。"""
    b = idx // KNOT_STEP
    t = (idx - b * KNOT_STEP) / KNOT_STEP
    n = len(idx)
    a = np.zeros((n, N_KNOT * 2), np.float64)
    rows = np.arange(n)
    for knot, w in ((b, 1.0 - t), ((b + 1) % N_KNOT, t)):
        np.add.at(a, (rows, knot * 2), w * u)
        np.add.at(a, (rows, knot * 2 + 1), w * v)
    return a


def main():
    arw = Path(sys.argv[1])
    z = np.load(sys.argv[2])
    style = sys.argv[3] if len(sys.argv) > 3 else "VV2"
    coeff = unpack_param_block(look_calibrations(arw)[LOOK_ORDER.index(style)].param_block)
    ours = matrices_from_coeff(coeff)

    src = z["ZcTaskSIMDLinearMatrix16_in"].astype(np.float64)
    mid = z["ZcTaskSIMDLinearMatrix16_out"].astype(np.float64)
    ok = (src.max(-1) > 200) & (mid.max(-1) > 0)
    x, y = src[ok] / WHITE, mid[ok] / WHITE
    idx = hue_index(x.astype(np.float32))
    u, v = x[:, 0] - x[:, 1], x[:, 2] - x[:, 1]
    keep = np.hypot(u, v) > 0.004                 # 近中性像素对系数没有约束力
    x, y, idx, u, v = x[keep], y[keep], idx[keep], u[keep], v[keep]
    print("参与拟合 %d 像素,色相索引覆盖 %d..%d" % (len(x), idx.min(), idx.max()))

    a = design(idx, u, v)
    got = np.zeros((N_KNOT, 3, 3))
    for i in range(3):
        c, *_ = np.linalg.lstsq(a, y[:, i] - x[:, 1], rcond=None)
        c = c.reshape(N_KNOT, 2)
        got[:, i, 0], got[:, i, 2] = c[:, 0], c[:, 1]
        got[:, i, 1] = 1 - c[:, 0] - c[:, 1]
        pred = a @ np.column_stack([got[:, i, 0], got[:, i, 2]]).ravel() + x[:, 1]
        print("  第 %d 行残差 中位 %.5f  p99 %.5f" % (i, np.median(np.abs(pred - y[:, i])),
                                                 np.percentile(np.abs(pred - y[:, i]), 99)))

    names = ["m%d%d" % ij for ij in OFFDIAG]
    print("\n解出来的 6 行(非对角项,注意 ARW 里存的是取负后的值):")
    for k, (i, j) in enumerate(OFFDIAG):
        print("  %-4s 引擎 %s" % (names[k], np.array2string(got[:, i, j], precision=3,
                                                          suppress_small=True, max_line_width=200)))
        print("  %-4s 我们 %s" % ("", np.array2string(ours[:, i, j], precision=3,
                                                    suppress_small=True, max_line_width=200)))

    # 解出来的 96 个数,逐个到解包出的 96 个数里找最近的 —— 排布错位一眼可见
    truth = np.stack([-got[:, i, j] for i, j in OFFDIAG]).ravel()   # ARW 里存的是取负的
    flat = np.array([coeff[r][k] for r in range(6) for k in range(N_KNOT)])
    print("\n解出的 96 个系数,在解包结果里的位置(理想应是 0,1,2,...,95):")
    pos = [int(np.argmin(np.abs(flat - t))) for t in truth]
    print("  " + " ".join("%3d" % p for p in pos[:32]) + " ...")
    print("  落在原位的比例 %.1f%%   最近邻平均差 %.4f"
          % (np.mean([p == i for i, p in enumerate(pos)]) * 100,
             np.mean([abs(flat[p] - t) for p, t in zip(pos, truth, strict=True)])))

    # 样本分布极不均匀,稀疏节点解不出来。只拿密集的比,并试环形位移
    dense = np.array([((idx // KNOT_STEP) == k).sum() for k in range(N_KNOT)])
    good = dense > 10000
    print("\n样本密集的节点 %s (各 %s)" % (np.where(good)[0].tolist(), dense[good].tolist()))
    print("环形位移试探(只用密集节点,平均 |差|):")
    for s in range(N_KNOT):
        e = np.mean([np.abs(got[good, i, j] - np.roll(ours[:, i, j], s)[good]).mean()
                     for i, j in OFFDIAG])
        print("  位移 %2d  %.4f%s" % (s, e, "   <- 当前" if s == 0 else ""))

    print("\n换几种排布试试(平均 |差|,越小越对):")
    d = np.frombuffer(look_calibrations(arw)[LOOK_ORDER.index(style)].param_block,
                      dtype="<u4", count=69)[6:54].astype(np.uint32)
    raw = np.empty(96, np.int32)
    from llr_worker.sony.sr2 import _decode14
    for lbl, hi_first in (("高 14 位在前", True), ("低 14 位在前", False)):
        a_, b_ = (0x12, 2) if hi_first else (2, 0x12)
        raw[0::2] = _decode14(d >> np.uint32(a_))
        raw[1::2] = _decode14(d >> np.uint32(b_))
        for shape, name in (((6, N_KNOT), "reshape(6,16)"), ((N_KNOT, 6), "reshape(16,6).T")):
            c = (raw.astype(np.float64) / 1024.0).reshape(shape)
            c = c if shape[0] == 6 else c.T
            print("  %-12s %-16s 平均 |差| %.4f"
                  % (lbl, name, np.abs(c.ravel() - truth).mean()))


if __name__ == "__main__":
    main()
