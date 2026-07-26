r"""Vatr(= DRO)到底是逐像素的还是空间的?

`stage_census.py` 对比两张图给出决定性证据:**DRO=Auto 的图多跑 `ZcTaskVatr` 36 次,
DRO=Off 的一次不跑**,两份普查再无其它差异。所以 **DRO = ZcTaskVatr**,
不是 README/dro.py 里写的 `ZcTaskAreaComp`(那个实测只在色度平面上做楔形修正)。

Vatr 排在 DemosaicRough 之后、GeometricTransformCorrection 之前,
所以三个平面是**线性 R/G/B**,不是 YCC。

这里回答复刻难度的关键问题:同一个输入值出来的是不是同一个输出值?
    是 -> 一条 1D 曲线就够
    否 -> 增益依赖邻域,是空间局部色调映射,离线复刻要重建整个空间部分
"""
import sys
from pathlib import Path

import numpy as np

NPZ = Path(__file__).parent / "stage_frames.npz"


def main():
    z = np.load(sys.argv[1] if len(sys.argv) > 1 else NPZ)
    a, b = z["ZcTaskVatr_in"], z["ZcTaskVatr_out"]
    ok = a.max(-1) > 0
    print(f"像素 {ok.sum()}\n")

    for k, nm in enumerate("RGB"):
        x, y = a[..., k][ok].astype(np.int64), b[..., k][ok].astype(np.int64)
        m = x > 0
        print(f"平面{k} ({nm}) 改动 {100 * (x != y).mean():.2f}%  "
              f"中位增益 {np.median(y[m] / x[m]):.4f}")

    # 逐像素?对每个输入值统计输出的散布
    x, y = a[..., 1][ok].astype(np.int64), b[..., 1][ok].astype(np.int64)   # 绿色通道
    print("\n绿色通道:同一输入值对应的输出散布")
    print("  输入      n        输出中位   输出 p1..p99    单值?")
    for v in (200, 500, 1000, 2000, 4000, 8000):
        m = x == v
        if m.sum() < 200:
            continue
        o = y[m]
        lo, hi = np.percentile(o, [1, 99])
        print(f"  {v:>6} {m.sum():>8}   {np.median(o):>8.0f}   {lo:.0f}..{hi:.0f}"
              f"        {'是' if hi - lo <= 1 else '否 (跨度 %d)' % (hi - lo)}")

    # 若是空间的,增益应该跟**邻域均值**走。用大盒滤波近似局部亮度看相关性
    g_in = a[..., 1].astype(np.float64)
    gain = np.where(g_in > 0, b[..., 1].astype(np.float64) / np.maximum(g_in, 1), 1.0)
    k = 33
    pad = np.pad(g_in, k // 2, mode="edge")
    cs = pad.cumsum(0).cumsum(1)
    cs = np.pad(cs, ((1, 0), (1, 0)))
    box = (cs[k:, k:] - cs[:-k, k:] - cs[k:, :-k] + cs[:-k, :-k]) / (k * k)
    m = ok & (g_in > 100)
    print(f"\n增益 vs 像素自身 相关 {np.corrcoef(gain[m], g_in[m])[0, 1]:+.4f}")
    print(f"增益 vs {k}x{k} 邻域均值 相关 {np.corrcoef(gain[m], box[m])[0, 1]:+.4f}")
    # 两者本来就高度相关,直接比没意义。**扣掉逐像素那部分**再看残差:
    # 若残差仍跟邻域走,增益就依赖邻域 -> 空间算法。
    xs, gs = g_in[m], gain[m]
    bins = np.clip((xs / 32).astype(int), 0, 511)
    med = np.zeros(512)
    for i in range(512):
        s = bins == i
        if s.any():
            med[i] = np.median(gs[s])
    resid = gs - med[bins]
    print(f"\n扣掉「按像素值的中位增益」后:")
    print(f"  残差标准差 {resid.std():.5f}  (原增益标准差 {gs.std():.5f})")
    print(f"  残差 vs 邻域均值 相关 {np.corrcoef(resid, box[m])[0, 1]:+.4f}")
    print(f"  残差 vs 像素自身 相关 {np.corrcoef(resid, xs)[0, 1]:+.4f}")

    # 邻域相关为零 -> 不是空间算法。那残差多半来自「增益由**亮度**驱动」:
    # 保色增益按 RGB 的某个组合算,再乘到三个通道上。逐个权重试一遍。
    rgb = a[ok].astype(np.float64)
    out = b[ok].astype(np.float64)
    lum_gain = np.where(rgb[:, 1] > 0, out[:, 1] / np.maximum(rgb[:, 1], 1), 1.0)
    keep = rgb[:, 1] > 100
    print("\n增益作为「哪个量」的函数最紧?(分箱后的残差标准差,越小越紧)")
    cands = {
        "G 单通道": rgb[:, 1],
        "BT.601 亮度": rgb @ [0.299, 0.587, 0.114],
        "BT.709 亮度": rgb @ [0.2126, 0.7152, 0.0722],
        "Sony 权重": rgb @ (np.array([2432, 4864, 896]) / 8192),
        "RGB 最大值": rgb.max(1),
    }
    for name, drv in cands.items():
        d, g = drv[keep], lum_gain[keep]
        bi = np.clip((d / 32).astype(int), 0, 1023)
        mm = np.zeros(1024)
        for i in np.unique(bi):
            mm[i] = np.median(g[bi == i])
        print(f"  {name:<12} 残差 std {(g - mm[bi]).std():.6f}")

    # 33x33 只有 132 像素跨度(dump 是 STEP=4),抓不到全画幅低频。
    # 把残差按大方块求均值:若块间差异远大于块内抽样噪声,就仍有空间结构。
    rmap = np.full(g_in.shape, np.nan)
    rmap[m] = resid
    bs = 64
    h, w = (np.array(rmap.shape) // bs) * bs
    blocks = rmap[:h, :w].reshape(h // bs, bs, w // bs, bs)
    with np.errstate(invalid="ignore"):
        bmean = np.nanmean(blocks, axis=(1, 3))
        bn = np.sum(~np.isnan(blocks), axis=(1, 3))
    good = bn > 500
    noise = resid.std() / np.sqrt(np.median(bn[good]))
    print(f"\n残差的空间结构({bs}x{bs} 块,{good.sum()} 块):")
    print(f"  块均值标准差 {np.nanstd(bmean[good]):.6f}   抽样噪声 {noise:.6f}"
          f"   {'仍有结构' if np.nanstd(bmean[good]) > 3 * noise else '无结构'}")

    # 曲线本身:按输入值给出增益,这就是要复刻的东西
    print("\n绿色通道的增益曲线:")
    for v in (64, 128, 256, 512, 1024, 2048, 4096, 8192, 12288):
        s = (np.abs(xs - v) < max(4, v // 64))
        if s.sum() > 100:
            print(f"  in {v:>6} -> x{np.median(gs[s]):.4f}  (n={s.sum()})")


if __name__ == "__main__":
    main()
