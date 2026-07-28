r"""把「有效核的 49 个网格位置」和「权重表的 25 个 tap」对上,同时定出混合系数 k。

`spica_solve.py` 解出的是有效核 `coef ≈ k·(w/512 - δ)`。要还原纯的 `w`,需要知道
网格位置 p 对应表里的哪个 tap i。**判据是成比例而不是绝对值**:

    若 p 对应 i,则跨所有 idx 都有  coef_idx[p]  ∝  w_idx[i]

拿 N 个 idx 各解一次,每个网格位置就有一条 N 维向量,每个表位置也有一条 N 维向量,
成比例的那一对相关系数接近 1。这比「取 |系数| 最大的 25 个」可靠得多 —— 后者会
被「某些 idx 的某个权重恰好接近 0」骗到(实测 10 个 idx 的 top25 交集只有 20 个)。

中心位置要单独处理:它多一个 `-δ`,即 `coef[中心] = k·(w[中心]/512 - 1)`。

用法: python spica_match.py [--nidx 16]
"""
import collections
import json
import os
import sys

import numpy as np

SCR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCR)
from spica_recon import MARGIN, build_lut, classify  # noqa: E402

R = 3
G = 2 * R + 1


def _opt(flag, default):
    return type(default)(sys.argv[sys.argv.index(flag) + 1]) if flag in sys.argv else default


def solve_kernels(nidx):
    """对样本最多的 nidx 个 idx 各解一次有效核。返回 {idx: 49 维系数}。"""
    lut = build_lut()
    z = np.load(os.path.join(SCR, "tiles_SIMDSpica.npz"))
    tiles = sorted(int(k[1:].split("_")[0]) for k in z if k.endswith("_in"))
    pre = []
    for t in tiles:
        a = z[f"t{t}_in"][..., 0].astype(np.float64)
        b = z[f"t{t}_out"][..., 0].astype(np.float64)
        m = classify(a)
        pre.append((a, b - a, lut[m, 0], lut[m, 1], lut[m, 2]))

    cnt = collections.Counter()
    for _, _, idx, dx, dy in pre:
        v = slice(MARGIN, -MARGIN)
        sub = idx[v, v][(dx[v, v] == 1) & (dy[v, v] == 1)]
        u, c = np.unique(sub, return_counts=True)
        cnt.update(dict(zip(u.tolist(), c.tolist())))
    top = [k for k, _ in cnt.most_common(nidx + 4) if k > 0][:nidx]

    out = {}
    for want in top:
        X, Y = [], []
        for a, d, idx, dx, dy in pre:
            sel = np.zeros(a.shape, bool)
            sel[MARGIN:-MARGIN, MARGIN:-MARGIN] = True
            sel &= (idx == want) & (dx == 1) & (dy == 1) & (np.abs(d) < 120)
            ys, xs = np.nonzero(sel)
            if len(ys) == 0:
                continue
            if len(ys) > 40000:
                k = np.random.default_rng(0).choice(len(ys), 40000, replace=False)
                ys, xs = ys[k], xs[k]
            X.append(np.stack([a[ys + i, xs + j] for i in range(-R, R + 1)
                               for j in range(-R, R + 1)], 1))
            Y.append(d[ys, xs])
        if not X or sum(len(x) for x in X) < 5000:
            continue
        X, Y = np.concatenate(X), np.concatenate(Y)
        X = np.hstack([X, np.ones((len(X), 1))])
        coef, *_ = np.linalg.lstsq(X, Y, rcond=None)
        out[want] = coef[:-1]
    return out


def main():
    nidx = _opt("--nidx", 16)
    K = solve_kernels(nidx)
    with open(os.path.join(SCR, "spica_wtab.json"), encoding="utf-8") as f:
        wt = {int(k): v for k, v in json.load(f)["tables"].items()}
    ids = sorted(K)
    print(f"用 {len(ids)} 个 idx 求解: {ids}\n")

    C = np.stack([K[i] for i in ids])                 # (N, 49)
    W = np.stack([wt[i] for i in ids]).astype(float)  # (N, 25)

    # 中心先定出来:它是唯一含 -δ 的位置,coef = k·(w/512 - 1)。
    # 对每个候选 (p, i) 解 k,再看这个 k 能不能同时解释其余位置 —— 先用最强的
    # 那个网格位置当中心(有效核里它的量级远超其他)。
    pc = int(np.argmax(np.abs(C).mean(0)))
    print(f"中心网格位置 = {(pc // G - R, pc % G - R)}"
          f"   平均 |系数| {np.abs(C[:, pc]).mean():.4f}")

    # 表里的中心 tap:同样取平均权重最大的那个
    ic = int(np.argmax(W.mean(0)))
    k_est = float(np.mean(C[:, pc] / (W[:, ic] / 512.0 - 1.0)))
    print(f"表里的中心 tap = 索引 {ic}   估出 k = {k_est:.6f}"
          f"   (逐 idx 的散布 {np.std(C[:, pc] / (W[:, ic] / 512.0 - 1.0)):.6f})\n")

    # 其余位置:相关系数矩阵,行 = 网格位置,列 = 表 tap
    def corr(u, v):
        u, v = u - u.mean(), v - v.mean()
        d = np.linalg.norm(u) * np.linalg.norm(v)
        return float(u @ v / d) if d > 1e-12 else 0.0

    print("表 tap -> 最匹配的网格位置(相关系数 / 由该对估出的 k):")
    used, rows = {}, []
    for i in range(25):
        if i == ic:
            rows.append((i, pc, 1.0, k_est))
            used[pc] = i
            continue
        best = None
        for p in range(G * G):
            if p == pc or p in used:
                continue
            c = corr(C[:, p], W[:, i])
            if best is None or c > best[0]:
                best = (c, p)
        c, p = best
        kk = float(np.mean(C[:, p] / (W[:, i] / 512.0))) if np.all(W[:, i] != 0) else float("nan")
        used[p] = i
        rows.append((i, p, c, kk))

    for i, p, c, kk in rows:
        print(f"  tap {i:2d} -> ({p // G - R:+d},{p % G - R:+d})   r = {c:+.4f}   k = {kk:.4f}")

    good = [r for r in rows if r[2] > 0.9]
    print(f"\n相关系数 > 0.9 的匹配: {len(good)}/25")
    kk = [r[3] for r in good if np.isfinite(r[3])]
    if kk:
        print(f"  这些匹配估出的 k:中位 {np.median(kk):.4f}  "
              f"均值 {np.mean(kk):.4f}  标准差 {np.std(kk):.4f}")


if __name__ == "__main__":
    main()
