"""喂给降噪的**输入**是不是同一批数字。

到目前为止所有"99.85% 逐位相同"的说法,前提都是**拿引擎自己的输入去算**:抓它的
mosaic / ref / detail,再用我们的算子跑一遍。那证明的是「同样的输入进去,同样的
结果出来」—— 不等于 llr 跑起来也一样,因为 llr 的输入来自 rawpy/LibRaw,不是
引擎。上游只要差一点(坏点校正、暗电流、读的数据区不同),降噪再准也是在算另一
张图。

而且引擎捕获到的 mosaic 是 1114x682,这张图全分辨率是 7008x4672 —— 之前一直是
在**预览尺寸**的调用上验证的,那本身就该说清楚。

这里查三件事:
  1. 引擎的 mosaic 与 rawpy 的 raw_image,值域、黑电平、直方图对不对得上
  2. 引擎的 mosaic 是不是 raw 的某种降采样(找出因子)
  3. 若能对齐,逐像素比一比
"""
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import numpy as np
import rawpy

sys.path.insert(0, "/home/jannchie/llr/sony_repro/tools")

from aniso_stage import find_arw  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))


def describe(a: np.ndarray, label: str) -> None:
    a = a.astype(np.float64)
    print(f"    {label:<28} 形状 {str(a.shape):<14} "
          f"min={a.min():6.0f} max={a.max():6.0f} 均值={a.mean():8.2f} "
          f"中位={np.median(a):6.0f}")


def main() -> int:
    stem = sys.argv[1] if len(sys.argv) > 1 else "DSC03036"
    cap = sys.argv[2] if len(sys.argv) > 2 else f"rawnr_full_{stem}.npz"
    z = np.load(os.path.join(HERE, cap))
    mos = z["mosaic"]
    rect = z["rect"].tolist() if "rect" in z.files else None
    arw = find_arw(stem)
    if arw is None:
        print(f"找不到 {stem}.ARW")
        return 1

    with rawpy.imread(str(arw)) as raw:
        rawimg = raw.raw_image_visible.copy()
        full = raw.raw_image.copy()
        black = np.array(raw.black_level_per_channel, np.float64)
        white = float(raw.white_level)
        pattern = raw.raw_pattern.tolist()

    print(f"{stem}  引擎 rect={rect}")
    print(f"  rawpy: 黑电平 {black.tolist()}  白电平 {white}  "
          f"raw_pattern {pattern}\n")
    print("  1. 值域与直方图")
    describe(mos, "引擎的 mosaic")
    describe(rawimg, "rawpy raw_image_visible")
    describe(full, "rawpy raw_image(含边)")

    print("\n  2. 尺寸关系")
    for label, a in (("visible", rawimg), ("含边", full)):
        fy, fx = a.shape[0] / mos.shape[0], a.shape[1] / mos.shape[1]
        print(f"    {label:<8} {a.shape} / {mos.shape} = "
              f"({fy:.4f}, {fx:.4f})")
    # 整数因子的降采样最容易验:直接抽样比一比。
    print("\n  3. 若是整数抽样,哪个因子对得上(比中位数与相关)")
    best = None
    for k in (2, 3, 4, 5, 6, 7, 8):
        h, w = mos.shape
        if rawimg.shape[0] < h * k or rawimg.shape[1] < w * k:
            continue
        for oy in range(min(k, 2)):
            for ox in range(min(k, 2)):
                sub = rawimg[oy::k, ox::k][:h, :w]
                if sub.shape != mos.shape:
                    continue
                a = sub[8:-8, 8:-8].astype(np.float64).ravel()
                b = mos[8:-8, 8:-8].astype(np.float64).ravel()
                r = float(np.corrcoef(a, b)[0, 1])
                if best is None or r > best[0]:
                    best = (r, k, oy, ox)
                if r > 0.5:
                    print(f"    k={k} 偏移=({oy},{ox})  相关 {r:+.6f}  "
                          f"逐位相同 {100 * float(np.mean(sub == mos)):.3f}%")
    if best:
        r, k, oy, ox = best
        print(f"    最好的:k={k} 偏移=({oy},{ox}) 相关 {r:+.6f}")
        if r < 0.5:
            print("    ⚠️ 没有整数抽样对得上 —— 引擎的预览不是简单抽样,"
                  "可能是 binning 或另一条解码路径。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
