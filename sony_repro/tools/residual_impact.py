"""滤波核剩下那 0.15% 的残差,落到画面上有多大 —— 给它一个量级。

八个假设试完仍未解(见 notes 2.19.4)。继续无方向地试之前,先量清楚它值不值得:
把 raw 域的残差换算到显示域,和**已知的可见差距**(llr 与 Edit 的成品相差
|Δ| 均值 0.0486、高光段 −0.113)放在同一把尺子上比。

若残差比可见差距小几个数量级,那它就不是画面不像的原因,优先级也就排在
demosaic 和色调后面 —— 这不是给它开脱,是别把力气用错地方。
"""
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import numpy as np

from llr_worker.sony.rawnr_simd import FILT_MARGIN
from filt_outlier_probe import compute  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
SLOTS = ["rb0", "g0", "g1", "rb1"]
#: 引擎的工作尺度(14 bit)与黑电平。残差要除以「黑电平之上的可用量程」才有意义。
WHITE, BLACK = 16383.0, 512.0
#: llr 与 Edit 成品的实测差距,来自 tools/llr_vs_edit.py —— **已补镜头畸变校正**。
#: 不补的话读数是 0.0486、高光 −0.113,那是四角错开 ±24 像素量出来的假数
#: (notes 2.19.6);校正后高光偏置变成 +0.014,根本没有"高光被压暗"这回事。
VISIBLE_MEAN, VISIBLE_HI = 0.0209, 0.0144


def main() -> int:
    names = sys.argv[1:] or ["rawnr_full_fl_test.npz", "rawnr_full_DSC03036.npz"]
    r, W = FILT_MARGIN, 6
    print("滤波核残差换算到 0..1 的显示尺度(除以 白电平−黑电平):\n")
    all_mean = []
    for name in names:
        z = np.load(os.path.join(HERE, name))
        print(f"  {name}")
        for slot in SLOTS:
            got, *_ = compute(z, slot)
            h, w = z[f"{slot}_ref"].shape
            eng = z[f"{slot}_out"][W:h - W, W:w - W]
            sl = (slice(W - r, h - W - r), slice(W - r, w - W - r))
            e = np.abs(got[sl] - eng) / (WHITE - BLACK)
            all_mean.append(float(e.mean()))
            print(f"    {slot}:  错的点 {100 * float(np.mean(e > 0)):6.3f}%   "
                  f"|Δ| 均值 {float(e.mean()):.3e}   "
                  f"p99.9 {float(np.percentile(e, 99.9)):.3e}   "
                  f"最大 {float(e.max()):.3e}")
        print()

    m = float(np.mean(all_mean))
    print(f"  四路平均的 |Δ| = {m:.3e}")
    print(f"  llr 与 Edit 成品的可见差距 = {VISIBLE_MEAN:.4f}(高光段 {VISIBLE_HI:.3f})")
    print(f"\n  可见差距 / 残差 = {VISIBLE_MEAN / max(m, 1e-12):,.0f} 倍"
          f"(高光段 {VISIBLE_HI / max(m, 1e-12):,.0f} 倍)")
    print("\n  => 残差比可见差距小几个数量级,它不是画面不像的原因。"
          "\n     但也别顺手赖给 demosaic:`demosaic_gap_share.py` 量过,四种算法"
          "\n     之间只差 0.3%,那 0.008 不在这一步。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
