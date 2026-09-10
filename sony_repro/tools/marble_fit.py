"""Marble 对色差做了什么 —— 有真值就别读代码。

Marble 是最后一级,in 与 out 都能抓到全分辨率,所以可以直接对着真值验算子假设:

  H1 亮度不动?                    量 |Y_out − Y_in|
  H2 色差被低通?                  拿 (R−G)_in 过各种半径的盒/二项低通,看哪个最像 out
  H3 色差被中值?                  同上换中值
  H4 色差被按亮度衰减(像 ChromaSuppres)?  看 out/in 的比值随 Y 的走势
"""
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import numpy as np

#: 默认是 DSC02995(ISO 320)。**算子的形状是从这一张的两块 tile 上读出来的**,
#: 所以换张片再跑一次是必要的,不是锦上添花 —— 给个名字即可:
#:     marble_fit.py marble2961
Z = Path("/home/jannchie/llr/sony_repro/tools") / (
    f"tiles_{sys.argv[1] if len(sys.argv) > 1 else 'marble'}.npz")
W601 = np.array([0.299, 0.587, 0.114])


def box(a, k):
    pad = k // 2
    p = np.pad(a, pad, mode="edge")
    cs = np.pad(p.cumsum(0).cumsum(1), ((1, 0), (1, 0)))
    return (cs[k:, k:] - cs[:-k, k:] - cs[k:, :-k] + cs[:-k, :-k]) / (k * k)


TILE = 32


def mad(x):
    return float(np.median(np.abs(x - np.median(x))) * 1.4826)


def detail(a):
    h, w = a.shape[0] & ~1, a.shape[1] & ~1
    a = a[:h, :w]
    m = (a[0::2, 0::2] + a[1::2, 0::2] + a[0::2, 1::2] + a[1::2, 1::2]) * 0.25
    return a - np.repeat(np.repeat(m, 2, axis=0), 2, axis=1)


