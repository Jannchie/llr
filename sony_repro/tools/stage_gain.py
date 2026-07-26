r"""任意一段的 in/out 在**同一个域**里改了什么:亮度、色度、按亮度分段。

YGamma 的教训是别只看整幅中位数 —— 一段可能只动两头(ChromaSuppres 就是设计成
这样),整幅中位数会把它藏起来。所以按 Y 分箱给出各自的增益。

平面顺序是 Y / Cr / Cb,色度以 8192 为零点(§7.6)。

用法: python stage_gain.py <任务名>            # 用 tools/stage_frames.npz
"""
import sys
from pathlib import Path

import numpy as np

NPZ = Path(__file__).parent / "stage_frames.npz"
MID = 8192.0


def main():
    name = sys.argv[1]
    z = np.load(sys.argv[2] if len(sys.argv) > 2 else NPZ)
    a, b = z[f"{name}_in"].astype(np.float64), z[f"{name}_out"].astype(np.float64)
    ok = a.max(-1) > 0
    y0, y1 = a[..., 0][ok], b[..., 0][ok]
    c0 = np.hypot(a[..., 1][ok] - MID, a[..., 2][ok] - MID)
    c1 = np.hypot(b[..., 1][ok] - MID, b[..., 2][ok] - MID)

    print(f"{name}: {ok.sum()} 像素")
    for k, nm in ((0, "Y"), (1, "Cr"), (2, "Cb")):
        print(f"  平面{k} ({nm:>2}) 改动 {100 * (a[..., k][ok] != b[..., k][ok]).mean():7.4f}%")

    print("\n  按亮度分箱(色度 >200 的像素):")
    edges = [0, 512, 1024, 2048, 4096, 8192, 12288, 16384]
    sel = c0 > 200
    for lo, hi in zip(edges[:-1], edges[1:], strict=True):
        m = sel & (y0 >= lo) & (y0 < hi)
        if m.sum() < 200:
            continue
        print(f"    Y {lo:>5}..{hi:<5} n={m.sum():>8}  亮度 x{np.median(y1[m] / np.maximum(y0[m], 1)):.4f}"
              f"   色度 x{np.median(c1[m] / c0[m]):.4f}")
    print(f"    {'全部':<13} n={sel.sum():>8}  亮度 x{np.median(y1[sel] / np.maximum(y0[sel], 1)):.4f}"
          f"   色度 x{np.median(c1[sel] / c0[sel]):.4f}")


if __name__ == "__main__":
    main()
