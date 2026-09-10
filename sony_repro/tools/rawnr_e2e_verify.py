"""从**马赛克**跑到底,逐步定位偏差在哪一环。

核本身已经逐位 99.85%(`rawnr_prod_verify.py`),可整幅图上高光边缘仍有品红/绿
彩边。那彩边只可能出在核**以外**:去交织、analysis、阈值表、边界填充。单核验证
一个都覆盖不到,所以这里按顺序一环一环比:

  1. 去交织 —— 马赛克拆出的四个相位,和引擎喂给核的是不是同一批像素
  2. analysis —— `analysis_rb`/`analysis_green` 算的 detail 与 ref
  3. filt —— 喂引擎自己的 ref,只验滤波(已知 99.85%,作基准线)
  4. 端到端 —— 从马赛克一路算到输出

哪一步开始掉,彩边的根就在哪一步。用 `rawnr_full_probe.py` 抓的四路捕获。
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
    PHASE_MARGIN,
    analysis_green,
    analysis_rb,
    denoise_phase_green,
    denoise_phase_rb,
    filt,
)

HERE = os.path.dirname(os.path.abspath(__file__))
#: 滤波的调用顺序就是 pack_bayer 的相位顺序:R, G1, G2, B。
SLOTS = ["rb0", "g0", "g1", "rb1"]
#: 相位在马赛克里的起点,与上面一一对应。
ORIGIN = [(0, 0), (0, 1), (1, 0), (1, 1)]


def cmp(got: np.ndarray, want: np.ndarray, label: str, trim: int = 8) -> float:
    """两幅同坐标的图比一比。trim 掉边缘 —— 那里引擎与我们的填充规则不同。"""
    g = got[trim:got.shape[0] - trim, trim:got.shape[1] - trim]
    w = want[trim:want.shape[0] - trim, trim:want.shape[1] - trim]
    err = g.astype(np.float64) - w.astype(np.float64)
    var = float(np.var(w))
    expl = 100.0 * (1.0 - float(np.var(err)) / var) if var > 0 else float("nan")
    print(f"    {label:<26} 逐位相同 {100 * float(np.mean(err == 0)):7.3f}%   "
          f"|误差| 中位 {float(np.median(np.abs(err))):8.4g}  "
          f"最大 {float(np.abs(err).max()):9.4g}   解释 {expl:8.4f}%")
    return float(np.mean(err == 0))


def main() -> int:
    name = sys.argv[1] if len(sys.argv) > 1 else "rawnr_full_fl_test.npz"
    z = np.load(os.path.join(HERE, name))
    mos = z["mosaic"].astype(np.float32)
    planes = [mos[y0::2, x0::2] for y0, x0 in ORIGIN]
    print(f"{name}  马赛克 {mos.shape} → 四个相位 {planes[0].shape}\n")

    print("  1. 去交织:马赛克拆出的相位 vs 引擎喂给核的 ref 的相关")
    for i, slot in enumerate(SLOTS):
        ref = z[f"{slot}_ref"]
        best = max(range(4), key=lambda j: abs(np.corrcoef(
            planes[j][8:-8, 8:-8].ravel(), ref[8:-8, 8:-8].ravel())[0, 1]))
        r = float(np.corrcoef(planes[i][8:-8, 8:-8].ravel(),
                              ref[8:-8, 8:-8].ravel())[0, 1])
        ok = "✓" if best == i else f"✗ 更像相位 {best}"
        print(f"    {slot} ↔ 相位{i} {ORIGIN[i]}  相关 {r:+.6f}  {ok}")

    print("\n  2. analysis:我们算的 ref / detail vs 引擎的")
    for i, slot in enumerate(SLOTS):
        if slot.startswith("rb"):
            d, ref = analysis_rb(planes[i], OFFSET_RB)
        else:
            other = planes[2] if i == 1 else planes[1]
            d, ref = analysis_green(planes[i], other, OFFSET_GREEN, int(slot[1]))
        # analysis 每边吃 1 圈,所以我们的 (0,0) 对引擎的 (1,1)。
        eng_ref = z[f"{slot}_ref"][1:1 + ref.shape[0], 1:1 + ref.shape[1]]
        eng_det = z[f"{slot}_detail"][1:1 + d.shape[0], 1:1 + d.shape[1]]
        cmp(ref, eng_ref, f"{slot} ref")
        cmp(d, eng_det, f"{slot} detail")

    print("\n  3. filt:喂引擎自己的 ref/detail,只验滤波(基准线)")
    for i, slot in enumerate(SLOTS):
        green = slot.startswith("g")
        kw = {}
        if green:
            kw = {"other": z[f"{slot}_ref2"], "base_own": BASE_GREEN_OWN,
                  "base_other": BASE_GREEN_OTHER[int(slot[1])]}
        got = filt(z[f"{slot}_detail"], z[f"{slot}_ref"], z[f"{slot}_tbl0"],
                   blend=int(z[f"{slot}_tbl3"][0]), gain=int(z[f"{slot}_gain"][0]),
                   limit=int(z[f"{slot}_limit"][0]),
                   offset=float(z[f"{slot}_offset"][0]), **kw)
        r = FILT_MARGIN
        eng = z[f"{slot}_out"][r:-r, r:-r]
        cmp(got, eng, f"{slot} filt")

    # ⚠️ 这一步**必须**调生产入口(`denoise_phase_*`),不能在这里把它的函数体重抄
    # 一遍。这个工具是"从马赛克到底逐位相同"这句话的出处 —— 笔记和
    # `rawnr_simd.py` 的 docstring 都引它。抄一份的话,验的是副本:谁改了
    # `denoise_phase_green`(比如把重复的 analysis 去掉、或动 `1 - phase` 的推导),
    # 这里照样报 100%,而生产已经跑偏。
    print("\n  4. 端到端:马赛克 → 我们的全链路 vs 引擎输出")
    pad = PHASE_MARGIN
    for i, slot in enumerate(SLOTS):
        p = np.pad(planes[i], pad, mode="reflect")
        kw = {"gain": int(z[f"{slot}_gain"][0]), "limit": int(z[f"{slot}_limit"][0]),
              "blend": int(z[f"{slot}_tbl3"][0])}
        if slot.startswith("g"):
            o = np.pad(planes[2] if i == 1 else planes[1], pad, mode="reflect")
            got = denoise_phase_green(p, o, z[f"{slot}_tbl0"],
                                      phase=int(slot[1]), **kw)
        else:
            got = denoise_phase_rb(p, z[f"{slot}_tbl0"], **kw)
        cmp(got, z[f"{slot}_out"], f"{slot} 端到端")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
