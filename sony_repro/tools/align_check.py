"""llr 与 Edit 的成品到底对没对齐 —— 分区域估平移。

`tone_transfer.py` 量到条件散布的带宽(0.2~0.3)远大于中位偏移(0.03~0.07),看着像
"空间/局部处理不同"。但在下这个结论之前必须先排掉一个已经踩过一次的坑:
notes 2.12 写着「离线复现链路缺镜头畸变校正,所有逐像素对比此前都是在错位的图上
做的」。ARW 的 SubIFD 里就带着 `DistortionCorrParams`,Edit 会应用它。

畸变是**径向**的:中心几乎不动,越靠边位移越大。所以一个全局的 +3 像素补不了,
而错位会把条件散布撑得很宽 —— 那时量到的"带宽大"根本不是画质差异。

判据:把画面切成 3x3,每块各自估平移。中心小、四周大且向外发散 => 是畸变;
各块都一样 => 只是整体位移,可以用一个平移补掉。
"""
import os
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import numpy as np
from PIL import Image

sys.path.insert(0, "/home/jannchie/llr/sony_repro/tools")
sys.path.insert(0, "/home/jannchie/llr/apps/worker/src")

from aniso_stage import E, find_arw, render  # noqa: E402
from lens_apply import apply_distortion, parse_lens_corr  # noqa: E402


def _lens_meta(arw):
    """镜头校正表。直接从 EXIF 算 —— `prepare_linear` 不往 colorProfile 里塞
    lensCorr(那是 daemon_linear 那条路径干的),照着取只会拿到 None。"""
    from llr_worker.cli import read_exiftool_metadata, sony_lens_corrections
    return sony_lens_corrections(read_exiftool_metadata(arw))

REC601 = np.array([0.299, 0.587, 0.114], np.float32)
SPAN = 24        # 搜索半径(像素)
PATCH = 320      # 每块用来估的窗口边长


def best_shift(ref: np.ndarray, mov: np.ndarray, cy: int, cx: int) -> tuple:
    """在 (cy,cx) 附近暴力搜最佳平移。用绝对差之和,对噪声比相关稳。"""
    n = PATCH // 2
    a = ref[cy - n:cy + n, cx - n:cx + n]
    best = None
    for dy in range(-SPAN, SPAN + 1, 2):
        for dx in range(-SPAN, SPAN + 1, 2):
            b = mov[cy - n + dy:cy + n + dy, cx - n + dx:cx + n + dx]
            if b.shape != a.shape:
                continue
            d = float(np.mean(np.abs(a - b)))
            if best is None or d < best[0]:
                best = (d, dy, dx)
    return best


def main() -> int:
    stem = sys.argv[1] if len(sys.argv) > 1 else "DSC03036"
    arw = find_arw(stem)
    jpg = Path(str(arw).replace(".ARW", ".JPG"))
    edit = np.asarray(Image.open(jpg).convert("RGB"), np.float32) / 255.0
    llr = np.asarray(render(arw, denoise_model="passthrough", chroma=True),
                     np.float32)
    # 补上镜头校正:离线链路走 worker 端,而 llr 的这一步在 web 的 shader 里。
    # 不补的话下面量到的全是几何错位,不是画质。
    lc = parse_lens_corr(_lens_meta(arw))
    if lc is None:
        print("  ⚠️ 这张片没有镜头校正表,下面的读数按原样比")
    else:
        llr, s = apply_distortion(llr, lc["distortion"])
        print(f"  已应用镜头畸变校正,fill scale = {s:.6f}")
    h, w = min(llr.shape[0], edit.shape[0]), min(llr.shape[1], edit.shape[1])
    ey, ly = (edit[:h, :w] @ REC601), (llr[:h, :w] @ REC601)
    print(f"{stem}  {h}x{w}   每块 {PATCH}px,搜索 ±{SPAN}px\n")

    print("  区域(行,列)      最佳平移(dy,dx)    残差")
    shifts = []
    for iy, fy in enumerate((0.25, 0.5, 0.75)):
        row = []
        for ix, fx in enumerate((0.25, 0.5, 0.75)):
            cy, cx = int(h * fy), int(w * fx)
            d, dy, dx = best_shift(ey, ly, cy, cx)
            row.append((dy, dx))
            shifts.append((fy, fx, dy, dx))
            print(f"    ({iy},{ix})          ({dy:+3d},{dx:+3d})        {d:.5f}")
    dys = [s[2] for s in shifts]
    dxs = [s[3] for s in shifts]
    print(f"\n  dy 范围 [{min(dys)}, {max(dys)}]   dx 范围 [{min(dxs)}, {max(dxs)}]")
    # 判据要按搜索步长放宽:步长就是 2,±2 的散布是量化噪声,不是几何差异。
    if max(dys) - min(dys) <= 4 and max(dxs) - min(dxs) <= 4:
        print("  => 各块平移一致 => 只是整体位移,一个平移就能补掉,"
              "\n     前面量到的带宽不是错位造成的。")
    else:
        print("  => 各块平移**不一致** => 两边的几何不同(镜头畸变校正),"
              "\n     那么所有逐像素对比都是在错位的图上做的,带宽读数不能用。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
