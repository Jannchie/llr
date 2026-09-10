r"""差分法的第二步:两边的「降噪改了什么」不只比幅度,还逐像素比**形状**。

`denoise_delta.py` 只报 |Δ| 的统计量。这里把 Edit 的 (开 − 关) 与 llr 的 (开 − 关)
两张差分图放在画面中央对齐(镜头畸变校正在中心位移≈0,离线链路没做校正也无妨),
搜 ±4 像素的整数位移取最大相关,报亮度差分的相关系数、σ 比,以及按频带的相关。
相关接近 1 说明 llr 在全分辨率、真实输入上做的就是引擎做的那件事 —— 这是 RawNR
与 ITP 两条「只验过引擎 tile」的复刻第一次在真实管线上闭合。

用法::

    bash run_py.sh denoise_delta_corr.py [stem] [crop] [--off 路径 --on 路径]

`--off/--on` 可指向 16 位 TIFF(用 tifffile 读),不给就用 <stem>.JPG / <stem>-nr.JPG。
"""
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import numpy as np
from PIL import Image

sys.path.insert(0, "/home/jannchie/llr/sony_repro/tools")
sys.path.insert(0, "/home/jannchie/llr/apps/worker/src")

from aniso_stage import find_arw, render  # noqa: E402
import e2e_pipeline as E  # noqa: E402
from lens_apply import apply_distortion, parse_lens_corr  # noqa: E402
from llr_worker.cli import prepare_linear  # noqa: E402

W601 = np.array([0.299, 0.587, 0.114], np.float32)


def luma(rgb):
    return rgb @ W601


def box(a, r):
    """半径 r 的盒均值(分离),用 cumsum。"""
    def run(x, axis):
        c = np.cumsum(np.pad(x, [(r + 1, r) if i == axis else (0, 0) for i in range(2)], mode="edge"), axis=axis)
        return (np.take(c, np.arange(2 * r + 1, c.shape[axis]), axis=axis)
                - np.take(c, np.arange(0, c.shape[axis] - 2 * r - 1), axis=axis)) / (2 * r + 1)
    return run(run(a, 0), 1)


def best_shift(a, b, rng=4):
    best = None
    for dy in range(-rng, rng + 1):
        for dx in range(-rng, rng + 1):
            aa = a[rng + dy:a.shape[0] - rng + dy, rng + dx:a.shape[1] - rng + dx]
            bb = b[rng:b.shape[0] - rng, rng:b.shape[1] - rng]
            c = np.corrcoef(aa.ravel(), bb.ravel())[0, 1]
            if best is None or c > best[0]:
                best = (c, dy, dx)
    return best


