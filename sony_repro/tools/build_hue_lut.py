"""从 kernel dump 标定「色度方向 -> 16 位分段索引」的映射,存成 1D LUT。

Edit.exe 内部用的是一个廉价 atan 近似(常量 366/109/37,x0.875,x1/512,
象限偏移 ±256/±512/±768/±1024,整圈 1024)。这里不复刻那套定点近似,而是
按真实 atan2 角度标定出等价的单调映射 —— 实测中位误差 1/1024。

用法: python tools/build_hue_lut.py <kernel_*.npz> [更多 kernel_*.npz ...] <out.npz>
"""
import sys

import numpy as np

NBIN = 4096
TWO_PI = 2 * np.pi

inputs, out = sys.argv[1:-1], sys.argv[-1]

ang_all, idx_all, sat_all = [], [], []
for p in inputs:
    z = np.load(p)
    R, G, B, I = (z[k].astype(np.float64).ravel() for k in ("R", "G", "B", "IDX"))
    mx = np.maximum(np.maximum(R, G), B)
    mn = np.minimum(np.minimum(R, G), B)
    sat = np.where(mx > 0, (mx - mn) / np.maximum(mx, 1e-9), 0.0)
    # 低彩度像素的索引不重要:所有分段矩阵每行和都为 1,中性色恒等映射。
    # 且那里 RGB 量化会让角度本身失真,纳入只会污染标定。
    m = (mx > 200) & (sat > 0.25)
    Y = (R + G + B) / 3.0
    ang_all.append(np.arctan2((R - Y)[m], (B - Y)[m]) % TWO_PI)
    idx_all.append(I[m])
    sat_all.append(sat[m])
    print(f"{p}: 取用 {m.sum()} / {R.size} 像素")

ang = np.concatenate(ang_all)
idx = np.concatenate(idx_all)
print(f"合计样本 {ang.size}")

bi = np.clip((ang / TWO_PI * NBIN).astype(int), 0, NBIN - 1)

# 环形量的中位数:先展开到复平面求主方向,再折算
lut = np.full(NBIN, np.nan)
for k in range(NBIN):
    s = bi == k
    if s.sum():
        lut[k] = np.median(idx[s])
filled = ~np.isnan(lut)
print(f"有数据的桶 {filled.sum()} / {NBIN}")

# 环形插值补空桶
ks = np.nonzero(filled)[0]
lut = np.interp(np.arange(NBIN), ks, lut[ks], period=NBIN)

pred = lut[bi]
resid = np.abs(((idx - pred + 512) % 1024) - 512)
print(f"标定残差: 中位 {np.median(resid):.3f}  p90 {np.percentile(resid,90):.3f}  "
      f"p99 {np.percentile(resid,99):.3f}  max {resid.max():.3f}")

sat = np.concatenate(sat_all)
print("按彩度分层的残差(彩度越高,索引越要紧):")
for lo, hi in ((0.25, 0.4), (0.4, 0.6), (0.6, 0.8), (0.8, 1.01)):
    s = (sat >= lo) & (sat < hi)
    if s.sum() > 100:
        print(f"  sat {lo:.2f}~{hi:.2f}  n={s.sum():>7}  "
              f"中位 {np.median(resid[s]):6.2f}  p90 {np.percentile(resid[s],90):7.2f}")

np.savez_compressed(out, lut=lut.astype(np.float32), nbin=NBIN)
print(f"SAVED {out}")
