"""上一步的接线坏在哪 —— 逐项查,别猜。

`rawnr_simd_try.py` 量到细节掉 47%、格纹反而升到 1.52。转录本身有单测钉着,
且对捕获的引擎数据端到端解释了 100%,所以坏的是**接线**。四个嫌疑,逐个证伪:

  1. **相位**:硬写的 RGGB 未必成立。`denoise.py` 是用 `_plane_colors` 从
     `raw_pattern` 加 visible crop 的奇偶算出来的 —— margin 是奇数就会整体
     错位,绿平面被当 R/B 跑,而那正好会同时伤细节和造高频。
  2. **`count == 0` 的退化**:`filt` 在无 tap 通过时给 0。单测记录了它。
     真实照片上若大面积触发,就是一地黑点 —— 细节掉、高频升,两个症状都对。
  3. **阈值量级**:ISO 2000 的阈值有多大,相对于平面的噪声幅度。
  4. **黑电平/尺度**:用 min() 取了个标量,而逐平面黑电平未必相同。
"""
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import numpy as np
import rawpy

sys.path.insert(0, "/home/jannchie/llr/sony_repro/tools")
sys.path.insert(0, "/home/jannchie/llr/apps/worker/src")

from aniso_stage import find_arw  # noqa: E402
from llr_worker.denoise import _plane_black_levels, _plane_colors, pack_bayer  # noqa: E402
from llr_worker.sony.rawnr import ENGINE_FULL_SCALE, noise_model  # noqa: E402
from llr_worker.sony import rawnr_simd as S  # noqa: E402
from rawnr_simd_try import PAD, thresholds_from  # noqa: E402


def main():
    stem = sys.argv[1] if len(sys.argv) > 1 else "DSC03036"
    arw = find_arw(stem)
    with rawpy.imread(str(arw)) as raw:
        sizes = raw.sizes
        rp = int(getattr(sizes, "top_margin", 0) or 0)
        cp = int(getattr(sizes, "left_margin", 0) or 0)
        print("== 1. 相位 ==")
        print(f"  raw_pattern=\n{raw.raw_pattern}")
        print(f"  color_desc={raw.color_desc!r}  top_margin={rp} left_margin={cp}")
        cfa = _plane_colors(raw, rp, cp)
        print(f"  _plane_colors -> {list(cfa)}   我硬写的是 ['R','G','G','B']")
        print(f"  一致吗:{list(cfa) == ['R', 'G', 'G', 'B']}")

        print("\n== 4. 黑电平 ==")
        blk = _plane_black_levels(raw, rp, cp)
        print(f"  逐平面 {list(blk)}   我用的 min() = {float(np.min(raw.black_level_per_channel))}")
        print(f"  white_level={raw.white_level}")

        visible = raw.raw_image_visible
        h, w = visible.shape
        he, we = h - (h % 2), w - (w % 2)
        planes = pack_bayer(np.ascontiguousarray(visible[:he, :we])).astype(np.float32)
        black = float(np.min(raw.black_level_per_channel))
        scale = ENGINE_FULL_SCALE / max(float(raw.white_level) - black, 1.0)
        eng = np.clip((planes - black) * scale, 0, ENGINE_FULL_SCALE)

        model = noise_model(arw)
        thr = thresholds_from(model)
        print(f"\n== 3. 阈值 ==  lo={model.lo} hi={model.hi} base={model.base} slope={model.slope}")
        for lv in (100, 500, 1000, 2000, 4000, 8000):
            print(f"  level {lv:5d} -> thr {int(thr[lv]):4d}")

        # 取中间一块跑,别整幅 —— 诊断要快。
        y0, x0, n = eng.shape[0] // 2, eng.shape[1] // 2, 512
        for k, name in ((0, "R"), (1, "G1"), (3, "B")):
            blk_in = eng[y0:y0 + n + 2 * PAD, x0:x0 + n + 2 * PAD, k]
            if k == 1:
                other = eng[y0:y0 + n + 2 * PAD, x0:x0 + n + 2 * PAD, 2]
                out = S.denoise_phase_green(blk_in, other, thr)
            else:
                out = S.denoise_phase_rb(blk_in, thr)
            src = blk_in[PAD:-PAD, PAD:-PAD]
            zeros = int((out == 0.0).sum())
            print(f"\n== 2. 平面 {name} ==")
            print(f"  输入 均值{src.mean():8.1f} 标准差{src.std():7.2f}"
                  f"  输出 均值{out.mean():8.1f} 标准差{out.std():7.2f}")
            print(f"  输出为 0 的点:{zeros} / {out.size}  ({100*zeros/out.size:.3f}%)")
            d = out - src
            print(f"  改动量 |d| 均值 {np.abs(d).mean():7.2f}  最大 {np.abs(d).max():8.1f}")
            print(f"  改动占信号 {100*np.abs(d).mean()/max(src.mean(),1):.2f}%"
                  f"   (引擎在 ISO100 tile 上测得 0.46%,ISO8000 上 7.97%)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
