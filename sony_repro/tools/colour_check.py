r"""颜色总账:离线链(e2e_pipeline,shader 的 numpy 镜像)对 Edit 自己的导出,按几种配置比色度。

    bash -lc "cd ~/llr/apps/worker && uv run --with tifffile python ../../sony_repro/tools/colour_check.py /mnt/e/temp_photo/DSC03036.ARW /mnt/e/temp_photo/DSC03036-auto16.TIF"

配置:当前(含 ChromaSuppres)、去掉 ChromaSuppres、再各叠 Marble 色差清理(旧参数 / 新参数)。
指标(中央窗口,镜头畸变校正 + 整数位移对齐后):色度 σ 比 llr/Edit(Cb、Cr)、平均色相差、
平均 RGB、以及按亮度分段(暗/中/亮/高光)的色度比。
"""
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import numpy as np  # noqa: E402

sys.path.insert(0, "/home/jannchie/llr/sony_repro/tools")
sys.path.insert(0, "/home/jannchie/llr/apps/worker/src")
import e2e_pipeline as E  # noqa: E402
from lens_apply import apply_distortion, parse_lens_corr  # noqa: E402

from llr_worker.cli import prepare_linear  # noqa: E402
from llr_worker.sony.chromanr import apply_chroma_nr  # noqa: E402

W601 = np.array([0.299, 0.587, 0.114], np.float32)
M = np.array([[0.299, 0.587, 0.114], [-0.168736, -0.331264, 0.5], [0.5, -0.418688, -0.081312]], np.float32)


def ycc(rgb):
    return rgb @ M.T


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


def stats(name, ours, edit):
    yo, ye = ycc(ours), ycc(edit)
    line = f"{name:<34}"
    for c, lab in ((1, "Cb"), (2, "Cr")):
        line += f" {lab} σ比 {yo[..., c].std() / ye[..., c].std():.3f}"
    ho = np.arctan2(yo[..., 2], yo[..., 1]); he = np.arctan2(ye[..., 2], ye[..., 1])
    sat = np.hypot(ye[..., 1], ye[..., 2]) > 0.03
    dh = np.degrees(np.angle(np.exp(1j * (ho - he))[sat]).mean())
    line += f"  色相差 {dh:+.2f}°  RGB均值 {np.round(ours.mean((0, 1)) * 255, 1)} vs {np.round(edit.mean((0, 1)) * 255, 1)}"
    y = ye[..., 0]
    bands = []
    for lo, hi, lab in ((0, 0.25, "暗"), (0.25, 0.6, "中"), (0.6, 0.85, "亮"), (0.85, 0.93, "高光"), (0.93, 0.97, ">0.93"), (0.97, 1.01, ">0.97")):
        m = (y >= lo) & (y < hi) & sat
        if m.sum() > 200:
            co = np.hypot(yo[..., 1], yo[..., 2])[m].mean(); ce = np.hypot(ye[..., 1], ye[..., 2])[m].mean()
            bands.append(f"{lab} {co / ce:.3f}")
    print(line + "  |饱和度比 " + " ".join(bands))


def load_edit_tiff(path):
    """Edit 的 16 位导出:像素按 TIFF 的 Orientation 标签转正(竖拍导出的像素仍是横向,只打标签),
    再归一到 0..1,和 llr 已经转正的渲染对齐。"""
    import subprocess
    import tifffile
    x = tifffile.imread(str(path)).astype(np.float32) / 65535.0
    o = subprocess.run(["exiftool", "-q", "-T", "-n", "-Orientation", str(path)], capture_output=True, text=True).stdout.strip()
    k = {"1": 0, "3": 2, "6": 3, "8": 1}.get(o, 0)     # 6 = 顺时针 90(转回需逆时针 3 次 rot90 的反向),8 = 逆时针 90
    if k:
        x = np.ascontiguousarray(np.rot90(x, k))
    print(f"Edit TIFF orientation {o or '?'} -> rot90 x{k}, shape {x.shape}")
    return x


