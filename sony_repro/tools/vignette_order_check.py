"""降噪之前,引擎有没有先做暗角校正 —— 这决定喂进去的是不是同一张图。

ARW 的 SubIFD 里写着 `VignettingCorrection: Auto` 和 17 个参数,Edit 会应用它。
问题是**在降噪之前还是之后**:暗角校正把边缘整体提亮(连噪声一起放大),而降噪的
阈值是按信号电平查表的 —— 顺序反了,边缘用的就是错的阈值档,输入也就不是同一
张图。llr 这边 rawpy 的 `raw_image` 是未校正的原始数据。

判据是**径向亮度分布**:把画面按到中心的归一化距离分环,看每一环的中位电平。
引擎那份 mosaic 虽然是降采样的(682x1114),但降采样保均值,径向形状不受影响,
可以和 rawpy 的全分辨率直接比形状。

  * 两条曲线形状一致  -> 降噪拿到的是**未校正**的原始值,llr 的顺序没问题
  * 引擎的边缘明显更高 -> 它在降噪前就把暗角补上了,llr 必须跟着改顺序
"""
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import numpy as np
import rawpy

sys.path.insert(0, "/home/jannchie/llr/sony_repro/tools")

from aniso_stage import find_arw  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
NRING = 10


def radial(plane: np.ndarray, black: float) -> np.ndarray:
    """按归一化半径分环,取每环的中位电平(减黑电平)。中位数抗高光干扰。"""
    h, w = plane.shape
    yy = (np.arange(h)[:, None] - h / 2) / (h / 2)
    xx = (np.arange(w)[None, :] - w / 2) / (w / 2)
    r = np.sqrt(yy ** 2 + xx ** 2) / np.sqrt(2.0)
    v = plane.astype(np.float64) - black
    out = []
    for i in range(NRING):
        m = (r >= i / NRING) & (r < (i + 1) / NRING)
        out.append(float(np.median(v[m])) if m.any() else np.nan)
    return np.array(out)


def main() -> int:
    stem = sys.argv[1] if len(sys.argv) > 1 else "DSC03036"
    cap = sys.argv[2] if len(sys.argv) > 2 else f"rawnr_full_{stem}.npz"
    z = np.load(os.path.join(HERE, cap))
    mos = z["mosaic"]
    arw = find_arw(stem)
    with rawpy.imread(str(arw)) as raw:
        rawimg = raw.raw_image_visible.copy()
        black = float(np.mean(raw.black_level_per_channel))

    # 只取一个相位,免得 Bayer 的通道差异混进径向曲线里。
    eng = radial(mos[0::2, 0::2], black)
    ref = radial(rawimg[0::2, 0::2], black)
    # 归一化到中心环,比的是**形状**不是绝对电平(降采样会改绝对值)。
    eng_n, ref_n = eng / eng[0], ref / ref[0]

    print(f"{stem}  黑电平 {black:.0f}   引擎 mosaic {mos.shape}   "
          f"rawpy {rawimg.shape}\n")
    print("  归一化半径   引擎(相对中心)   rawpy(相对中心)   之比")
    for i in range(NRING):
        lo, hi = i / NRING, (i + 1) / NRING
        rr = eng_n[i] / ref_n[i] if ref_n[i] else np.nan
        print(f"    [{lo:.1f},{hi:.1f})      {eng_n[i]:8.4f}        "
              f"{ref_n[i]:8.4f}      {rr:7.4f}")

    # 边缘那几环的比值就是判据。暗角校正在边缘能到 1.3~2x,不会是 1.00。
    edge = np.nanmean((eng_n / ref_n)[-3:])
    print(f"\n  最外三环的平均比值 {edge:.4f}")
    if edge > 1.08:
        print("  => 引擎在降噪**之前**已经补过暗角。llr 若在降噪后才补,"
              "\n     边缘喂给降噪的电平就偏低,阈值档也就选错了。")
    elif edge < 0.93:
        print("  => 引擎那份反而更暗,说明它做的是别的事,得单独查。")
    else:
        print("  => 形状一致,降噪拿到的是**未校正**的原始值 —— "
              "这一环 llr 的顺序没问题。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
