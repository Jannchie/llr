"""引擎那份 mosaic 是**原始像素**还是**降采样平均** —— 用噪声幅度判。

这一条决定了前面所有验证的性质:

  * 若是原始像素(某个 tile),那 `rawnr_e2e_verify` 一直就是在**真实输入**上跑的,
    "那些捕获都是预览路径"这个撤回本身要再撤回;
  * 若是降采样平均,才轮到说"llr 实际那条路没验过"。

判据不受位置和内容影响:降采样平均 n 个样本,噪声的标准差会降到 1/sqrt(n)。所以
只要比"相邻像素之差"的尺度就行。同一相位内取相邻(间距 2)的两点作差,差的标准差
除以 sqrt(2) 就是单像素噪声 σ。

⚠️ 要在**相近的信号电平**上比,不然比的是散粒噪声随亮度的变化。所以按电平分档,
每档单独报。
"""
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import numpy as np
import rawpy

sys.path.insert(0, "/home/jannchie/llr/sony_repro/tools")

from aniso_stage import find_arw  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))


def noise_by_level(plane: np.ndarray, edges) -> list:
    """按电平分档的单像素噪声 σ。用相邻差,避开大尺度内容。"""
    a = plane.astype(np.float64)
    d = (a[:, :-1] - a[:, 1:]) / np.sqrt(2.0)
    lvl = (a[:, :-1] + a[:, 1:]) / 2.0
    out = []
    for lo, hi in zip(edges, edges[1:]):
        m = (lvl >= lo) & (lvl < hi)
        n = int(m.sum())
        # 用四分位距估 σ,抗边缘处的大差值。
        if n > 500:
            q1, q3 = np.percentile(d[m], [25, 75])
            out.append((lo, hi, n, float((q3 - q1) / 1.349)))
        else:
            out.append((lo, hi, n, float("nan")))
    return out


def main() -> int:
    stem = sys.argv[1] if len(sys.argv) > 1 else "DSC03036"
    cap = sys.argv[2] if len(sys.argv) > 2 else f"rawnr_full_{stem}.npz"
    z = np.load(os.path.join(HERE, cap))
    mos = z["mosaic"]
    arw = find_arw(stem)
    with rawpy.imread(str(arw)) as raw:
        vis = raw.raw_image_visible.copy()

    edges = [600, 700, 800, 900, 1000, 1100, 1200, 1400]
    eng = noise_by_level(mos[0::2, 0::2], edges)
    ref = noise_by_level(vis[0::2, 0::2], edges)
    print(f"{stem}  引擎 mosaic {mos.shape}   rawpy visible {vis.shape}\n")
    print("  电平档          引擎 σ (n)            rawpy σ (n)        比值")
    ratios = []
    for (lo, hi, n1, s1), (_, _, n2, s2) in zip(eng, ref):
        rr = s1 / s2 if s2 and not np.isnan(s1) and not np.isnan(s2) else np.nan
        if not np.isnan(rr):
            ratios.append(rr)
        print(f"    [{lo:4d},{hi:4d})   {s1:7.2f} ({n1:8d})   "
              f"{s2:7.2f} ({n2:9d})   {rr:6.3f}")
    r = float(np.median(ratios)) if ratios else float("nan")
    print(f"\n  比值中位 {r:.3f}")
    if r > 0.8:
        print("  => 噪声一样大 => 引擎那份是**原始像素**(某个 tile),"
              "\n     那么之前的验证一直跑在真实输入上。")
    else:
        n = 1.0 / (r * r) if r else float("inf")
        print(f"  => 噪声小了 {1 / r:.2f} 倍 => 是平均了约 {n:.1f} 个样本的降采样,"
              "\n     不是 llr 拿到的那份数据。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
