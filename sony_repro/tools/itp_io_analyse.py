r"""拿 `tiles_ITPio.npz` 回答两条挂了很久的未决(notes §3 的 ⚠️ 与 §5 的 ⚠️)。

1. **ITP 的输入是不是马赛克**。按 Bayer 四相位统计:马赛克的四相位均值会呈典型
   RGGB(两个绿相接近、红蓝各自偏离),而且**奇偶间距的差**比同相位间距的差大得多。
   这条直接判普查那份调用顺序是不是数据流顺序。
2. **低色差是 ITP 自己做的,还是上游 RawNR 做完它只是没破坏**。§2.1 只证明了低
   色差在 ITP 出口就已存在,分不开。现在入口出口都有了,直接比。

⚠️ 这份 dump 是**预览路径**的(§2.19.2 的教训:预览的值域被压过,不能拿来做端到端
数值比对)。但这里问的两件事都是**结构性**的 —— 输入是不是马赛克、色差在这一级
掉了多少倍 —— 预览数据答得了。要落地数值仍需全分辨率导出时的捕获。

用法::

    uv run python itp_io_analyse.py [tiles_ITPio.npz]
"""
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import numpy as np

SCR = os.path.dirname(os.path.abspath(__file__))


def highpass(a):
    """减去 3x3 均值 —— 留下噪声,去掉结构。"""
    a = a.astype(np.float64)
    k = np.zeros_like(a)
    n = np.zeros_like(a)
    for dy in (-1, 0, 1):
        for dx in (-1, 0, 1):
            k += np.roll(np.roll(a, dy, 0), dx, 1)
            n += 1.0
    return a - k / n


def phases(m):
    """马赛克按 2x2 拆四相位。"""
    return {"(0,0)": m[0::2, 0::2], "(0,1)": m[0::2, 1::2],
            "(1,0)": m[1::2, 0::2], "(1,1)": m[1::2, 1::2]}


def black_level(src):
    """入口马赛克是**黑电平未减**的(§2.15),出口显然已减。不扣掉,入口的相对噪声
    会被系统性低估约 1.9 倍 —— 足以凭空造出「ITP 放大了噪声」这个结论。"""
    import glob
    stem = os.path.basename(str(src).replace("\\", "/"))
    for d in ("/home/jannchie/llr/samples", "/home/jannchie/llr/tmp"):
        for p in glob.glob(os.path.join(d, stem)):
            try:
                import rawpy
                with rawpy.imread(p) as raw:
                    lv = list(raw.black_level_per_channel)
                    print(f"  黑电平(读自 {p}): {lv}")
                    return float(np.mean(lv))
            except Exception as e:
                print(f"  ⚠️ 读黑电平失败({p}): {e}")
    print("  ⚠️ 找不到源 ARW,黑电平按 512 计 —— 这是假设,不是实测")
    return 512.0


def chroma_stats(rgb, label):
    """半分辨率 RGB 上的亮度/色差噪声幅度。

    两处必要的归一,不做就得到假结论:

    * 各通道**先除以自身均值**。入口马赛克还没做白平衡(这张上 R/G/B 均值
      1070/1155/675),出口又过了 ITP 的通道系数,两边的"色差"根本不同量纲。
    * 两边用**同样的相位抽样**,谁都不许额外平滑。第一版给出口做了 2x2 平均,
      那本身就降噪约 2 倍 —— 结果两块 tile 给出 1.30 与 2.27 倍,连"亮度也被压了
      3.12 倍"这种不可能的读数都出来了。
    """
    r, g, b = (x / max(float(np.mean(x)), 1e-9) for x in rgb)
    y = (r + g + g + b) / 4.0
    cb, cr = b - y, r - y
    hy, hcb, hcr = highpass(y), highpass(cb), highpass(cr)
    # 只统计平坦区:按 |局部梯度| 取最平的一半,避免边缘主导
    grad = np.abs(np.diff(y, axis=0, prepend=y[:1])) + \
        np.abs(np.diff(y, axis=1, prepend=y[:, :1]))
    m = grad <= np.median(grad)
    sy, sc = float(hy[m].std()), float(np.hypot(hcb[m], hcr[m]).std())
    print(f"    {label:<26} 亮度噪声 {sy:8.3f}   色差噪声 {sc:8.3f}"
          f"   色差/亮度 {sc / max(sy, 1e-9):6.3f}")
    return sy, sc


