"""把 llr 与 Edit 剩下的 0.0209 分解开:多少是噪声,多少是真正的处理差异。

已经排掉的:几何错位(补了镜头畸变校正,0.0486 → 0.0209)、色调曲线(各亮度档偏置
只有 +0.003~+0.014 且同向)、锐化(加了只会更远)、demosaic(换四种算法只动 0.3%)。
剩下约四成没着落,而"六成是噪声"这个估计是**按 σ 推的**,不是量的。

这里直接量:按**局部结构强度**分档。

  * 平坦区(局部方差低):两边的差只可能是各自的噪声 —— 这一档的 |Δ| 就是噪声底;
  * 结构区:噪声 + 处理差异。

两档之差才是真正的处理差距。若结构区的 |Δ| 并不比平坦区高多少,那 0.0209 基本就是
噪声,没有"未定位的处理差异"可追;若高出不少,差在哪一类结构上也就有了线索。

⚠️ 必须先补镜头畸变校正,否则量的是错位(notes 2.19.6)。
"""
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import numpy as np
from PIL import Image

sys.path.insert(0, "/home/jannchie/llr/sony_repro/tools")
sys.path.insert(0, "/home/jannchie/llr/apps/worker/src")

from aniso_stage import find_arw, render  # noqa: E402
from lens_apply import apply_distortion, parse_lens_corr  # noqa: E402

REC601 = np.array([0.299, 0.587, 0.114], np.float32)
BLK = 16


