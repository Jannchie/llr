"""引擎那份 mosaic 是不是原图的一个 **tile**(而不是整幅缩小)。

`mosaic_downsample_match.py` 把它当成"整幅图缩到 1114x682"去比,相关几乎是 0,
于是我判定它走的是另一条解码路径。可 `rect=[0,0,1114,682]` 同样可以读成**左上角
那一块原始像素**——tile。若真是 tile,那份数据就是全分辨率的一部分,值域只有
525~1454 也说得通(左上角是暗部),而且意味着之前所有"逐位 99.85%"根本就是在
**真实输入**上做的,不是在预览上。

这个区别决定了整条结论线,所以直接试:拿 rawpy 的 raw 在几个候选位置切出同样大小
的块,逐像素比。含边的 raw 还要考虑左上角的 margin,一并扫。
"""
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import numpy as np
import rawpy

sys.path.insert(0, "/home/jannchie/llr/sony_repro/tools")

from aniso_stage import find_arw  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))


def main() -> int:
    stem = sys.argv[1] if len(sys.argv) > 1 else "DSC03036"
    cap = sys.argv[2] if len(sys.argv) > 2 else f"rawnr_full_{stem}.npz"
    z = np.load(os.path.join(HERE, cap))
    mos = z["mosaic"].astype(np.float64)
    mh, mw = mos.shape
    arw = find_arw(stem)
    with rawpy.imread(str(arw)) as raw:
        vis = raw.raw_image_visible.astype(np.float64)
        full = raw.raw_image.astype(np.float64)
    print(f"{stem}  引擎 mosaic {mos.shape}   visible {vis.shape}   "
          f"含边 {full.shape}\n")

    best = None
    for label, src in (("visible", vis), ("含边", full)):
        # Bayer 的相位必须对齐,所以偏移只能是**偶数**。
        for oy in range(0, 33, 2):
            for ox in range(0, 33, 2):
                if oy + mh > src.shape[0] or ox + mw > src.shape[1]:
                    continue
                sub = src[oy:oy + mh, ox:ox + mw]
                # 不要用均值预筛。第一版用 |Δ均值|>30 就跳过,结果把**所有**候选
                # 都筛掉了 —— 左上角是暗部,均值本来就该和全图差一大截,那是假
                # 阴性,不是"对不上"。相关本身已经降采样算过,不贵。
                r = float(np.corrcoef(sub[::4, ::4].ravel(),
                                      mos[::4, ::4].ravel())[0, 1])
                if best is None or r > best[0]:
                    best = (r, label, oy, ox)
                if r > 0.9:
                    exact = 100.0 * float(np.mean(sub == mos))
                    print(f"    {label} 偏移 ({oy},{ox})  相关 {r:+.6f}   "
                          f"逐位相同 {exact:.4f}%")
    if best is None:
        print("    没有一个候选位置的均值对得上 —— 不是简单的 tile 裁剪")
        return 0
    r, label, oy, ox = best
    print(f"\n  最好的:{label} 偏移 ({oy},{ox})  相关 {r:+.6f}")
    if r > 0.9:
        sub = (vis if label == "visible" else full)[oy:oy + mh, ox:ox + mw]
        d = sub - mos
        print(f"  |差| 中位 {float(np.median(np.abs(d))):.4f}   "
              f"最大 {float(np.abs(d).max()):.1f}   "
              f"逐位相同 {100 * float(np.mean(sub == mos)):.4f}%")
        print("  => 它就是原图的一个 tile。那么之前的验证一直在**真实输入**上,"
              "\n     '预览路径'那个判断要撤回。")
    else:
        print("  => 不是 tile,也不是整幅缩放。它确实走了另一条路。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
