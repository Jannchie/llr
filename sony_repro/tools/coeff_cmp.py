r"""引擎参数区自己的 6x16 系数 vs 我们从 ARW 解出来的 —— 位流解读错在哪。

`matrix_table.py` 顺手把 kernel 参数区 `+0x28` 的 96 个 float 抓了下来,那是引擎
自己用的系数,不经任何解码。和 `unpack_param_block(0x780f)` 一比就知道 `sr2.py`
的位域拆法哪里不对。

用法: python coeff_cmp.py <ARW> <engine_matrix_table.npz> [style]
"""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "apps/worker/src"))
from llr_worker.sony.sr2 import look_calibrations, unpack_param_block  # noqa: E402
from llr_worker.sony.tone import LOOK_ORDER  # noqa: E402


def main():
    arw = Path(sys.argv[1])
    z = np.load(sys.argv[2])
    if "coeff" not in z:
        print("这份 npz 里没有 coeff,请用新版 matrix_table.py 重抓")
        return
    eng = z["coeff"].astype(np.float64)          # 引擎参数区里的原样,未取负
    style = sys.argv[3] if len(sys.argv) > 3 else "VV2"

    print("引擎系数(参数区 +0x28,6x16):")
    for r in range(6):
        print("  行%d %s" % (r, np.array2string(eng[r], precision=4, max_line_width=200)))

    print("\n各外观从 ARW 解出来的,与引擎比(平均 |差|):")
    for name in LOOK_ORDER:
        c = unpack_param_block(look_calibrations(arw)[LOOK_ORDER.index(name)].param_block)
        c = np.asarray(c, np.float64)
        best = min((np.abs(np.roll(c, s, 1) - eng).mean(), s) for s in range(16))
        print("  %-3s 原位 %.5f   最好按节点位移 %+3d 时 %.5f   取负后 %.5f"
              % (name, np.abs(c - eng).mean(), best[1] if best[1] < 8 else best[1] - 16,
                 best[0], np.abs(-c - eng).mean()))

    c = np.asarray(unpack_param_block(
        look_calibrations(arw)[LOOK_ORDER.index(style)].param_block), np.float64)
    print("\n%s 逐行对照(上引擎 / 下我们):" % style)
    for r in range(6):
        print("  行%d 引擎 %s" % (r, np.array2string(eng[r], precision=4, max_line_width=200)))
        print("      我们 %s" % np.array2string(c[r], precision=4, max_line_width=200))

    # 96 个数各自到对方数组里找最近的,看是不是整体被打乱了
    flat_e, flat_o = eng.ravel(), c.ravel()
    pos = [int(np.argmin(np.abs(flat_o - t))) for t in flat_e]
    print("\n引擎 96 个数在我们数组里的最近位置(理想是 0..95):")
    print("  " + " ".join("%2d" % p for p in pos[:48]))
    print("  落在原位 %.1f%%   最近邻平均差 %.5f"
          % (np.mean([p == i for i, p in enumerate(pos)]) * 100,
             np.mean([abs(flat_o[p] - t) for p, t in zip(pos, flat_e, strict=True)])))


if __name__ == "__main__":
    main()