def blockvar(y: np.ndarray) -> np.ndarray:
    """每个 16x16 块的「结构强度」:先 4x4 平均再看方差,避开像素级噪声。"""
    h, w = y.shape
    h -= h % BLK
    w -= w % BLK
    t = y[:h, :w].reshape(h // BLK, BLK, w // BLK, BLK)
    small = t.reshape(h // BLK, BLK // 4, 4, w // BLK, BLK // 4, 4).mean(axis=(2, 5))
    return small.var(axis=(1, 3))


def main() -> int:
    stem = sys.argv[1] if len(sys.argv) > 1 else "DSC03036"
    arw = find_arw(stem)
    jpg = Path(str(arw).replace(".ARW", ".JPG"))
    edit = np.asarray(Image.open(jpg).convert("RGB"), np.float32) / 255.0
    from llr_worker.cli import read_exiftool_metadata, sony_lens_corrections
    lc = parse_lens_corr(sony_lens_corrections(read_exiftool_metadata(arw)))
    # llr 的色度清理(Marble 的那半)是**无条件**跑的,而 Edit 关掉降噪时很可能连它
    # 一起关了 —— 旁证:llr 的色度噪声只有 Edit 的 1/4.6(0.148 对 0.678,
    # noise_chroma_ratio.py)。色度清理正是会抹平结构的东西,所以两种都跑一遍。
    use_chroma = "--no-chroma" not in sys.argv
    llr = np.asarray(render(arw, denoise_model="passthrough", chroma=use_chroma),
                     np.float32)
    if "--spica" in sys.argv:
        # 离线链路缺 Spica(web 端有,passes.ts:899 起),而剩余差距恰好集中在结构
        # 区 —— Spica 正是读邻域、按细节强度分类的算子。`spica_model.py` 是从反
        # 汇编逐条对出来的(逐位 100%),直接用它。
        #
        # ⚠️ 它跑在 14bit 整数平面上,而这里是 float 的显示帧;web 端的 GLSL 版为此
        # 改成 float 并去掉了两次截断。所以这里映射到 14bit 域再跑,与 web 端会有
        # 微小差异 —— 够用来看"Spica 能解释多少",不足以拿来做逐位对比。
        from spica_model import Cfg, load_wtab, run
        cfg, W = Cfg(), load_wtab()
        sw = (float(sys.argv[sys.argv.index("--spica-w") + 1])
              if "--spica-w" in sys.argv else 0.5)
        acc = np.empty_like(llr)
        for ch in range(3):
            q = np.clip(llr[..., ch] * 16383.0, 0, 16383).astype(np.uint16)
            acc[..., ch] = run(q, cfg, W, weight=sw)[0].astype(np.float32) / 16383.0
        llr = acc
        print(f"  已补 Spica(14bit 域,逐通道,weight={sw})")
    if lc is not None:
        llr, _ = apply_distortion(llr, lc["distortion"])
    h = min(llr.shape[0], edit.shape[0])
    w = min(llr.shape[1], edit.shape[1])
    llr, edit = llr[:h, :w], edit[:h, :w]

    ly, ey = llr @ REC601, edit @ REC601
    d = np.abs(ly - ey)
    var = blockvar(ey)
    bh, bw = var.shape
    dblk = d[:bh * BLK, :bw * BLK].reshape(bh, BLK, bw, BLK).mean(axis=(1, 3))
    print(f"{stem}  {h}x{w}  分成 {bh}x{bw} 个 {BLK}px 块"
          f"   色度清理={'开' if use_chroma else '关'}")
    print(f"  整体 |Δ亮度| 均值 {float(d.mean()):.5f}"
          f"   |Δ全通道| 均值 {float(np.abs(llr - edit).mean()):.5f}\n")

    qs = np.percentile(var, [10, 30, 50, 70, 90])
    edges = [0.0, *qs, float(var.max()) + 1]
    names = ["最平坦 10%", "10-30%", "30-50%", "50-70%", "70-90%", "最有结构 10%"]
    print("  结构强度档        块数      |Δ亮度| 均值")
    vals = []
    for nm, lo, hi in zip(names, edges, edges[1:]):
        m = (var >= lo) & (var < hi)
        if not m.any():
            continue
        v = float(dblk[m].mean())
        vals.append(v)
        print(f"    {nm:<14} {int(m.sum()):7d}      {v:.5f}")
    # ⚠️ 结构强的地方通常也更亮,而散粒噪声 ∝ sqrt(信号) —— 上面那张表里"结构
    # 相关"和"亮度相关"是混在一起的。在**同一亮度档内**再按结构分,才分得开:
    # 档内还随结构涨 => 真有处理差异;档内平掉 => 那就是噪声,追错方向了。
    yblk = ey[:bh * BLK, :bw * BLK].reshape(bh, BLK, bw, BLK).mean(axis=(1, 3))
    print("\n  控制亮度后(同一亮度档内再按结构分三档):")
    print("    亮度档            低结构    中结构    高结构     高/低")
    for lo, hi in ((0.02, 0.06), (0.06, 0.12), (0.12, 0.25), (0.25, 0.60)):
        sel = (yblk >= lo) & (yblk < hi)
        if sel.sum() < 300:
            continue
        v = var[sel]
        d3 = dblk[sel]
        q1, q2 = np.percentile(v, [33, 67])
        cells = [float(d3[v < q1].mean()),
                 float(d3[(v >= q1) & (v < q2)].mean()),
                 float(d3[v >= q2].mean())]
        print(f"    [{lo:.2f},{hi:.2f})  n={int(sel.sum()):6d}  "
              + "  ".join(f"{c:.5f}" for c in cells)
              + f"   {cells[2] / max(cells[0], 1e-9):5.2f}x")

    if len(vals) >= 2:
        floor, top = vals[0], vals[-1]
        print(f"\n  噪声底(最平坦档) {floor:.5f}   最有结构档 {top:.5f}   "
              f"差 {top - floor:+.5f}")
        print(f"  处理差异占最有结构档的 "
              f"{100 * (top - floor) / max(top, 1e-9):.1f}%")
        print("\n  两档接近 => 剩下的基本就是噪声,没有"
              "未定位的处理差异可追;"
              "\n  结构档明显更高 => 差异跟结构有关,那才值得继续找。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
