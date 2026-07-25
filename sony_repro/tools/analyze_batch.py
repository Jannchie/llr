"""分析批量 dump:LinearMatrix16 的 6x16 标定表如何随图变化,与白平衡的关系。

已确认的结构(见 PIPELINE.md):
  - 表位于 lv+0x919c0,6 组 x 16 bin,量化到 1/1024
  - lv+0x91bc0 的 3x3 == 表的 bin 0(逐位相等),故 3x3 只是表的一个切片
  - 表按图重算,不是相机固有标定

本脚本回答:表的自由度有多少?能否由白平衡预测?

用法: python tools/analyze_batch.py <batch_dir> <arw_dir>
"""
import sys
import glob
import os

import numpy as np
import rawpy

BATCH, ARWDIR = sys.argv[1], sys.argv[2]

recs = []
for f in sorted(glob.glob(os.path.join(BATCH, "*.npz"))):
    z = np.load(f)
    recs.append({"tag": os.path.splitext(os.path.basename(f))[0],
                 "coef": z["coef"], "matrix": z["matrix"], "tone": z["tone"]})
print(f"样本 {len(recs)} 张")

C = np.array([np.round(r["coef"] * 1024).astype(np.int64) for r in recs])  # (N,6,16)
flat = C.reshape(len(C), -1)

# --- 1) 去重(连拍会给出完全相同的表) ---
uniq, first_idx, inv = np.unique(flat, axis=0, return_index=True, return_inverse=True)
print(f"去重后不同的表: {len(uniq)} 组")
for k in range(min(len(uniq), 30)):
    members = [recs[i]["tag"] for i in np.nonzero(inv == k)[0]]
    print(f"  表{k:>2}: {len(members):>2} 张  {members[0]}..{members[-1]}")

# --- 2) 自由度:去重后做 PCA ---
X = uniq.astype(float)
mu = X.mean(axis=0)
Xc = X - mu
if len(X) > 1:
    s = np.linalg.svd(Xc, compute_uv=False)
    var = s ** 2 / max((s ** 2).sum(), 1e-12)
    print("\n[PCA] 各主成分方差占比:")
    print("  " + "  ".join(f"{v:.4f}" for v in var[:8]))
    cum = np.cumsum(var)
    k95 = int(np.searchsorted(cum, 0.95)) + 1
    k999 = int(np.searchsorted(cum, 0.999)) + 1
    print(f"  解释 95% 需 {k95} 个成分,99.9% 需 {k999} 个 → 有效自由度")

# --- 3) 每个 bin 的变化幅度 ---
print("\n[各 bin 的取值范围] (Q10)")
names = ["m01", "m02", "m10", "m12", "m20", "m21"]
for j, nm in enumerate(names):
    rng = [f"{C[:, j, b].min()}~{C[:, j, b].max()}" for b in range(16)]
    print(f"  {nm}: " + " ".join(f"{r:>10}" for r in rng[:8]))
    print(f"       " + " ".join(f"{r:>10}" for r in rng[8:]))

# --- 4) 与白平衡的关系 ---
print("\n[白平衡关联]")
wb_rows, keep = [], []
for i, r in enumerate(recs):
    arw = os.path.join(ARWDIR, r["tag"] + ".ARW")
    if not os.path.exists(arw):
        continue
    try:
        with rawpy.imread(arw) as raw:
            wb = np.asarray(raw.camera_whitebalance, dtype=float)[:3]
    except Exception as e:
        print(f"  {r['tag']} 读取失败: {e}")
        continue
    wb_rows.append(wb / max(wb[1], 1e-9))
    keep.append(i)

if len(wb_rows) > 3:
    W = np.array(wb_rows)                    # (M,3) 已按 G 归一
    Y = flat[keep].astype(float)             # (M,96)
    print(f"  可用 {len(W)} 张, R/G∈[{W[:,0].min():.3f},{W[:,0].max():.3f}]  "
          f"B/G∈[{W[:,2].min():.3f},{W[:,2].max():.3f}]")

    # 线性回归: 表 ≈ a + b*(R/G) + c*(B/G)
    A = np.column_stack([np.ones(len(W)), W[:, 0], W[:, 2]])
    sol, *_ = np.linalg.lstsq(A, Y, rcond=None)
    pred = A @ sol
    err = np.abs(pred - Y)
    print(f"  线性(R/G,B/G) 预测残差 Q10: 中位 {np.median(err):.2f}  "
          f"p95 {np.percentile(err, 95):.2f}  max {err.max():.2f}")

    # 加二次项
    A2 = np.column_stack([A, W[:, 0] ** 2, W[:, 2] ** 2, W[:, 0] * W[:, 2]])
    sol2, *_ = np.linalg.lstsq(A2, Y, rcond=None)
    err2 = np.abs(A2 @ sol2 - Y)
    print(f"  二次     预测残差 Q10: 中位 {np.median(err2):.2f}  "
          f"p95 {np.percentile(err2, 95):.2f}  max {err2.max():.2f}")

# --- 5) tone LUT ---
print("\n[tone LUT]")
T = np.array([r["tone"] for r in recs if r["tone"].size == 32768])
if len(T):
    ut = np.unique(T, axis=0)
    print(f"  有效 {len(T)} 条, 去重 {len(ut)} 条")
    print(f"  峰值 {T.max(axis=1).min()}~{T.max(axis=1).max()}")
    print(f"  LUT[1] {T[:,1].min()}~{T[:,1].max()}  LUT[100] {T[:,100].min()}~{T[:,100].max()}")
    nz = (T > 0).sum(axis=1)
    print(f"  非零项数 {nz.min()}~{nz.max()}")