def flat_tiles(y, frac=0.25, cap=200):
    h, w = y.shape[0] // TILE * TILE, y.shape[1] // TILE * TILE
    v = y[:h, :w].reshape(h // TILE, TILE, w // TILE, TILE).std(axis=(1, 3))
    ys, xs = np.nonzero(v <= np.quantile(v, frac))
    idx = np.linspace(0, len(ys) - 1, min(cap, len(ys))).astype(int)
    return [(slice(int(ys[k]) * TILE, int(ys[k]) * TILE + TILE),
             slice(int(xs[k]) * TILE, int(xs[k]) * TILE + TILE)) for k in idx]


def gauss(a, sigma):
    """可分离高斯。盒低通滚降太慢,会连 20px 以上的结构一起伤 —— 而 Marble 保得住,
    所以候选里必须有截止更锐利的那一类。"""
    r = max(1, int(3 * sigma))
    x = np.arange(-r, r + 1, dtype=np.float64)
    k = np.exp(-0.5 * (x / sigma) ** 2)
    k /= k.sum()
    p = np.pad(a, ((0, 0), (r, r)), mode="edge")
    out = np.apply_along_axis(lambda v: np.convolve(v, k, "valid"), 1, p)
    p = np.pad(out, ((r, r), (0, 0)), mode="edge")
    return np.apply_along_axis(lambda v: np.convolve(v, k, "valid"), 0, p)


def pyramid(a, levels):
    """逐级 2x2 盒平均下采样,再双线性上采样回原尺寸 —— 锐利的尺度分割。"""
    cur, shapes = a, []
    for _ in range(levels):
        shapes.append(cur.shape)
        h, w = cur.shape[0] & ~1, cur.shape[1] & ~1
        c = cur[:h, :w]
        cur = (c[0::2, 0::2] + c[1::2, 0::2] + c[0::2, 1::2] + c[1::2, 1::2]) * 0.25
    for shape in reversed(shapes):
        up = np.repeat(np.repeat(cur, 2, axis=0), 2, axis=1)
        # 双线性:上采样后再过一次 3 抽头,免得留下块状
        up = (up + np.roll(up, 1, 0) + np.roll(up, 1, 1)
              + np.roll(np.roll(up, 1, 0), 1, 1)) * 0.25
        pad_h, pad_w = shape[0] - up.shape[0], shape[1] - up.shape[1]
        if pad_h > 0 or pad_w > 0:
            up = np.pad(up, ((0, max(pad_h, 0)), (0, max(pad_w, 0))), mode="edge")
        cur = up[:shape[0], :shape[1]]
    return cur


def explained(target, cand):
    """cand 解释掉 target 的多少 —— 1 − var(残差)/var(target)。"""
    t, c = target.ravel(), cand.ravel()
    return 1.0 - float(np.var(t - c) / max(np.var(t), 1e-12))


def main():
    z = np.load(Z)
    for tag in sorted({k.split("_")[0] for k in z.files if k.endswith("_in")}):
        a = z[f"{tag}_in"].astype(np.float64)
        b = z[f"{tag}_out"].astype(np.float64)
        m = 8  # 切掉边界
        a, b = a[m:-m, m:-m], b[m:-m, m:-m]
        yi, yo = a @ W601, b @ W601
        print(f"\n=== {tag}  {a.shape}")
        print(f"  亮度: in 均值 {yi.mean():8.1f}  out {yo.mean():8.1f}   "
              f"|Δ| 中位 {np.median(np.abs(yo - yi)):7.3f}")
        # 平坦区的细尺度 MAD —— 与 stage_chroma.py 同一指标。整块 std 被真实画面
        # 结构主导,量不出噪声:Marble 保住大尺度色彩结构而只清细噪声,std 因此几乎不动。
        tiles = flat_tiles(yi)
        for name, i, j in (("R-G", 0, 1), ("B-G", 2, 1)):
            di, do = a[..., i] - a[..., j], b[..., i] - b[..., j]
            fi = np.median([mad(detail(di[s])) for s in tiles])
            fo = np.median([mad(detail(do[s])) for s in tiles])
            print(f"  {name}: 细节 MAD in {fi:8.3f} → out {fo:8.3f}   "
                  f"×{fo / max(fi, 1e-9):5.3f}      "
                  f"整块 std {di.std():8.1f} → {do.std():8.1f}")
            # 只在**平坦区**上问一次「是不是低通类」。保边算子在没有边的地方退化为
            # 低通,所以平坦区的解释力才是算子类别的判据 —— 整块的数字被边缘和
            # Clarity 的局部对比度分量拖着(t12 的整块 std 反而涨了),分不出类别。
            cands = {"原样": di}
            cands.update({f"盒 k={k}": box(di, k) for k in (3, 5, 9, 13, 21, 31)})
            # 归一化色度假设:色度降噪多半作用在 (R−G)/Y 上而不是 R−G 上。
            # 两块 tile 的亮度均值差不少(7663 / 5388),正好能把这两种分开。
            ny = np.maximum(yi, 1.0)
            for k in (9, 13, 21, 31):
                cands[f"(R-G)/Y 盒 k={k}"] = box(di / ny, k) * np.maximum(yo, 1.0)
            flat_t = np.concatenate([do[s].ravel() for s in tiles])
            scores = {}
            for cname, c in cands.items():
                p = np.concatenate([c[s].ravel() for s in tiles])
                scores[cname] = 1.0 - float(np.var(flat_t - p) / max(np.var(flat_t), 1e-12))
            top = sorted(scores.items(), key=lambda kv: -kv[1])[:3]
            print("        平坦区解释力: "
                  + "   ".join(f"{n} {v * 100:6.2f}%" for n, v in top))

            # 线性混合的最小二乘:out ≈ a·in + b·box9 + c·box21 + d。
            # 若 a 明显非零,说明是「部分混回原图」而不是纯替换;若 b/c 之和明显
            # 偏离 1,就有增益。
            # ⚠️ 整信号的拟合在这里是**没意义的**:Marble 同时在做一个大幅色彩变换
            # (Clarity 那一支),in→out 的差异被它主导 —— 实测所有平滑候选的残差都和
            # 「原样」相差不到 2%。真正该问的只在**细节带**上:细节从 70.6 掉到 3.3,
            # 是「按系数衰减」还是「被整个换掉」?前者的复刻就只是乘一个系数。
            dd_i = np.concatenate([detail(di[s]).ravel() for s in tiles])
            dd_o = np.concatenate([detail(do[s]).ravel() for s in tiles])
            slope = float((dd_o * dd_i).sum() / max((dd_i * dd_i).sum(), 1e-12))
            corr = float(np.corrcoef(dd_i, dd_o)[0, 1])
            keep = 1.0 - float(np.var(dd_o - slope * dd_i) / max(np.var(dd_o), 1e-12))
            print(f"        细节带: 斜率 {slope:+6.4f}  相关 {corr:+6.3f}  "
                  f"线性解释 {keep * 100:6.2f}%")

            # 判据统一成「残差 RMS / 输出自身的噪声底」,接近 1 才算解释到噪声底。
            floor = float(np.median([mad(detail(do[s])) for s in tiles]))
            print("        单候选残差(RMS / 噪声底):")
            singles = {"原样": di, "盒 k=21": box(di, 21)}
            singles.update({f"高斯 σ={s}": gauss(di, s) for s in (2, 4, 8)})
            singles.update({f"金字塔 {n} 级": pyramid(di, n) for n in (2, 3, 4)})
            for cname, c in singles.items():
                res = np.concatenate([(do[s] - c[s]).ravel() for s in tiles])
                rms = float(np.sqrt(np.mean(res ** 2)))
                print(f"          {cname:<12} {rms:8.2f} / {floor:5.2f} = "
                      f"{rms / max(floor, 1e-9):7.1f}×")

            cols = [di, box(di, 9), box(di, 21), np.ones_like(di)]
            A = np.stack([np.concatenate([c[s].ravel() for s in tiles]) for c in cols], 1)
            coef, *_ = np.linalg.lstsq(A, flat_t, rcond=None)
            r2 = 1.0 - float(np.var(flat_t - A @ coef) / max(np.var(flat_t), 1e-12))
            print(f"        线性混合 a={coef[0]:+6.3f} b(k9)={coef[1]:+6.3f} "
                  f"c(k21)={coef[2]:+6.3f} d={coef[3]:+8.2f}   R² {r2 * 100:6.2f}%")

            # 分档要看**残差绝对值**,不能看 R²:在 |色差| 相近的一档里目标方差本来就小,
            # R² 会塌到 0 附近(t17 的 B−G 高饱和档只有 5.5%,而整体 98.67%)——
            # 那是方差划分的假象,不是模型失效。判据取「残差 RMS 对上输出自身的细节」:
            # 接近 1 就说明模型已经解释到噪声底了。
            key = np.array([np.median(np.abs(di[s])) for s in tiles])
            order = np.argsort(key)
            parts = []
            for gname, grp in zip(("低饱和", "中", "高饱和"), np.array_split(order, 3)):
                t = np.concatenate([do[tiles[k]].ravel() for k in grp])
                pred = np.stack([np.concatenate([c[tiles[k]].ravel() for k in grp])
                                 for c in cols], 1) @ coef
                rms = float(np.sqrt(np.mean((t - pred) ** 2)))
                dnoise = np.median([mad(detail(do[tiles[k]])) for k in grp])
                parts.append(f"{gname} 残差 {rms:7.2f} / 噪声底 {dnoise:5.2f} "
                             f"= {rms / max(dnoise, 1e-9):5.1f}×  (|色差| "
                             f"{np.median(key[grp]):6.0f})")
            for p in parts:
                print("          " + p)

            # H2:各半径盒低通(整块)
            best = max(((k, explained(do, box(di, k))) for k in (3, 5, 7, 9, 13, 17, 25)),
                       key=lambda t: t[1])
            print(f"        盒低通最佳 k={best[0]:2d} 解释 {best[1] * 100:6.2f}%   "
                  f"(原样 {explained(do, di) * 100:6.2f}%,  常数 0 "
                  f"{explained(do, np.zeros_like(do)) * 100:6.2f}%)")
            # H4:整体缩放?
            s = float((do * di).sum() / max((di * di).sum(), 1e-12))
            print(f"        最佳整体缩放 {s:6.3f} 解释 {explained(do, s * di) * 100:6.2f}%")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
