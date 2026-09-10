r"""llr 离线链对 Edit 导出,按 Edit Y 分桶看亮度差/饱和度比。
    bash -lc "cd ~/llr/apps/worker && uv run --with tifffile python ../../sony_repro/tools/ev_check.py <ARW> <Edit.TIF> <EV> [--nr none] [--marble] [--lut3d]"
--marble:末端加引擎 Marble 色差清理(ISO 从 exif 读,档 5);--lut3d:高级色彩复制。
"""
import subprocess
import sys
from pathlib import Path
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import numpy as np
sys.path.insert(0, "/home/jannchie/llr/sony_repro/tools"); sys.path.insert(0, "/home/jannchie/llr/apps/worker/src")
import colour_check as C
import e2e_pipeline as E
from lens_apply import apply_distortion, parse_lens_corr
from llr_worker.cli import prepare_linear

arw, tif, ev = Path(sys.argv[1]), Path(sys.argv[2]), float(sys.argv[3])
nr = None if "--nr" in sys.argv and sys.argv[sys.argv.index("--nr") + 1] in ("none", "off") else "sony"
edit = C.load_edit_tiff(tif)
meta = prepare_linear(arw, {"profileId": "sony"}, E.ROOT, None, False, half_size=False, denoise_model=nr).metadata
lens = parse_lens_corr(getattr(meta, "lens_corr", None))
marble = None
if "--marble" in sys.argv:
    iso = int(subprocess.run(["exiftool", "-q", "-T", "-n", "-ISO", str(arw)], capture_output=True, text=True).stdout.strip() or 100)
    marble = (iso, 5)
img, _ = E.render(arw, "sony", with_chroma=True, half_size=False, denoise_model=nr, exposure_ev=ev,
                  lut3d="--lut3d" in sys.argv, marble=marble)
print(f"llr: 降噪 {nr or '关'} marble {marble} 高级 {'--lut3d' in sys.argv}")
img = np.asarray(img, np.float32)
img = apply_distortion(img, lens["distortion"])[0].astype(np.float32) if lens else img
h = min(edit.shape[0], img.shape[0]); w = min(edit.shape[1], img.shape[1])
crop = 2000; cy, cx = h // 2, w // 2
win = (slice(cy - crop // 2, cy + crop // 2), slice(cx - crop // 2, cx + crop // 2))
_, dy, dx = C.best_shift(edit[win] @ C.W601, img[win] @ C.W601, rng=4)
ew = edit[win][4 + dy:crop - 4 + dy, 4 + dx:crop - 4 + dx]; lw = img[win][4:crop - 4, 4:crop - 4]
ye, yl = C.ycc(ew), C.ycc(lw)
print(f"EV {ev:+.2f} 对齐 ({dy},{dx}) 相关 {np.corrcoef(ye[...,0].ravel(), yl[...,0].ravel())[0,1]:.4f}")
print("Edit Y 分桶 | n | ΔY 中位 llr−Edit | 饱和度比")
sat = np.hypot(ye[..., 1], ye[..., 2]) > 0.03
for lo, hi in [(0, .1), (.1, .25), (.25, .4), (.4, .6), (.6, .75), (.75, .85), (.85, .93), (.93, .97), (.97, 1.01)]:
    m = (ye[..., 0] >= lo) & (ye[..., 0] < hi)
    if m.sum() < 500: continue
    ms = m & sat
    cr = np.hypot(yl[..., 1], yl[..., 2])[ms].mean() / max(np.hypot(ye[..., 1], ye[..., 2])[ms].mean(), 1e-6) if ms.sum() > 50 else float('nan')
    print(f"  [{lo:.2f},{hi:.2f}) {m.sum():8d} | {np.median((yl[..., 0] - ye[..., 0])[m]):+.4f} | {cr:.3f}")
