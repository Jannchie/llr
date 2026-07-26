r"""那 96 个系数是怎么算出来的?

已知:引擎解包的 276 字节里,只有字节 24..215(48 个 dword 的系数区)与十种外观的
tag `0x780f` 不同,头部和尾部完全相同。所以系数是加载时算出来的。

先确认解包器本身:拿引擎真读的字节走一遍 `unpack_param_block`,应当与引擎参数区
`+0x28` 的 6x16 逐位相同 —— 对上了,才说明剩下的问题纯粹是「这些字节从哪来」。
然后检验最自然的猜想:是不是十种外观的线性组合。

用法: python src276_solve.py <ARW> <src276.npz> <engine_matrix_table.npz>
"""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sony_repro.sr2 import data_ifds, unpack_param_block  # noqa: E402

LOOK_ORDER = ("ST", "VV", "NT", "PT", "FL", "VV2", "IN", "SH", "BW", "SE")


def main():
    arw = Path(sys.argv[1])
    raw = bytes(np.load(sys.argv[2])["block_0"])
    ours = np.asarray(unpack_param_block(raw), np.float64)

    tab = np.load(sys.argv[3])
    if "coeff" in tab:
        eng = tab["coeff"].astype(np.float64)
        d = np.abs(ours - eng)
        print("用引擎真读的字节走我们的解包器,对引擎参数区 +0x28:")
        print("  最大差 %.6f   逐位相同 %.2f%%" % (d.max(), (d < 1e-6).mean() * 100))
        if d.max() > 1e-6:
            print("  我们 %s" % np.array2string(ours[0], precision=4, max_line_width=200))
            print("  引擎 %s" % np.array2string(eng[0], precision=4, max_line_width=200))

    # 是不是十种外观的线性组合?
    looks = np.stack([np.asarray(unpack_param_block(
        bytes(data_ifds(arw)[i][0x780F])), np.float64).ravel() for i in range(10)])
    y = ours.ravel()
    w, *_ = np.linalg.lstsq(looks.T, y, rcond=None)
    resid = np.abs(looks.T @ w - y)
    print("\n当作十种外观的线性组合来解:")
    print("  权重 %s" % np.array2string(w, precision=4, max_line_width=200))
    print("  和 %.4f   残差 中位 %.5f  最大 %.5f" % (w.sum(), np.median(resid), resid.max()))
    print("  (若真是插值,权重应当稀疏且和为 1;残差应当接近 0)")

    print("\n单个外观的距离:")
    for i, name in enumerate(LOOK_ORDER):
        print("  %-3s 平均 |差| %.5f" % (name, np.abs(looks[i] - y).mean()))


if __name__ == "__main__":
    main()
