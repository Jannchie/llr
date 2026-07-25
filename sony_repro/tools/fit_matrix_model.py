"""检验 LinearMatrix16 标定表的生成模型。

假设 A:同一创意外观下,表 = 两个光源端点表的线性插值 → 去中心后应为 1 维。
假设 B:插值系数由白平衡(色温)决定。

用法: python tools/fit_matrix_model.py <batch_dir> <arw_dir>
"""
import sys
import os
import glob
import json
import subprocess
from collections import defaultdict

import numpy as np
import rawpy

BATCH, ARWDIR = sys.argv[1], sys.argv[2]

recs = {}
for f in sorted(glob.glob(os.path.join(BATCH, "*.npz"))):
    tag = os.path.splitext(os.path.basename(f))[0]
    z = np.load(f)
    recs[tag] = np.round(z["coef"] * 1024).astype(np.int64)

arws = [os.path.join(ARWDIR, t + ".ARW") for t in recs if
        os.path.exists(os.path.join(ARWDIR, t + ".ARW"))]
out = subprocess.run(["exiftool", "-j", "-n", "-CreativeStyle", "-ColorTemperature"] + arws,
                     capture_output=True, text=True).stdout
meta = {os.path.splitext(os.path.basename(m["SourceFile"]))[0]: m for m in json.loads(out)}

wb = {}
for t in recs:
    p = os.path.join(ARWDIR, t + ".ARW")
    if not os.path.exists(p):
        continue
    with rawpy.imread(p) as raw:
        w = np.asarray(raw.camera_whitebalance, dtype=float)[:3]
    wb[t] = w / max(w[1], 1e-9)

by_style = defaultdict(list)
for t in recs:
    if t in wb and t in meta:
        by_style[str(meta[t].get("CreativeStyle"))].append(t)

for style, members in sorted(by_style.items()):
    Y = np.array([recs[t].ravel() for t in members], dtype=float)
    W = np.array([wb[t] for t in members])
    uniq, idx = np.unique(Y, axis=0, return_index=True)
    print(f"\n===== CreativeStyle {style}:{len(members)} 张,{len(uniq)} 张不同表 =====")
    if len(uniq) < 2:
        continue

    mu = uniq.mean(axis=0)
    U, s, Vt = np.linalg.svd(uniq - mu, full_matrices=False)
    var = s ** 2 / (s ** 2).sum()
    print("  PCA 方差占比: " + "  ".join(f"{v:.5f}" for v in var[:6]))

    # 1 维重建误差
    for k in (1, 2, 3):
        if k > len(s):
            break
        rec = mu + (U[:, :k] * s[:k]) @ Vt[:k]
        err = np.abs(rec - uniq)
        print(f"  用 {k} 个成分重建: 中位 {np.median(err):6.3f}  "
              f"p95 {np.percentile(err,95):7.3f}  max {err.max():8.3f}  (Q10 单位)")

    # 主成分坐标 vs 白平衡
    T = (Y - mu) @ Vt[0]
    rg, bg = W[:, 0], W[:, 2]
    for nm, v in (("R/G", rg), ("B/G", bg), ("R/B", rg / np.maximum(bg, 1e-9)),
                  ("log(R/B)", np.log(rg / np.maximum(bg, 1e-9)))):
        if np.std(v) > 1e-12:
            print(f"  corr(PC1, {nm:>8}) = {np.corrcoef(T, v)[0,1]:+.4f}")

    # 用 R/B 单调映射预测 PC1
    if len(members) >= 6:
        x = rg / np.maximum(bg, 1e-9)
        for deg in (1, 2, 3):
            c = np.polyfit(x, T, deg)
            e = np.abs(np.polyval(c, x) - T)
            print(f"  PC1 ← poly{deg}(R/B): 残差中位 {np.median(e):.3f} max {e.max():.3f}")

    # 端点表(PC1 两端)
    if var[0] > 0.9:
        lo, hi = uniq[np.argmin(U[:, 0] * s[0])], uniq[np.argmax(U[:, 0] * s[0])]
        print("  → 近似 1 维,两端表(Q10):")
        print(f"    lo m01: {lo.reshape(6,16)[0].astype(int).tolist()}")
        print(f"    hi m01: {hi.reshape(6,16)[0].astype(int).tolist()}")
