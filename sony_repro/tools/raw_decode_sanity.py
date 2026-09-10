"""rawpy 解出来的 Bayer 数据本身对不对 —— 补上论证链的第三环。

全分辨率那条链是:①算子在引擎输入上逐位正确(实测)、②算子与尺寸分块无关(实测)、
③无损压缩解码唯一(此前只靠格式保证)。第三环没法直接对着引擎验(它的预览走低分辨率
解码,与 rawpy 那份空间上对不上),但**解码有没有出错是可以自检的**:

* **压缩块痕迹**:Sony 的无损压缩按固定长度分块。解码若错位或某块解错,块内位置
  会留下系统性偏差 —— 把像素按 `x mod k` 分组看均值,正确的解码应当**没有**周期性。
* **方差-均值关系**:传感器噪声 = 散粒(泊松,方差 ∝ 信号)+ 读出(常数)。正确解码
  出来的数据这条关系必须是线性的;解错会让高位错乱,关系立刻塌掉。

两条都过,说明这批数字是合乎物理的传感器读数,而不是解错的位模式 —— 第三环就从
"纯靠格式保证"变成"有实测支持"。仍不等于"和引擎逐位相同",这一点不含糊。
"""
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import numpy as np
import rawpy

sys.path.insert(0, "/home/jannchie/llr/sony_repro/tools")

from aniso_stage import find_arw  # noqa: E402


def main() -> int:
    stem = sys.argv[1] if len(sys.argv) > 1 else "DSC03036"
    arw = find_arw(stem)
    with rawpy.imread(str(arw)) as raw:
        vis = raw.raw_image_visible.copy()
        black = float(np.mean(raw.black_level_per_channel))
    # 单相位,避免 Bayer 的通道差异混进来。
    p = vis[0::2, 0::2].astype(np.float64)
    h, w = p.shape
    print(f"{stem}  相位 {p.shape}  黑电平 {black:.0f}\n")

    print("  1. 压缩块痕迹:按 x mod k 分组的均值(正确解码应当没有周期性)")
    for k in (8, 16, 32):
        cols = [float(p[:, i::k].mean()) for i in range(k)]
        a = np.array(cols)
        spread = float(a.max() - a.min())
        rel = spread / max(float(a.mean()) - black, 1.0)
        flag = "  ⚠️ 有周期性" if rel > 0.02 else ""
        print(f"    k={k:3d}  组间极差 {spread:8.3f}   "
              f"相对信号 {100 * rel:6.3f}%{flag}")

    print("\n  2. 方差-均值关系(散粒噪声应当线性:var = a·signal + b)")
    # 用**相邻像素差**估噪声,不要用块方差:块里混着画面结构,拟合会被带偏
    # (第一版就是这样,截距 −3753、低信号档预测为负,明显不物理)。相邻差
    # 只留下像素级的噪声,再用四分位距抗掉跨边缘的大差值。
    d = (p[:, :-1] - p[:, 1:]) / np.sqrt(2.0)
    lvl = (p[:, :-1] + p[:, 1:]) / 2.0 - black
    edges = np.percentile(lvl[(lvl > 3) & (lvl < 8000)], np.linspace(0, 100, 12))
    xs, ys = [], []
    for lo, hi in zip(edges, edges[1:]):
        m = (lvl >= lo) & (lvl < hi)
        if m.sum() < 2000:
            continue
        q1, q3 = np.percentile(d[m], [25, 75])
        sigma = (q3 - q1) / 1.349
        xs.append(float(np.median(lvl[m])))
        ys.append(float(sigma ** 2))
    xs, ys = np.array(xs), np.array(ys)
    A = np.stack([xs, np.ones_like(xs)], axis=1)
    coef, *_ = np.linalg.lstsq(A, ys, rcond=None)
    pred = A @ coef
    ss = 1.0 - float(np.sum((ys - pred) ** 2) / max(np.sum((ys - ys.mean()) ** 2), 1e-9))
    print(f"    拟合 var = {coef[0]:.4f}·signal + {coef[1]:.1f}    R² = {ss:.5f}")
    for x, y, q in zip(xs, ys, pred):
        print(f"      信号 {x:8.1f}   方差 {y:9.1f}   拟合 {q:9.1f}")
    print(f"\n  斜率为正且 R² 接近 1 => 是合乎物理的传感器读数,解码没有错位;"
          f"\n  ⚠️ 这仍不等于「和引擎逐位相同」,只是把第三环从纯推理变成有实测支持。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