def main():
    arw, tif = Path(sys.argv[1]), Path(sys.argv[2])
    edit = load_edit_tiff(tif)
    meta = prepare_linear(arw, {"profileId": "sony"}, E.ROOT, None, False, half_size=False, denoise_model="sony").metadata
    lens = parse_lens_corr(getattr(meta, "lens_corr", None))
    real_chroma = E.sony_chroma

    def render(no_suppress):
        if no_suppress:
            E.sony_chroma = lambda s, cross, gain, pivot=0.0, contrast=1.0, sat=1.0, suppress=None, lut=None: real_chroma(s, cross, gain, pivot, contrast, sat, None, lut)
        try:
            img, _ = E.render(arw, "sony", with_chroma=True, half_size=False, denoise_model="sony")
        finally:
            E.sony_chroma = real_chroma
        img = np.asarray(img, np.float32)
        if lens is not None:
            img = apply_distortion(img, lens["distortion"])[0].astype(np.float32)
        return img

    cur, nosup = render(False), render(True)
    h = min(edit.shape[0], cur.shape[0]); w = min(edit.shape[1], cur.shape[1])
    crop = 1600
    cy, cx = h // 2, w // 2
    win = (slice(cy - crop // 2, cy + crop // 2), slice(cx - crop // 2, cx + crop // 2))
    e = edit[win]
    c0 = cur[win]
    _, dy, dx = best_shift(e @ W601, c0 @ W601)
    print(f"对齐位移 ({dy},{dx});窗口 {crop}²,中心")
    ew = e[4 + dy:crop - 4 + dy, 4 + dx:crop - 4 + dx]

    def cut(img):
        return img[win][4:crop - 4, 4:crop - 4]
    stats("当前(ChromaSuppres 开)", cut(cur), ew)
    stats("去掉 ChromaSuppres", cut(nosup), ew)
    cands = [(4, 1e-4, 0.9)] if "--quick" in sys.argv else [(4, 1e-4, 0.9), (4, 1e-4, 1.0), (5, 1e-4, 1.0), (5, 3e-4, 1.0), (5, 1e-3, 1.0), (5, 3e-3, 1.0), (5, 1e-2, 1.0), (4, 1e-3, 1.0)]
    for lv, eps, amt in cands:
        kw = dict(levels=lv, guide=True, eps=eps, subsample=8, amount=amt)
        stats(f"当前+Marble L{lv} eps{eps:g} a{amt}", apply_chroma_nr(cut(cur), **kw).astype(np.float32), ew)
    # 同一组候选对引擎 Marble tile(自动档)的残差:细节带 / 整体(两色差平均),chromanr_sweep 的指标
    z = np.load("/home/jannchie/llr/sony_repro/tools/tiles_SIMDMarble_export.npz")

    def hp(x):
        k = np.zeros_like(x)
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                k += np.roll(np.roll(x, dy, 0), dx, 1)
        return x - k / 9

    def mad(v):
        return float(np.median(np.abs(v - np.median(v))) * 1.4826)
    print("\n对引擎 Marble tile 的残差 MAD(细节带 / 整体):")
    for lv, eps, amt in cands:
        acc = np.zeros(2); n = 0
        for t in ("t0", "t1", "t2"):
            a = z[f"{t}_in"].astype(np.float32); b = z[f"{t}_out"].astype(np.float32); m = z[f"{t}_meta"]
            x0, y0, x1, y1 = m[0x30 // 4:0x40 // 4]; sl = (slice(y0 + 16, y1 - 16), slice(x0 + 16, x1 - 16))
            ours = apply_chroma_nr(a / 16383.0, levels=lv, guide=True, eps=eps, subsample=8, amount=amt) * 16383.0
            for c in (0, 2):
                do = ours[..., c] - ours[..., 1]; de = b[..., c] - b[..., 1]
                acc += [mad((hp(do) - hp(de))[sl]), mad((do - de)[sl])]; n += 1
        acc /= n
        print(f"  L{lv} eps{eps:g} a{amt}: fine {acc[0]:.1f}  all {acc[1]:.1f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
