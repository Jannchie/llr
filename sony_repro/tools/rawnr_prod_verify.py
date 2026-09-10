"""拿**生产代码**的 filt 去重算引擎捕获 —— 红蓝与绿色都要。

`green_base_verify.py` 验的是研究脚本里的一份实现;这里验的是
`llr_worker.sony.rawnr_simd.filt` 本身,包括绿色核接进去之后红蓝有没有被带坏
(那次改动把 `m9 * (1/9)` 换成了 `members / n`,浮点上并不等价)。

捕获里已经有 analysis 的输出 `ref`(以及绿色的 `ref2`)和 `detail`,所以这里直接
喂 filt,不重跑 analysis —— 要单独验的就是滤波这一段。
"""
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import numpy as np

from llr_worker.sony.rawnr_simd import (
    BASE_GREEN_OTHER,
    BASE_GREEN_OWN,
    FILT_MARGIN,
    OFFSET_GREEN,
    OFFSET_RB,
    filt,
)

HERE = os.path.dirname(os.path.abspath(__file__))


def check(name: str) -> None:
    z = np.load(os.path.join(HERE, name))
    ref, out, det = z["ref"], z["out"], z["detail"]
    tbl0, tbl3 = z["tbl0"], z["tbl3"]
    green = "ref2" in z.files
    which = int(z["which"][0]) if "which" in z.files else 0
    kw = {}
    if green:
        kw = {"other": z["ref2"], "base_own": BASE_GREEN_OWN,
              "base_other": BASE_GREEN_OTHER[which]}
    got = filt(det, ref, tbl0, blend=int(tbl3[0]),
               gain=int(z["gain"][0]), limit=int(z["limit"][0]),
               offset=float(z["offset"][0]),
               **kw)

    h, w = ref.shape
    r, W = FILT_MARGIN, 6
    eng = out[W:h - W, W:w - W]
    got = got[W - r:h - W - r, W - r:w - W - r]
    err = got - eng
    var = float(np.var(eng))
    expl = 100.0 * (1.0 - float(np.var(err)) / var) if var > 0 else float("nan")
    kind = f"绿色 phase={which}" if green else "红蓝"
    off = float(z["offset"][0])
    assert off == (OFFSET_GREEN if green else OFFSET_RB), "offset 与通道对不上"
    print(f"  {name}\n    {kind}:逐位相同 {100 * float(np.mean(err == 0)):7.4f}%   "
          f"|误差| 中位 {float(np.median(np.abs(err))):.4g}  "
          f"最大 {float(np.abs(err).max()):.4g}   解释 {expl:7.3f}%")


def main() -> int:
    names = sys.argv[1:] or ["rawnr_kern_fl_test_rb_s0w0.npz",
                             "rawnr_kern_fl_test_g_s0w0.npz"]
    print("生产代码 llr_worker.sony.rawnr_simd.filt 对引擎捕获:")
    for n in names:
        check(n)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
