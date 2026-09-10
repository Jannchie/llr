"""把引擎那份 mosaic 直接画出来看 —— 它到底是不是这张照片。

绕了一圈:它不是整幅缩放(相关 0),不是 tile(相关 −0.08),可它拆出的相位与引擎
自己的 `ref` 相关 0.99。三条放在一起,只剩两种可能:

  * 它确实是这张照片,只是走了一条我还没认出来的解码路径;
  * **我读它的方式就是错的**(stride 或 dtype),那么它"像图像"只是错得有规律,
    而前面一大串结论的地基都在这上面。

分不清就先看图。同时把 rawpy 的 raw 缩到同样大小画一张,并排对照。
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
OUT = "/home/jannchie/llr/tmp"


def to_img(a: np.ndarray, gamma: float = 0.45) -> Image.Image:
    """按分位数拉伸再加个 gamma,暗部才看得见东西。"""
    lo, hi = np.percentile(a, [0.5, 99.5])
    v = np.clip((a.astype(np.float64) - lo) / max(hi - lo, 1e-6), 0, 1) ** gamma
    return Image.fromarray((v * 255).astype(np.uint8))


def main() -> int:
    stem = sys.argv[1] if len(sys.argv) > 1 else "DSC03036"
    cap = sys.argv[2] if len(sys.argv) > 2 else f"rawnr_full_{stem}.npz"
    z = np.load(os.path.join(HERE, cap))
    mos = z["mosaic"]
    mh, mw = mos.shape
    arw = find_arw(stem)
    with rawpy.imread(str(arw)) as raw:
        vis = raw.raw_image_visible.copy()

    # 各取一个相位来画,免得 Bayer 的棋盘格盖住内容。
    eng = to_img(mos[0::2, 0::2])
    # ⚠️ 右边必须用**抽样**(NEAREST),不能用面积平均。第一版右边做了 6x6 平均,
    # 噪声降了六倍,于是左边(单像素 raw,夜景、信噪比极低)看着像纯噪声、右边像
    # 干净照片 —— 那是两种处理的差别,不是数据的差别,差点据此断定"mosaic 读错了"。
    ref = to_img(np.asarray(
        Image.fromarray(vis[0::2, 0::2].astype(np.float32), mode="F")
        .resize((mw // 2, mh // 2), Image.NEAREST), np.float32))

    w, h = eng.size
    canvas = Image.new("L", (w * 2 + 12, h), 0)
    canvas.paste(eng, (0, 0))
    canvas.paste(ref, (w + 12, 0))
    path = os.path.join(OUT, f"{stem}-mosaic-vs-raw.png")
    canvas.save(path)
    print(f"左=引擎的 mosaic  右=rawpy 缩到同尺寸   {mos.shape}")
    print(f"写出 {path}")
    print("\n  两边画面一致 => 引擎那份就是这张照片,只是解码路径不同;"
          "\n  左边是乱码/错位 => 是我读 mosaic 的方式错了(stride 或 dtype),"
          "\n  那前面所有基于它的结论都要重做。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