def bilinear_demosaic(m):
    """最普通的双线性 demosaic(RGGB),只作**对照**用。

    没有它,「ITP 把色差压了 1.85 倍」这个读数是没法解释的:任何 demosaic 的插值
    都会顺带低通掉一部分色差噪声。要知道这 1.85 倍里有多少是 ITP 自己的本事,
    就得先量出一台没有任何降噪意图的 demosaic 能压多少。
    """
    m = m.astype(np.float64)
    R = np.zeros_like(m)
    G = np.zeros_like(m)
    B = np.zeros_like(m)
    R[0::2, 0::2] = m[0::2, 0::2]
    G[0::2, 1::2] = m[0::2, 1::2]
    G[1::2, 0::2] = m[1::2, 0::2]
    B[1::2, 1::2] = m[1::2, 1::2]

    def sm(a, mask, ker):
        num = np.zeros_like(a)
        den = np.zeros_like(a)
        for (dy, dx), wgt in ker:
            num += wgt * np.roll(np.roll(a, dy, 0), dx, 1)
            den += wgt * np.roll(np.roll(mask, dy, 0), dx, 1)
        out = a.copy()
        fill = den > 0
        out[fill & ~mask.astype(bool)] = (num / np.maximum(den, 1e-9))[
            fill & ~mask.astype(bool)]
        return out

    cross = [((-1, 0), 1.0), ((1, 0), 1.0), ((0, -1), 1.0), ((0, 1), 1.0)]
    diag = [((-1, -1), 1.0), ((-1, 1), 1.0), ((1, -1), 1.0), ((1, 1), 1.0)]
    mG = np.zeros_like(m)
    mG[0::2, 1::2] = 1
    mG[1::2, 0::2] = 1
    mR = np.zeros_like(m)
    mR[0::2, 0::2] = 1
    mB = np.zeros_like(m)
    mB[1::2, 1::2] = 1
    G = sm(G, mG, cross)
    # R/B 先补对角(在异色位点),再补十字 —— 两步就够,这里只要个对照
    R = sm(sm(R, mR, diag), (mR + np.roll(np.roll(mR, 1, 0), 1, 1)) > 0, cross)
    B = sm(sm(B, mB, diag), (mB + np.roll(np.roll(mB, -1, 0), -1, 1)) > 0, cross)
    return R, G, B


def samp2(a):
    """按相位抽样(不是平均)—— 与马赛克拆相位落在同一个采样格,且不额外平滑。"""
    return a[0::2, 0::2].astype(np.float64)


