"""穷举开头那四个预加载 ymm 的次序 —— 只有 24 种,可以枚举。

反汇编把 base 成员的累加次序读出了大半(`0x3a1206`..`0x3a12c5`):

    ymm7 + (-2,0) + ymm2 + ymm9 + ymm10 + (2,-1) + (2,1)
         + (-1,-1) + (-1,0) + (-1,1) + (1,-1) + (1,0) + (1,1) + [other 12 个]

`(-2,0)` 在第 2 位是直接从 `[r14+r15-8]` 读出来的,后面八个也是。卡住的只有开头四个
预加载的寄存器 —— 它们对应剩下的 `(0,0)/(0,−2)/(0,2)/(2,0)`,但哪个是哪个不知道。

4! = 24,直接枚举,每种**整幅**计分(不是只看差异点 —— 那样会过拟合,已实测:
`base_order_search.py` 给某个次序打 4/4,整幅却更差)。
"""
import itertools
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import numpy as np

from llr_worker.sony.rawnr_simd import BASE_GREEN_OTHER, BASE_GREEN_OWN, FILT_MARGIN, filt

HERE = os.path.dirname(os.path.abspath(__file__))
#: 从反汇编读定的部分:第 2 位,以及后八个。
FIXED_SECOND = (-2, 0)
TAIL = ((2, -1), (2, 1),
        (-1, -1), (-1, 0), (-1, 1),
        (1, -1), (1, 0), (1, 1))
#: 开头四个预加载 ymm 对应的四个成员,次序未知。
UNKNOWN = ((0, 0), (0, -2), (0, 2), (2, 0))


def score(z, slot, own):
    ref, other = z[f"{slot}_ref"], z[f"{slot}_ref2"]
    det, tbl0 = z[f"{slot}_detail"], z[f"{slot}_tbl0"]
    got = filt(det, ref, tbl0, blend=int(z[f"{slot}_tbl3"][0]),
               gain=int(z[f"{slot}_gain"][0]), limit=int(z[f"{slot}_limit"][0]),
               offset=float(z[f"{slot}_offset"][0]), other=other,
               base_own=own, base_other=BASE_GREEN_OTHER[int(slot[1])])
    h, w = ref.shape
    r, W = FILT_MARGIN, 6
    eng = z[f"{slot}_out"][W:h - W, W:w - W]
    sl = (slice(W - r, h - W - r), slice(W - r, w - W - r))
    return 100.0 * float(np.mean(got[sl] == eng))


def main() -> int:
    names = sys.argv[1:] or ["rawnr_full_fl_test.npz", "rawnr_full_DSC04568.npz"]
    zs = [(n, np.load(os.path.join(HERE, n))) for n in names]
    print("枚举开头四个 ymm 的 24 种次序,整幅计分(绿色两路,多张图取平均)\n")

    rows = []
    for perm in itertools.permutations(UNKNOWN):
        own = (perm[0], FIXED_SECOND, perm[1], perm[2], perm[3], *TAIL)
        vals = [score(z, s, own) for _, z in zs for s in ("g0", "g1")]
        rows.append((float(np.mean(vals)), own, vals))
    rows.sort(reverse=True)

    print("  平均      各路读数                              开头五个")
    for avg, own, vals in rows[:8]:
        cells = " ".join(f"{v:8.4f}" for v in vals)
        head = " ".join(f"({a:+d},{b:+d})" for a, b in own[:5])
        print(f"  {avg:8.4f}  {cells}   {head}")
    best = rows[0]
    print(f"\n  最好的平均 {best[0]:.4f}%")
    print("  生产在用的开头五个:"
          + " ".join(f"({a:+d},{b:+d})" for a, b in BASE_GREEN_OWN[:5]))
    print("  ⚠️ 有并列第一时它们在现有语料上数值不可分,别当成「找到了唯一解」。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
