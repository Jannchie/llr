"""是不是**坐标系方向**不同 —— 上下翻、左右翻、转置。

僵局是这样的:引擎那份 mosaic 拆相位跑 analysis,与引擎自己的 `ref` 逐位 100%
相同(所以它确实是降噪的输入);单像素噪声与 raw 之比 0.92~0.97(所以没被平均过);
可整幅缩放、跳采样、tile 裁剪、全图互相关**全都定位不到**,两张不同的图都如此。

翻转/转置正好能同时解释这三条:它不动噪声一个字,却会把空间相关打到 0。这是最
常见的一类坐标系差异,偏偏一直没试。

顺带把"先翻转再缩放"也试了 —— 尺寸不同,得缩到一起才能比。
"""
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import numpy as np
import rawpy
from PIL import Image

sys.path.insert(0, "/home/jannchie/llr/sony_repro/tools")

from aniso_stage import find_arw  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))

ORIENT = {
    "原样": lambda a: a,
    "上下翻": lambda a: a[::-1],
    "左右翻": lambda a: a[:, ::-1],
    "旋转180": lambda a: a[::-1, ::-1],
    "转置": lambda a: a.T,
    "转置+上下翻": lambda a: a.T[::-1],
    "转置+左右翻": lambda a: a.T[:, ::-1],
    "旋转90": lambda a: a.T[::-1, ::-1],
}


def main() -> int:
    stem = sys.argv[1] if len(sys.argv) > 1 else "fl_test"
    cap = sys.argv[2] if len(sys.argv) > 2 else f"rawnr_full_{stem}.npz"
    z = np.load(os.path.join(HERE, cap))
    eng = z["mosaic"][0::2, 0::2].astype(np.float64)
    arw = find_arw(stem)
    with rawpy.imread(str(arw)) as raw:
        vis = raw.raw_image_visible.copy()
    ref0 = vis[0::2, 0::2].astype(np.float32)
    eh, ew = eng.shape
    print(f"{stem}  引擎相位 {eng.shape}   rawpy 相位 {ref0.shape}\n")
    print("  方向            缩放后相关     |差| 中位")
    best = None
    for name, f in ORIENT.items():
        a = f(ref0)
        # 缩到引擎那份的大小才能逐点比。用 NEAREST 保住噪声。
        got = np.asarray(Image.fromarray(np.ascontiguousarray(a), mode="F")
                         .resize((ew, eh), Image.NEAREST), np.float64)
        n = 12
        x, y = got[n:-n, n:-n], eng[n:-n, n:-n]
        r = float(np.corrcoef(x.ravel(), y.ravel())[0, 1])
        d = float(np.median(np.abs(x - y)))
        print(f"    {name:<12} {r:+10.6f}   {d:9.2f}")
        if best is None or r > best[0]:
            best = (r, name)
    print(f"\n  最好的:{best[1]}  相关 {best[0]:+.6f}")
    if best[0] > 0.8:
        print("  => 就是方向的事。引擎的输入与 raw 同源,只是坐标系不同。")
    else:
        print("  => 方向也解释不了。引擎在降噪前对 raw 做了别的事,"
              "\n     而且那件事不改变单像素噪声的尺度。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