def main() -> int:
    stem = sys.argv[1] if len(sys.argv) > 1 else "DSC03036"
    crop = int(sys.argv[2]) if len(sys.argv) > 2 and sys.argv[2].isdigit() else 1200
    arw = find_arw(stem)
    edit_off = Path(sys.argv[sys.argv.index("--off") + 1]) if "--off" in sys.argv else Path(str(arw).replace(".ARW", ".JPG"))
    edit_on = Path(sys.argv[sys.argv.index("--on") + 1]) if "--on" in sys.argv else Path(str(arw).replace(".ARW", "-nr.JPG"))

    def load(p):
        if p.suffix.lower() in (".tif", ".tiff"):
            import tifffile
            x = tifffile.imread(str(p)).astype(np.float32)
            return x / (65535.0 if x.max() > 255 else 255.0)
        return np.asarray(Image.open(p).convert("RGB"), np.float32) / 255.0
    a, b = load(edit_off), load(edit_on)
    print(f"Edit 关 {edit_off.name}  开 {edit_on.name}")
    d_edit = luma(b) - luma(a)
    print(f"Edit 开−关: 亮度差分 σ {d_edit.std():.5f}  尺寸 {d_edit.shape}")

    off = np.asarray(render(arw, denoise_model="passthrough", chroma=False), np.float32)
    on = np.asarray(render(arw, denoise_model="sony", chroma=False), np.float32)
    # 几何:Edit 的导出做了镜头畸变校正,离线 llr 没做 —— 差分图像噪声,半个像素的错位
    # 就把细尺度相关打到零。按 lens.ts 的表把 llr 两张都校正到 Edit 的几何上再相减。
    meta = prepare_linear(arw, {"profileId": "sony"}, E.ROOT, None, False,
                          half_size=False, denoise_model="passthrough").metadata
    lens = parse_lens_corr(getattr(meta, "lens_corr", None))
    if lens is not None:
        off = apply_distortion(off, lens["distortion"])[0].astype(np.float32)
        on = apply_distortion(on, lens["distortion"])[0].astype(np.float32)
        print("已按镜头畸变表校正 llr 两张渲染")
    else:
        print("⚠️ 没有镜头畸变表,几何未校正")
    d_llr = luma(on) - luma(off)
    print(f"llr  开−关: 亮度差分 σ {d_llr.std():.5f}  尺寸 {d_llr.shape}")

    h = min(d_edit.shape[0], d_llr.shape[0])
    w = min(d_edit.shape[1], d_llr.shape[1])
    cy, cx = h // 2, w // 2
    e = d_edit[cy - crop // 2:cy + crop // 2, cx - crop // 2:cx + crop // 2]
    m = d_llr[cy - crop // 2:cy + crop // 2, cx - crop // 2:cx + crop // 2]
    c, dy, dx = best_shift(e, m)
    print(f"\n中央 {crop}x{crop}:最佳位移 ({dy},{dx}),亮度差分相关 {c:.4f},σ 比 llr/Edit {m.std() / e.std():.3f}")
    e2 = e[4 + dy:e.shape[0] - 4 + dy, 4 + dx:e.shape[1] - 4 + dx]
    m2 = m[4:m.shape[0] - 4, 4:m.shape[1] - 4]
    prev_e, prev_m = e2, m2
    for r in (1, 2, 4, 8):
        le, lm = box(e2, r), box(m2, r)
        he, hm = prev_e - le, prev_m - lm
        cc = np.corrcoef(he.ravel(), hm.ravel())[0, 1]
        print(f"  频带 ~{r}px: 相关 {cc:.4f}  σ Edit {he.std():.5f}  llr {hm.std():.5f}  比 {hm.std() / max(he.std(), 1e-9):.3f}")
        prev_e, prev_m = le, lm
    cc = np.corrcoef(prev_e.ravel(), prev_m.ravel())[0, 1]
    print(f"  低频 (>8px): 相关 {cc:.4f}  σ Edit {prev_e.std():.5f}  llr {prev_m.std():.5f}")
    # 四张图各自的亮度按频带 σ(同一中央窗口,平坦块上取 MAD 更稳,这里先看整块 σ)
    print("\n  四张图各自的亮度高频 σ(中央窗口):")
    imgs = {"Edit 关": luma(a), "Edit 开": luma(b), "llr 关": luma(off), "llr 开": luma(on)}
    for nm, im in imgs.items():
        c_ = im[cy - crop // 2:cy + crop // 2, cx - crop // 2:cx + crop // 2].astype(np.float64)
        parts, prev = [], c_
        for r in (1, 2, 4, 8):
            lo = box(prev, r)
            parts.append(f"~{r}px {(prev - lo).std():.5f}")
            prev = lo
        print(f"    {nm:<8} " + "  ".join(parts))
    # 四角各取一块,看相关是否随畸变位移衰减(离线 llr 没做畸变校正)
    for name, (y, x) in {"左上": (crop, crop), "右下": (h - 2 * crop, w - 2 * crop)}.items():
        ee = d_edit[y:y + crop, x:x + crop]
        mm = d_llr[y:y + crop, x:x + crop]
        c, dy, dx = best_shift(ee, mm, 6)
        print(f"  {name} {crop}x{crop}: 最佳位移 ({dy},{dx}) 相关 {c:.4f}(畸变位移可能超过搜索范围)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
