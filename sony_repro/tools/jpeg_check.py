r"""llr 离线链对机内 JPEG:按 JPEG 亮度分桶看亮度差、饱和度比、色相差;再单看某个色相带(默认黄色)。

    bash -lc "cd ~/llr/apps/worker && uv run python ../../sony_repro/tools/jpeg_check.py /mnt/e/10960725/DSC02976.ARW /mnt/e/10960725/DSC02976.JPG [--lut3d] [--nr none] [--hue 60 --hw 25]"

JPEG 按 EXIF 方向转正,llr 做镜头畸变校正后和 JPEG 做整数位移对齐(中央 2000² 窗口)。
"""
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import numpy as np  # noqa: E402
from PIL import Image, ImageOps  # noqa: E402

sys.path.insert(0, "/home/jannchie/llr/sony_repro/tools")
sys.path.insert(0, "/home/jannchie/llr/apps/worker/src")
import colour_check as C  # noqa: E402
import e2e_pipeline as E  # noqa: E402
from lens_apply import apply_distortion, parse_lens_corr  # noqa: E402

from llr_worker.cli import prepare_linear  # noqa: E402


def main():
    argv = [a for a in sys.argv[1:]]
    lut3d = "--lut3d" in argv
    nr = "sony"
    if "--nr" in argv:
        nr = argv[argv.index("--nr") + 1]
    nr = None if nr in ("none", "off") else nr
    hue0 = float(argv[argv.index("--hue") + 1]) if "--hue" in argv else 60.0
    hw = float(argv[argv.index("--hw") + 1]) if "--hw" in argv else 25.0
    pos = [a for a in argv if a.lower().endswith((".arw", ".jpg", ".jpeg", ".tif", ".tiff"))]
    arw, jpg = Path(pos[0]), Path(pos[1])
    j = np.asarray(ImageOps.exif_transpose(Image.open(jpg)).convert("RGB"), np.float32) / 255.0
    if arw.suffix.lower() in (".tif", ".tiff"):
        # 直接拿 Edit 的导出当「llr」那一边,量 Edit 自己对机内 JPEG 的差
        img = C.load_edit_tiff(arw)
        print(f"Edit TIFF {img.shape};JPEG {j.shape}")
    else:
        meta = prepare_linear(arw, {"profileId": "sony"}, E.ROOT, None, False, half_size=False, denoise_model=nr).metadata
        lens = parse_lens_corr(getattr(meta, "lens_corr", None))
        marble = None
        if "--marble" in argv:
            import subprocess
            iso = int(subprocess.run(["exiftool", "-q", "-T", "-n", "-ISO", str(arw)], capture_output=True, text=True).stdout.strip() or 100)
            marble = (iso, 5)
        img, _ = E.render(arw, "sony", with_chroma=True, half_size=False, denoise_model=nr, lut3d=lut3d, marble=marble)
        img = np.asarray(img, np.float32)
        if lens is not None:
            img = apply_distortion(img, lens["distortion"])[0].astype(np.float32)
        print(f"llr {img.shape} 高级色彩复制 {'开' if lut3d else '关'} 降噪 {nr or '关'} marble {marble};JPEG {j.shape}")
    if img.shape[:2] != j.shape[:2]:
        # JPEG 可能与 RAW 尺寸不同(机内裁边),按短边缩放 llr 到 JPEG 尺寸
        sy, sx = j.shape[0] / img.shape[0], j.shape[1] / img.shape[1]
        ys = np.clip((np.arange(j.shape[0]) / sy).astype(int), 0, img.shape[0] - 1)
        xs = np.clip((np.arange(j.shape[1]) / sx).astype(int), 0, img.shape[1] - 1)
        img = img[np.ix_(ys, xs)]
        print(f"  llr 重采样到 JPEG 尺寸 ({sy:.4f},{sx:.4f})")
    h, w = j.shape[:2]
    crop = 2000
    cy, cx = h // 2, w // 2
    win = (slice(cy - crop // 2, cy + crop // 2), slice(cx - crop // 2, cx + crop // 2))
    _, dy, dx = C.best_shift(j[win] @ C.W601, img[win] @ C.W601, rng=6)
    print(f"对齐位移 ({dy},{dx})")
    jw = j[win][6 + dy:crop - 6 + dy, 6 + dx:crop - 6 + dx]
    lw = img[win][6:crop - 6, 6:crop - 6]
    yj, yl = C.ycc(jw), C.ycc(lw)
    sat_j = np.hypot(yj[..., 1], yj[..., 2])
    hue_j = np.degrees(np.arctan2(yj[..., 2], yj[..., 1]))
    hue_l = np.degrees(np.arctan2(yl[..., 2], yl[..., 1]))

    def rgb_hue(x):
        # HSV 式色相:红 0°、黄 60°、绿 120°
        return np.degrees(np.arctan2(np.sqrt(3.0) * (x[..., 1] - x[..., 2]), 2 * x[..., 0] - x[..., 1] - x[..., 2]))
    hsv_j, hsv_l = rgb_hue(jw), rgb_hue(lw)
    print("JPEG Y 分桶 | n | ΔY 中位 (llr−JPEG) | 饱和度比 llr/JPEG | 色相差 llr−JPEG(°) | llr RGB 均值 / JPEG RGB 均值")
    for lo, hi in [(0, 0.1), (0.1, 0.25), (0.25, 0.4), (0.4, 0.6), (0.6, 0.75), (0.75, 0.85), (0.85, 0.93), (0.93, 0.97), (0.97, 1.01)]:
        m = (yj[..., 0] >= lo) & (yj[..., 0] < hi)
        ms = m & (sat_j > 0.03)
        if m.sum() < 500:
            continue
        cr = np.hypot(yl[..., 1], yl[..., 2])[ms].mean() / max(np.hypot(yj[..., 1], yj[..., 2])[ms].mean(), 1e-6) if ms.sum() > 50 else float("nan")
        dh = np.degrees(np.angle(np.exp(1j * np.radians(hue_l - hue_j))[ms]).mean()) if ms.sum() > 50 else float("nan")
        print(f"  [{lo:.2f},{hi:.2f}) {m.sum():8d} | {np.median((yl[..., 0] - yj[..., 0])[m]):+.4f} | {cr:.3f} | {dh:+.1f} | "
              f"{np.round(lw[m].mean(0) * 255, 1)} / {np.round(jw[m].mean(0) * 255, 1)}")
    # 指定色相带(JPEG 上判定),按亮度分桶
    dh0 = np.degrees(np.angle(np.exp(1j * np.radians(hsv_j - hue0))))
    band = (np.abs(dh0) < hw) & (sat_j > 0.08)
    print(f"\nJPEG HSV 色相 {hue0:.0f}±{hw:.0f}°、饱和度>0.08 的像素 {band.sum()}({band.mean() * 100:.2f}%)")
    print("JPEG Y 分桶 | n | ΔY 中位 | 饱和度比 | 色相差 | llr RGB / JPEG RGB")
    for lo, hi in [(0, 0.25), (0.25, 0.4), (0.4, 0.6), (0.6, 0.75), (0.75, 0.85), (0.85, 0.93), (0.93, 1.01)]:
        m = band & (yj[..., 0] >= lo) & (yj[..., 0] < hi)
        if m.sum() < 200:
            continue
        cr = np.hypot(yl[..., 1], yl[..., 2])[m].mean() / np.hypot(yj[..., 1], yj[..., 2])[m].mean()
        dh = np.degrees(np.angle(np.exp(1j * np.radians(hsv_l - hsv_j))[m]).mean())
        print(f"  [{lo:.2f},{hi:.2f}) {m.sum():8d} | {np.median((yl[..., 0] - yj[..., 0])[m]):+.4f} | {cr:.3f} | {dh:+.1f} | "
              f"{np.round(lw[m].mean(0) * 255, 1)} / {np.round(jw[m].mean(0) * 255, 1)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
