"""用户报的"边缘会有很诡异的黑块" —— 是画面四周,还是物体边缘。

两处都可能,查法不同,所以两处都查:

  1. **画面四周**。`SonyRawNRDenoiser` 用 `np.pad(..., mode="reflect")` 补出
     `PHASE_MARGIN` 的边距。补错了就会在最外那一圈留下系统性偏差,而"一圈"
     在视觉上正是块状而非点状。判据:最外 N 行/列的改动量,与画面内部比。
  2. **物体边缘**。`count == 0` 那条已经修成保留中心值,但 `count` 很小
     (1~3)时 mean 仍是少数抽头的平均,可能整块偏暗。判据:找出比原图暗
     一大截的**连通块**,报它们的大小 —— 孤立点和成块的东西要分开看。

⚠️ 两个都要在**平面域**(降噪真正作用的地方)上量,不是在成品 RGB 上 ——
成品经过去马赛克与色调曲线,块会被抹开、位置会挪,反而看不清。
"""
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import numpy as np
import rawpy

sys.path.insert(0, "/home/jannchie/llr/sony_repro/tools")
sys.path.insert(0, "/home/jannchie/llr/apps/worker/src")

from aniso_stage import find_arw  # noqa: E402
from llr_worker.denoise import (  # noqa: E402
    _plane_black_levels, _plane_colors, get_denoiser, pack_bayer)
from llr_worker.sony.rawnr import detail_restore, noise_model  # noqa: E402


def connected_sizes(mask, cap=200000):
    """mask 里各连通块的大小(4 邻接),用洪水填充数,不引外部依赖。"""
    seen = np.zeros_like(mask, bool)
    sizes = []
    ys, xs = np.nonzero(mask)
    h, w = mask.shape
    for y0, x0 in zip(ys, xs):
        if seen[y0, x0]:
            continue
        stack = [(y0, x0)]
        seen[y0, x0] = True
        n = 0
        while stack:
            y, x = stack.pop()
            n += 1
            for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                yy, xx = y + dy, x + dx
                if 0 <= yy < h and 0 <= xx < w and mask[yy, xx] and not seen[yy, xx]:
                    seen[yy, xx] = True
                    stack.append((yy, xx))
        sizes.append(n)
        if len(sizes) > cap:
            break
    return np.array(sizes)


def main():
    stem = sys.argv[1] if len(sys.argv) > 1 else "DSC03036"
    arw = find_arw(stem)
    with rawpy.imread(str(arw)) as raw:
        visible = raw.raw_image_visible
        h, w = visible.shape
        he, we = h - (h % 2), w - (w % 2)
        planes = pack_bayer(np.ascontiguousarray(visible[:he, :we])).astype(np.float32)
        sizes_ = raw.sizes
        rp = int(getattr(sizes_, "top_margin", 0) or 0)
        cp = int(getattr(sizes_, "left_margin", 0) or 0)
        black = _plane_black_levels(raw, rp, cp)
        cfa = _plane_colors(raw, rp, cp)
        white = float(raw.white_level)
        scale = np.maximum(white - black, 1.0)
        norm = np.clip((planes - black) / scale, 0.0, 1.0)
        curve, rest = noise_model(arw), detail_restore(arw)
        det = None if rest is None or curve is None else (
            rest.fraction, rest.limit_in_thresholds(curve))

    for name in ("sony", "wavelet"):
        a = get_denoiser(name)(norm.copy(), None, cfa, curve, det,
                               sensor_levels=(black, white))
        d = a - norm
        print(f"\n=== {name}")

        # 1. 画面四周
        print("  四周各带的 |改动| 均值(平面坐标):")
        for n in (2, 5, 8, 16, 64):
            ring = np.zeros(d.shape[:2], bool)
            ring[:n, :] = ring[-n:, :] = True
            ring[:, :n] = ring[:, -n:] = True
            inner = ~ring
            ro = float(np.abs(d)[ring].mean())
            io = float(np.abs(d)[inner].mean())
            flag = "  <- 异常" if ro > io * 3.0 else ""
            print(f"    最外 {n:>3} 圈 {ro:.6f}   内部 {io:.6f}   比 {ro/max(io,1e-9):6.2f}x{flag}")

        # 2. 变暗的连通块 —— **逐平面**分开看。
        # 判别性在这里:若块压倒性地落在两个绿平面上,那它与高光彩边是同一个
        # 根因(绿色滤波核是借用 R/B 的);若四个平面均摊,那是 sigma 滤波器
        # 在小 count 下的通病,与绿色核无关。
        names = list(cfa) if cfa is not None else ["R", "G", "G", "B"]
        for k in range(4):
            dark = a[..., k] < norm[..., k] - 0.03
            if not dark.any():
                print(f"  平面{k}({names[k]}):没有变暗 0.03 以上的点")
                continue
            cs = connected_sizes(dark) if dark.sum() < 400000 else np.array([])
            if cs.size == 0:
                print(f"  平面{k}({names[k]}):点太多({int(dark.sum())}),跳过")
                continue
            big = cs[cs >= 4]
            print(f"  平面{k}({names[k]}):暗点 {100*float(dark.mean()):.4f}%  "
                  f"块 {cs.size} 个,最大 {int(cs.max())};"
                  f"≥4 像素的 {big.size} 个,占 {int(big.sum())} 像素")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
