"""llr 的成品 vs Edit 的成品,逐像素量差距 —— 这才是"肉眼看到的"那个差距。

之前所有"99.85% 逐位相同"都是**拿引擎自己的输入**去算的:抓它的 mosaic/ref,
再用我们的算子跑一遍。那证明算子对,不证明 llr 跑起来一样 —— llr 的输入来自
rawpy,而 `mosaic_input_check.py` 已经查出引擎那份 mosaic 的值域只有 525~1454
(真实 raw 是 291~16383)、尺寸比也不是整数,根本是另一条预览路径的数据。

所以换个问法:把两边的**成品**摆在一起,差多少、差在哪。用户在 Edit 里关掉降噪
导出了一张 JPEG,llr 这边也关掉降噪渲染,条件对齐。

⚠️ 两边可能有镜头畸变校正带来的位移(notes 2.12)。所以先估一个整体平移再比,
估不出来就明说,不要拿错位的图报数字 —— 那会把一切都算成"差异"。
"""
import os
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import numpy as np
from PIL import Image

sys.path.insert(0, "/home/jannchie/llr/sony_repro/tools")
sys.path.insert(0, "/home/jannchie/llr/apps/worker/src")

from aniso_stage import find_arw, render, sharpen  # noqa: E402
from lens_apply import apply_distortion, parse_lens_corr  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
REC601 = np.array([0.299, 0.587, 0.114], np.float32)


def best_shift(a: np.ndarray, b: np.ndarray, span: int = 6) -> tuple:
    """用中心一块估整体平移。相位相关太重,直接暴力搜 —— 只搜几像素。"""
    h, w = a.shape
    cy, cx = h // 2, w // 2
    n = 256
    ref = a[cy - n:cy + n, cx - n:cx + n]
    best = None
    for dy in range(-span, span + 1):
        for dx in range(-span, span + 1):
            t = b[cy - n + dy:cy + n + dy, cx - n + dx:cx + n + dx]
            if t.shape != ref.shape:
                continue
            d = float(np.mean(np.abs(ref - t)))
            if best is None or d < best[0]:
                best = (d, dy, dx)
    return best


def main() -> int:
    stem = sys.argv[1] if len(sys.argv) > 1 else "DSC03036"
    arw = find_arw(stem)
    jpg = Path(str(arw).replace(".ARW", ".JPG"))
    if not jpg.exists():
        print(f"没有 {jpg}")
        return 1
    edit = np.asarray(Image.open(jpg).convert("RGB"), np.float32) / 255.0
    print(f"{stem}\n  Edit 导出 {edit.shape}")

    llr = np.asarray(render(arw, denoise_model="passthrough", chroma=True),
                     np.float32)
    # ⚠️ 必须补镜头畸变校正。llr 的这一步在 web 的 shader 里,离线链路(worker 端)
    # 没有;不补的话两边四角错开 ±24 像素以上(align_check.py),量到的"差距"里
    # 绝大部分是错位。notes 2.12 早就写过这条。
    from llr_worker.cli import read_exiftool_metadata, sony_lens_corrections
    lc = parse_lens_corr(sony_lens_corrections(read_exiftool_metadata(arw)))
    if lc is not None:
        llr, s = apply_distortion(llr, lc["distortion"])
        print(f"  llr 渲染 {llr.shape}(均关降噪,已补镜头校正 s={s:.6f})")
    else:
        print(f"  llr 渲染 {llr.shape}(均关降噪;⚠️ 无镜头校正表)")
    if llr.shape != edit.shape:
        print(f"  ⚠️ 尺寸不同,裁到公共区域再比 —— 这本身就是一处差异")
    h = min(llr.shape[0], edit.shape[0])
    w = min(llr.shape[1], edit.shape[1])
    llr, edit = llr[:h, :w], edit[:h, :w]

    ly, ey = llr @ REC601, edit @ REC601
    d, dy, dx = best_shift(ey, ly)
    print(f"  估到的整体平移 ({dy:+d},{dx:+d}),该处 |亮度差| 均值 {d:.5f}")
    if abs(dy) > 0 or abs(dx) > 0:
        ly = np.roll(np.roll(ly, -dy, 0), -dx, 1)
        llr = np.roll(np.roll(llr, -dy, 0), -dx, 1)

    # Edit 导出时 Sharpness=Normal,而相机**没有"关"这一档**(sony/sharpness.py:
    # "even Sharpness +0 still sharpens"),所以它必然锐化过,而离线渲染 sharp=0
    # 完全没有。扫一遍找出让差距最小的量 —— 从 shader 常数推 amount 的尺度不如
    # 直接量。锐化在原始几何上做,所以放在畸变校正之前那一步的输出上。
    print("\n  锐化量扫描(|Δ| 均值应在正确的量上出现极小):")
    best = None
    for a in (0.0, 0.01, 0.02, 0.04, 0.083, 0.15, 0.3):
        cand = llr if a == 0.0 else sharpen(llr, a)
        e = float(np.mean(np.abs(cand[64:h - 64, 64:w - 64]
                                 - edit[64:h - 64, 64:w - 64])))
        mark = ""
        if best is None or e < best[0]:
            best, mark = (e, a), "  ←"
        print(f"    amount {a:6.3f}   |Δ| 均值 {e:.5f}{mark}")
    if best[1] > 0.0:
        llr = sharpen(llr, best[1])
        print(f"  取 amount={best[1]:.3f} 继续下面的分档")

    m = (slice(64, h - 64), slice(64, w - 64))
    err = llr[m] - edit[m]
    print(f"\n  逐像素(裁掉 64 圈):")
    print(f"    |差| 均值 {float(np.mean(np.abs(err))):.5f}   "
          f"p99 {float(np.percentile(np.abs(err), 99)):.5f}   "
          f"最大 {float(np.abs(err).max()):.5f}")
    print(f"    亮度 |差| 均值 {float(np.mean(np.abs(ly[m] - ey[m]))):.5f}")
    for i, ch in enumerate("RGB"):
        e = err[..., i]
        print(f"    {ch}: 偏置 {float(np.mean(e)):+.5f}  "
              f"|差| 均值 {float(np.mean(np.abs(e))):.5f}")

    # 差异是整体色偏、还是集中在某些亮度段?分开看,别混成一个数。
    print("\n  按亮度分档(看差异集中在哪):")
    for lo, hi in ((0, 0.05), (0.05, 0.15), (0.15, 0.35), (0.35, 0.6), (0.6, 1.01)):
        sel = (ey[m] >= lo) & (ey[m] < hi)
        if sel.sum() < 1000:
            continue
        print(f"    亮度 [{lo:.2f},{hi:.2f})  占 {100 * sel.mean():5.1f}%   "
              f"|差| 均值 {float(np.mean(np.abs(err[sel]))):.5f}   "
              f"亮度偏置 {float(np.mean((ly[m] - ey[m])[sel])):+.5f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