def main() -> int:
    path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(SCR, "tiles_ITPio.npz")
    z = np.load(path, allow_pickle=True)
    blk = 512.0
    if "source" in z:
        print(f"来源: {list(z['source'])}")
        blk = black_level(z["source"][0])
    print()
    rows = []

    for key in sorted(k for k in z.files if k.endswith("_in")):
        t = key[:-3]
        a_in = z[key][:, :, 0]
        a_out = z[f"{t}_out"]
        print(f"=== {t}  入口 {a_in.shape}  出口 {a_out.shape}")

        # 抓下来的是**整个平面,含外扩边界**;task 里记着有效矩形。不裁就把填充区
        # 一起统计进去了 —— 两块 tile 会给出互相矛盾的读数(实测 0.30 倍对 0.76 倍)。
        meta = z[f"{t}_meta"]
        outer = meta[0x18 // 4:0x24 // 4 + 1]
        valid = meta[0x30 // 4:0x3c // 4 + 1]
        place = meta[0x48 // 4:0x54 // 4 + 1]
        # 三个矩形的语义(实测):外扩是**全局** x0,y0,x1,y1(宽高正好等于抓到的
        # 平面);有效是**平面局部** x0,y0,x1,y1;位置是这块 tile 在整幅里的落点。
        print(f"  0) 外扩 {list(outer)}  有效 {list(valid)}  位置 {list(place)}"
              f"  -> 裁 [{valid[1]}:{valid[3]}, {valid[0]}:{valid[2]}]")
        x0, y0, x1, y1 = (int(v) for v in valid)
        if (x0 | y0) & 1:
            raise SystemExit(f"有效矩形原点 ({x0},{y0}) 是奇数,裁下去会错开 Bayer 相位")
        a_in = a_in[y0:y1, x0:x1]
        a_out = a_out[y0:y1, x0:x1]

        # ---- 问题 1:入口是不是马赛克
        ph = phases(a_in)
        mus = {k: float(v.mean()) for k, v in ph.items()}
        print("  1) 入口四相位均值: "
              + "  ".join(f"{k}={v:8.2f}" for k, v in mus.items()))
        # 奇偶间距的差 vs 同相位间距的差。马赛克上前者远大于后者。
        d_adj = float(np.abs(np.diff(a_in.astype(np.float64), axis=1)).std())
        d_same = float(np.abs(a_in[:, 2:].astype(np.float64)
                              - a_in[:, :-2].astype(np.float64)).std())
        print(f"     相邻列差 std {d_adj:8.3f}   隔一列差 std {d_same:8.3f}"
              f"   比值 {d_adj / max(d_same, 1e-9):5.2f}")
        spread = (max(mus.values()) - min(mus.values())) / max(np.mean(list(mus.values())), 1e-9)
        verdict = ("是马赛克" if d_adj > d_same * 1.3 or spread > 0.15
                   else "不像马赛克(四相位彼此接近)")
        print(f"     -> {verdict}(四相位相对离散 {spread:.3f})")

        # ---- 问题 2:出口三平面之间是什么关系
        o = [a_out[:, :, k].astype(np.float64) for k in range(a_out.shape[2])]
        print("  2) 出口三平面均值: "
              + "  ".join(f"p{k}={v.mean():8.2f}" for k, v in enumerate(o)))
        c01 = float(np.corrcoef(o[0].ravel(), o[1].ravel())[0, 1])
        c21 = float(np.corrcoef(o[2].ravel(), o[1].ravel())[0, 1])
        print(f"     相关 p0~p1 {c01:.4f}   p2~p1 {c21:.4f}")

        # ---- 问题 3:色差噪声,入口 vs 出口(同一个网格)
        print("  3) 色差噪声(半分辨率同网格,取最平的一半像素):")
        # 绿色只取**一个**相位:两个绿相一平均就先降了 sqrt(2) 的绿噪声,
        # 出口那边没有对应的操作,平均掉就把优势算到 ITP 头上了。
        r = ph["(0,0)"].astype(np.float64) - blk
        g = ph["(0,1)"].astype(np.float64) - blk
        b = ph["(1,1)"].astype(np.float64) - blk
        n = min(r.shape[0], g.shape[0], b.shape[0]), min(r.shape[1], g.shape[1], b.shape[1])
        # 入口这一行只为**留痕**:它的读数不参与判据(见下面的 ⚠️),但打出来能让
        # 「入口比值跟着内容跳」这件事一眼可见。
        chroma_stats((r[:n[0], :n[1]], g[:n[0], :n[1]], b[:n[0], :n[1]]),
                     "入口(马赛克按相位)")
        oo = [samp2(x) for x in o]
        sy_o, sc_o = chroma_stats((oo[0], oo[1], oo[2]), "出口(三平面同相位抽样)")
        # 对照:同一份入口马赛克,一台没有降噪意图的双线性 demosaic
        bl = [samp2(x) for x in bilinear_demosaic(a_in.astype(np.float64) - blk)]
        sy_b, sc_b = chroma_stats((bl[0], bl[1], bl[2]), "对照(双线性 demosaic)")
        r_o, r_b = sc_o / max(sy_o, 1e-9), sc_b / max(sy_b, 1e-9)
        # ⚠️ **基准是双线性,不是入口。** 入口的 R/G/B 取自相差 1 像素的不同位置,
        # 有结构的地方那个空间错位会被记成"色差" —— 实测入口的色差/亮度比在六块
        # tile 上从 0.21 跳到 1.42,跟着内容走;同位置 RGB 的双线性稳定在 0.74~1.43。
        # 拿入口当基准,得到的"ITP 压了几倍"是内容的函数,不是算子的性质。
        print(f"     -> 对双线性:色差/亮度比 {r_b:.3f} -> {r_o:.3f}"
              f",低 {r_b / max(r_o, 1e-9):5.2f} 倍"
              f";亮度高频 ITP 是双线性的 {sy_o / max(sy_b, 1e-9):5.2f} 倍")
        print()
        rows.append((t, r_b, r_o, sy_o / max(sy_b, 1e-9)))

    if rows:
        print("  汇总(对双线性):")
        for t, rb, ro, ly in rows:
            print(f"    {t}  色差/亮度 {rb:.3f} -> {ro:.3f}  低 {rb / max(ro, 1e-9):5.2f} 倍"
                  f"   亮度高频 {ly:5.2f} 倍")
        f = [rb / max(ro, 1e-9) for _, rb, ro, _ in rows]
        ly = [x for *_, x in rows]
        print(f"    中位:色差/亮度比低 {np.median(f):.2f} 倍,亮度高频 {np.median(ly):.2f} 倍")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
