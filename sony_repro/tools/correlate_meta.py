"""把批量 dump 的 LinearMatrix16 标定表 / tone LUT 与 EXIF 设置关联。

回答两个问题:
  1. tone LUT 由什么决定?(创意外观? DRO?)
  2. 6x16 标定表由什么决定?(创意外观 + 白平衡?)

用法: python tools/correlate_meta.py <batch_dir> <arw_dir>
"""
import sys
import os
import glob
import json
import subprocess
from collections import defaultdict

import numpy as np

BATCH, ARWDIR = sys.argv[1], sys.argv[2]

TAGS = ["CreativeStyle", "DynamicRangeOptimizer", "ColorTemperature", "WhiteBalance",
        "WBShiftAB", "WBShiftGM", "ISO", "PictureEffect", "SonyISO",
        "ColorMode", "Saturation", "Contrast", "Sharpness"]

recs = {}
for f in sorted(glob.glob(os.path.join(BATCH, "*.npz"))):
    tag = os.path.splitext(os.path.basename(f))[0]
    z = np.load(f)
    recs[tag] = {"coef": np.round(z["coef"] * 1024).astype(np.int64),
                 "tone": z["tone"]}

arws = [os.path.join(ARWDIR, t + ".ARW") for t in recs]
arws = [a for a in arws if os.path.exists(a)]
cmd = ["exiftool", "-j", "-n"] + [f"-{t}" for t in TAGS] + arws
meta_list = json.loads(subprocess.run(cmd, capture_output=True, text=True).stdout)
meta = {os.path.splitext(os.path.basename(m["SourceFile"]))[0]: m for m in meta_list}
print(f"样本 {len(recs)},元数据 {len(meta)}\n")

tags = sorted(t for t in recs if t in meta)


def key_of(t, fields):
    return tuple(str(meta[t].get(f)) for f in fields)


def group_report(title, label_fn, value_fn):
    """按 label 分组,检查 value 在组内是否一致、组间是否不同。"""
    groups = defaultdict(list)
    for t in tags:
        groups[label_fn(t)].append(t)
    print(f"=== {title} ===")
    consistent = True
    sigs = {}
    for k in sorted(groups, key=str):
        members = groups[k]
        vals = {value_fn(t).tobytes() for t in members}
        sig = next(iter(vals))
        sigs.setdefault(sig, []).append(k)
        flag = "一致" if len(vals) == 1 else f"**{len(vals)} 种**"
        print(f"  {str(k):<38} {len(members):>3} 张  组内 {flag}")
        if len(vals) > 1:
            consistent = False
    cross = [v for v in sigs.values() if len(v) > 1]
    print(f"  → 组内一致: {consistent};不同组共用同一取值的情况: {len(cross)}")
    for v in cross[:6]:
        print(f"      共用: {v}")
    print()


tone_of = lambda t: recs[t]["tone"]
coef_of = lambda t: recs[t]["coef"]

group_report("tone LUT ~ CreativeStyle", lambda t: key_of(t, ["CreativeStyle"]), tone_of)
group_report("tone LUT ~ CreativeStyle+DRO",
             lambda t: key_of(t, ["CreativeStyle", "DynamicRangeOptimizer"]), tone_of)
group_report("标定表 ~ CreativeStyle", lambda t: key_of(t, ["CreativeStyle"]), coef_of)

# 标定表:创意外观固定时,是否由白平衡唯一决定?
print("=== 标定表 ~ (CreativeStyle, 白平衡) ===")
import rawpy
wb = {}
for t in tags:
    try:
        with rawpy.imread(os.path.join(ARWDIR, t + ".ARW")) as raw:
            w = np.asarray(raw.camera_whitebalance, dtype=float)[:3]
        wb[t] = w / max(w[1], 1e-9)
    except Exception as e:
        print(f"  {t} 读 WB 失败: {e}")

by_style = defaultdict(list)
for t in tags:
    if t in wb:
        by_style[meta[t].get("CreativeStyle")].append(t)

for style, members in sorted(by_style.items(), key=lambda x: str(x[0])):
    W = np.array([wb[t] for t in members])
    Y = np.array([recs[t]["coef"].ravel() for t in members], dtype=float)
    uniq_tab = len(np.unique(Y, axis=0))
    uniq_wb = len(np.unique(np.round(W, 6), axis=0))
    line = f"  {str(style):<8} {len(members):>3} 张  不同表 {uniq_tab:>2}  不同WB {uniq_wb:>2}"
    if len(members) >= 4:
        A = np.column_stack([np.ones(len(W)), W[:, 0], W[:, 2],
                             W[:, 0] ** 2, W[:, 2] ** 2, W[:, 0] * W[:, 2]])
        sol, *_ = np.linalg.lstsq(A, Y, rcond=None)
        err = np.abs(A @ sol - Y)
        line += f"  二次拟合残差 中位 {np.median(err):5.2f} p95 {np.percentile(err,95):6.2f} max {err.max():7.2f}"
    print(line)

# tone LUT 的两条曲线各是什么
print("\n=== tone LUT 的不同取值 ===")
T = np.array([recs[t]["tone"] for t in tags])
uniq, inv = np.unique(T, axis=0, return_inverse=True)
for i in range(len(uniq)):
    members = [tags[j] for j in np.nonzero(inv == i)[0]]
    styles = sorted({str(meta[m].get("CreativeStyle")) for m in members})
    dros = sorted({str(meta[m].get("DynamicRangeOptimizer")) for m in members})
    print(f"  曲线{i}: {len(members):>3} 张  峰值 {uniq[i].max()}  LUT[1]={uniq[i][1]}")
    print(f"          CreativeStyle={styles}  DRO={dros}")
