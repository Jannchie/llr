r"""已被 `spica_model.py` 取代 —— 那份是从反汇编逐条对出来的,逐位 100%。
本文件停在拟合模型的 75.6%,只作为解题过程留档。

用 Spica 的入口平面重建出口平面 —— 整级的最终验收。

要素齐了(全部 100% 验证过,见 PIPELINE §7.11.4.2):
* 分类:9 点 LBP -> D2 轨道代表元 -> 排名 = idx (1..99)
* 方向:取到代表元的那个群元 g,e/H/V/HV -> (dx,dy) = (1,1)/(-1,1)/(1,-1)/(-1,-1)
* 权重:`spica_wtab.json` 的 99 张表,每张 25 个整数,和恒为 512

**tap 几何是半径 3 的菱形**(`|dy| + |dx| <= 3`,行数 1+3+5+7+5+3+1 = 25),
row-major 编号 —— 由 `spica_match.py` 把 49 个网格位置和 25 个 tap 按「跨 idx 成
比例」配对定出,25/25 相关系数都 > 0.9(多数 > 0.97)。采样网格按 `(dx,dy)` 翻转,
因为核存的是代表元形状:
    tap i = (dy_i, dx_i) 取 I[y + dy_i * sy,  x + dx_i * sx]

这里只做**无偏的对照**:算出 conv = Σ w·I / 512,然后看 out 和 (in, conv) 是什么
关系 —— 是直接替换、是加权混合、还是过了限幅。不预设答案。

用法: python spica_recon.py [tiles_SIMDSpica.npz]
"""
import json
import os
import sys

import numpy as np

SCR = os.path.dirname(os.path.abspath(__file__))
PLANE_WHITE = 16383.0
STEP = PLANE_WHITE / 255.0          # 一个 8-bit 级差
MARGIN = 8

# 半径 3 的菱形,row-major。顺序就是权重表里 tap 0..24 的顺序。
TAPS = [(dy, dx) for dy in range(-3, 4) for dx in range(-3, 4)
        if abs(dy) + abs(dx) <= 3]
assert len(TAPS) == 25


def _swap(m, a, b):
    x, y = (m >> a) & 1, (m >> b) & 1
    return (m & ~((1 << a) | (1 << b))) | (x << b) | (y << a)


def _H(m):
    return _swap(_swap(m, 2, 6), 3, 5)


def _V(m):
    return _swap(_swap(m, 0, 8), 1, 7)


def _fold(m):
    return (~m & 0xFF) if (m & 0x100) else m


def build_lut():
    """M(0..511) -> (idx, dx, dy)。idx=0 表示该 M 不可能出现。"""
    group = [(_fold, 1, 1), (lambda m: _fold(_H(m)), -1, 1),
             (lambda m: _fold(_V(m)), 1, -1),
             (lambda m: _fold(_H(_V(m))), -1, -1)]
    reps = sorted({min(f(m) for f, _, _ in group) for m in range(512)} - {0})
    rank = {r: i + 1 for i, r in enumerate(reps)}
    lut = np.zeros((512, 3), np.int32)
    for m in range(512):
        vals = [f(m) for f, _, _ in group]
        best = min(vals)
        g = vals.index(best)                    # 顺序即 e > H > V > HV 的优先级
        lut[m] = (rank.get(best, 0), group[g][1], group[g][2])
    return lut


def classify(img):
    """9 位 LBP。img 是 float64 的整幅平面,返回同形状的 int32。"""
    def s(dy, dx):
        return np.roll(np.roll(img, -dy, 0), -dx, 1)
    pts = [s(-2, 0), s(-1, 0), s(0, -2), s(0, -1), s(0, 0),
           s(0, 1), s(0, 2), s(1, 0), s(2, 0)]
    st = np.stack(pts)
    lo, hi = st.min(0), st.max(0)
    thr = (hi - lo) // 2                        # 整数平面,与引擎的有符号除 2 一致
    m = np.zeros(img.shape, np.int32)
    for b, p in enumerate(pts):
        m |= ((p - lo) >= thr).astype(np.int32) << b
    return m


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(SCR, "tiles_SIMDSpica.npz")
    with open(os.path.join(SCR, "spica_wtab.json"), encoding="utf-8") as f:
        wt = json.load(f)["tables"]
    W = np.zeros((100, 25), np.float64)
    for k, v in wt.items():
        W[int(k)] = v
    lut = build_lut()

    z = np.load(path)
    print("来源:", list(z["source"]) if "source" in z else "(未记录)")
    tiles = sorted(int(k[1:].split("_")[0]) for k in z if k.endswith("_in"))

    for t in tiles:
        a = z[f"t{t}_in"][..., 0].astype(np.float64)
        b = z[f"t{t}_out"][..., 0].astype(np.float64)
        m = classify(a)
        idx, dx, dy = lut[m, 0], lut[m, 1], lut[m, 2]

        # 四个朝向各做一遍全图卷积,再按朝向取用 —— 全向量化,比逐像素快得多
        conv = np.zeros(a.shape, np.float64)
        for sy in (1, -1):
            for sx in (1, -1):
                acc = np.zeros(a.shape, np.float64)
                for i, (ty, tx) in enumerate(TAPS):
                    sh = np.roll(np.roll(a, -ty * sy, 0), -tx * sx, 1)
                    acc += W[idx, i] * sh
                sel = (dy == sy) & (dx == sx)
                conv[sel] = acc[sel] / 512.0
        # 平坦区(hi==lo -> M=511 -> 代表元 0 -> idx=0,没有对应的表)引擎根本不调用
        # 这个函数 —— 1500 个真实样本里 M=511 零个,最小动态范围 32。原样通过。
        conv[idx == 0] = a[idx == 0]

        v = slice(MARGIN, -MARGIN)
        A, B, C = a[v, v], b[v, v], conv[v, v]
        d_engine, d_conv = B - A, C - A
        print(f"\n=== tile {t} ===  idx 覆盖 {len(np.unique(idx[v, v]))} 种")
        print(f"  引擎改动量  RMS {np.sqrt((d_engine ** 2).mean()) / STEP:7.4f} 个 8-bit 级"
              f"   极值 [{d_engine.min():+7.1f}, {d_engine.max():+7.1f}]")
        print(f"  卷积-原图   RMS {np.sqrt((d_conv ** 2).mean()) / STEP:7.4f} 个 8-bit 级"
              f"   极值 [{d_conv.min():+7.1f}, {d_conv.max():+7.1f}]")
        # 直接替换?
        r = np.abs(C - B)
        print(f"  |卷积 - 引擎出口|  最大 {r.max():8.2f}  RMS {np.sqrt((r ** 2).mean()):8.3f}"
              f"   逐位相同 {100 * (r == 0).mean():.4f}%")
        # 线性关系:out - in = k * (conv - in) ?  k 就是那个混合权重
        den = (d_conv ** 2).sum()
        if den > 0:
            k = (d_conv * d_engine).sum() / den
            resid = d_engine - k * d_conv
            print(f"  最小二乘 out-in = k*(conv-in):  k = {k:.6f}"
                  f"   残差 RMS {np.sqrt((resid ** 2).mean()) / STEP:.4f} 个 8-bit 级")
            corr = np.corrcoef(d_conv.ravel(), d_engine.ravel())[0, 1]
            print(f"  相关系数 {corr:.6f}")


if __name__ == "__main__":
    main()
