r"""引擎展开好的 1024 张分段矩阵 vs 我们展开出来的 —— 一次分清是节点错还是索引错。

表在 kernel 0x37f8e0 的参数3 + 0x228,条目步长 72 字节(asm 是 `rax*8` 而 `rax = idx*9`),
前 9 个 float32 有效、后 9 个是填充。`tools/matrix_table.py` 负责抓。

若两张表逐位相同 -> 节点与展开都没问题,错的只能是逐像素的色相索引;
若不同 -> 错在 ARW 系数的解包或节点矩阵的构造。

用法: python matrix_table_cmp.py <ARW> <engine_matrix_table.npz> [style]
"""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "apps/worker/src"))
from llr_worker.sony.linear_matrix import N_INDEX, SegmentedMatrix  # noqa: E402
from llr_worker.sony.sr2 import look_calibrations, unpack_param_block  # noqa: E402
from llr_worker.sony.tone import LOOK_ORDER  # noqa: E402


def main():
    arw = Path(sys.argv[1])
    eng = np.load(sys.argv[2])["table"].astype(np.float64)
    style = sys.argv[3] if len(sys.argv) > 3 else "VV2"

    print("引擎表 %s   每行和 %.6f ~ %.6f" % (eng.shape, eng.sum(-1).min(), eng.sum(-1).max()))
    for name in LOOK_ORDER:
        cal = look_calibrations(arw)[LOOK_ORDER.index(name)]
        ours = SegmentedMatrix(unpack_param_block(cal.param_block)).table.astype(np.float64)
        best = min(((np.abs(np.roll(ours, s, 0) - eng).mean(), s) for s in range(N_INDEX)))
        print("  %-3s 原位平均 |差| %.6f   最好位移 %+5d 时 %.6f"
              % (name, np.abs(ours - eng).mean(), best[1] if best[1] < N_INDEX // 2
                 else best[1] - N_INDEX, best[0]))

    cal = look_calibrations(arw)[LOOK_ORDER.index(style)]
    ours = SegmentedMatrix(unpack_param_block(cal.param_block)).table.astype(np.float64)
    print("\n%s 逐段抽看(引擎 / 我们):" % style)
    for k in range(0, N_INDEX, 128):
        print("  段 %4d  引擎 %s" % (k, np.array2string(eng[k].ravel(), precision=3,
                                                      suppress_small=True, max_line_width=200)))
        print("          我们 %s" % np.array2string(ours[k].ravel(), precision=3,
                                                   suppress_small=True, max_line_width=200))


if __name__ == "__main__":
    main()
