r"""高光带逐像素看:Edit 出口 vs llr(ChromaSuppres 开/关)在 Edit Y>0.97 的有色像素上,通道最大/最小值、色度。
    bash -lc "cd ~/llr/apps/worker && uv run --with tifffile python ../../sony_repro/tools/highlight_probe.py /mnt/e/temp_photo/DSC03036.ARW /mnt/e/temp_photo/DSC03036-auto16.TIF"

可选参数:
    --lut3d      渲染时打开 ZcTask3DLut(Edit 的「色彩复制 = 高级」,sony/lut3d.py)。
                 只有当那份 Edit 导出**也**是开着高级色彩复制导的,两边才可比。
    --nr <名字>  降噪模型,默认 sony;`--nr none` 是完全不降噪 —— 对着一份
                 「降噪关」的 Edit 导出量的时候必须给它,否则量到的是降噪的差。
"""
import sys
from pathlib import Path
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import numpy as np
sys.path.insert(0, "/home/jannchie/llr/sony_repro/tools")
sys.path.insert(0, "/home/jannchie/llr/apps/worker/src")
import colour_check as C
import e2e_pipeline as E
from lens_apply import apply_distortion, parse_lens_corr
from llr_worker.cli import prepare_linear

argv = [a for a in sys.argv[1:] if not a.startswith("--")]
flags = [a for a in sys.argv[1:] if a.startswith("--")]
lut3d = "--lut3d" in flags
nr = "sony"
if "--nr" in sys.argv:
    nr = sys.argv[sys.argv.index("--nr") + 1]
    argv = [a for a in argv if a != nr]
nr = None if nr in ("none", "off", "") else nr
arw, tif = Path(argv[0]), Path(argv[1])
print(f"llr: 降噪 {nr or '关'}   高级色彩复制 {'开' if lut3d else '关'}")
edit = C.load_edit_tiff(tif)
meta = prepare_linear(arw, {"profileId": "sony"}, E.ROOT, None, False, half_size=False, denoise_model=nr).metadata
lens = parse_lens_corr(getattr(meta, "lens_corr", None))
real = E.sony_chroma
outs = {}
for name, nosup in (("开", False), ("关", True)):
    if nosup:
        # 只关 ChromaSuppres,YGamma 的表照查 —— 关掉的是 suppress 那一项。
        E.sony_chroma = lambda s, cross, gain, pivot=0.0, contrast=1.0, sat=1.0, suppress=None, lut=None, lut3d=False: real(s, cross, gain, pivot, contrast, sat, None, lut, lut3d)
    img, _ = E.render(arw, "sony", with_chroma=True, half_size=False, denoise_model=nr, lut3d=lut3d)
    E.sony_chroma = real
    img = np.asarray(img, np.float32)
    outs[name] = apply_distortion(img, lens["distortion"])[0].astype(np.float32) if lens else img
h = min(edit.shape[0], outs["开"].shape[0]); w = min(edit.shape[1], outs["开"].shape[1])
e = edit[:h, :w]; ye = C.ycc(e)
sat = np.hypot(ye[..., 1], ye[..., 2])
for lo, hi in ((0.93, 0.97), (0.97, 1.01)):
    m = (ye[..., 0] >= lo) & (ye[..., 0] < hi) & (sat > 0.03)
    print(f"Edit Y∈[{lo},{hi}) 有色像素 {m.sum()} ({m.mean()*100:.2f}% 整幅)")
    for name, img in (("Edit", e), ("llr 开", outs["开"][:h, :w]), ("llr 关", outs["关"][:h, :w])):
        yc = C.ycc(img)
        print(f"   {name:<7} Y均 {yc[...,0][m].mean():.4f}  色度均 {np.hypot(yc[...,1], yc[...,2])[m].mean():.4f}  max通道均 {img[m].max(-1).mean():.4f}  min通道均 {img[m].min(-1).mean():.4f}  max通道==1 占比 {(img[m].max(-1) >= 0.999).mean()*100:.1f}%")

# 亮度传递:按 Edit 的 Y 分桶,看 llr 的 Y 平均高/低多少(整幅中央 3000²,避开边角畸变)
cy, cx = h // 2, w // 2
win = (slice(cy - 1500, cy + 1500), slice(cx - 1500, cx + 1500))
ye_c = C.ycc(e[win])[..., 0]; yl_c = C.ycc(outs["开"][:h, :w][win])[..., 0]
print("\n亮度传递(Edit Y 分桶 → llr Y − Edit Y 的均值,及中位):")
for lo in np.arange(0.0, 1.0, 0.1):
    m = (ye_c >= lo) & (ye_c < lo + 0.1)
    if m.sum() > 1000:
        d = (yl_c - ye_c)[m]
        print(f"   [{lo:.1f},{lo+0.1:.1f}) n={m.sum():8d}  Δ均 {d.mean():+.4f}  Δ中位 {np.median(d):+.4f}")
m = ye_c >= 0.93
print(f"   [0.93,1.0]  n={m.sum():8d}  Δ均 {(yl_c-ye_c)[m].mean():+.4f}  Δ中位 {np.median((yl_c-ye_c)[m]):+.4f}")
