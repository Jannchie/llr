"""用 Imaging Edge 导出的 16-bit TIFF 真值 + 对应 ARW,高精度拟合 color_profile。

src = LibRaw 相机原生线性 RGB;tgt = Sony 完整渲染的 16-bit TIFF。
拟合出的 profile 直接把相机线性映射到 Sony 成品(tone+color 一起)。

**多图合并**:扫描目录里所有 <名>.ARW + 同名 .TIF/.TIFF 配对,合并全部像素对一起
拟合,避免单图过拟合;并逐图单独报 ΔE 以检验泛化。

用法:
  python tools/fit_from_tiff.py <dir>            # 扫描目录里所有配对
  python tools/fit_from_tiff.py <dir> out.npz    # 并保存 profile
  python tools/fit_from_tiff.py a.ARW a.TIF       # 单对(兼容)
"""
import sys
import os
import glob
import numpy as np
import rawpy
import tifffile
from sony_repro.color_profile import fit, fit_matrix, ColorTransform, delta_e_stats

PER_IMG = 150_000  # 每图采样像素对数
DOWN = 4           # 降采样倍数:block 平均掉 LibRaw vs Sony 去马赛克/对齐差异,纯化颜色拟合


def block_mean(a, f):
    h = (a.shape[0] // f) * f; w = (a.shape[1] // f) * f
    return a[:h, :w].reshape(h // f, f, w // f, f, 3).mean(axis=(1, 3))


def collect_pairs(args):
    if len(args) >= 2 and args[1].lower().endswith((".tif", ".tiff")):
        return [(args[0], args[1])], (args[2] if len(args) > 2 else None)
    target = args[0]
    out = args[1] if len(args) > 1 and args[1].lower().endswith(".npz") else None
    arws = []
    if os.path.isdir(target):
        for e in ("*.ARW", "*.arw"):
            arws += glob.glob(os.path.join(target, e))
    else:
        arws = [target]
    pairs = []
    for a in sorted(set(arws)):
        base = os.path.splitext(a)[0]
        for ext in (".TIF", ".TIFF", ".tif", ".tiff"):
            if os.path.exists(base + ext):
                pairs.append((a, base + ext)); break
    return pairs, out


def load_pair(arw, tif_path):
    raw = rawpy.imread(arw)
    lin = raw.postprocess(use_camera_wb=True, no_auto_bright=True,
                          output_color=rawpy.ColorSpace.sRGB, gamma=(1, 1),
                          output_bps=16, half_size=False).astype(np.float32) / 65535.0
    tif = tifffile.imread(tif_path)
    if tif.ndim == 3 and tif.shape[2] > 3:
        tif = tif[..., :3]
    tif = tif.astype(np.float32) / (65535.0 if tif.dtype == np.uint16 else 255.0)
    if lin.shape[:2] != tif.shape[:2]:
        if lin.shape[0] == tif.shape[1] and lin.shape[1] == tif.shape[0]:
            tif = np.transpose(tif, (1, 0, 2))
        h = min(lin.shape[0], tif.shape[0]); w = min(lin.shape[1], tif.shape[1])
        ly = (lin.shape[0] - h) // 2; lx = (lin.shape[1] - w) // 2
        ty = (tif.shape[0] - h) // 2; tx = (tif.shape[1] - w) // 2
        lin = lin[ly:ly + h, lx:lx + w]; tif = tif[ty:ty + h, tx:tx + w]
    if DOWN > 1:
        lin = block_mean(lin, DOWN); tif = block_mean(tif, DOWN)
    return lin, tif


def sample(lin, tif, n, seed):
    src = lin.reshape(-1, 3); dst = tif.reshape(-1, 3)
    L = src.mean(1)
    keep = (L > 0.005) & (L < 0.99) & (dst.max(1) < 0.999)
    ok = np.where(keep)[0]
    rng = np.random.default_rng(seed)
    idx = rng.choice(ok, size=min(n, ok.size), replace=False)
    return src[idx], dst[idx]


def main():
    pairs, out = collect_pairs(sys.argv[1:])
    if not pairs:
        print("没有找到 ARW+TIF 配对"); return
    print("配对:", [os.path.basename(a) for a, _ in pairs], flush=True)

    per = []
    for i, (a, t) in enumerate(pairs):
        lin, tif = load_pair(a, t)
        s, d = sample(lin, tif, PER_IMG, seed=i)
        per.append((os.path.basename(a), s, d))
        print(f"  {os.path.basename(a)}: lin {lin.shape} 采样 {s.shape[0]}", flush=True)

    S = np.vstack([s for _, s, _ in per]); D = np.vstack([d for _, _, d in per])
    print(f"合并采样 {S.shape[0]}  (拟合中...)", flush=True)

    m = fit_matrix(S, D)
    print("3x3 only     总体:", {k: round(v, 4) for k, v in delta_e_stats(ColorTransform(m).apply(S), D).items()}, flush=True)
    ct = fit(S, D, lut_size=33)
    print("matrix+33^3  总体:", {k: round(v, 4) for k, v in delta_e_stats(ct.apply(S), D).items()}, flush=True)
    print("-- 逐图泛化(用合并 profile 各自评估)--", flush=True)
    for name, s, d in per:
        st = delta_e_stats(ct.apply(s), d)
        print(f"  {name}: mean {st['mean']:.4f}  p95 {st['p95']:.4f}  max {st['max']:.4f}", flush=True)
    if out:
        ct.save(out); print("saved", out, flush=True)


if __name__ == "__main__":
    main()
