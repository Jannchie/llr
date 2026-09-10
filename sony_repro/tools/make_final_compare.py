"""四格对照:不降噪 / llr 小波 / Edit 的 RawNRSIMD / 机内直出。

数字已经说了(2.0-2.7px 带 1.49 / 0.93 / 1.18,直出 1.23),但"哪一版看起来更像
相机"这件事最终得用眼睛收。四张走同一条渲染链路、同一块画面、同一个放大方式,
差别只剩降噪那一级。

⚠️ 直出那格来自相机的 JPEG,几何与前三格差几十像素(前三格是离线渲染,不做裁剪
与镜头畸变校正)。它是**质感的参照**,不是逐点对照的对象。
"""
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import numpy as np
from PIL import Image

sys.path.insert(0, "/home/jannchie/llr/sony_repro/tools")
sys.path.insert(0, "/home/jannchie/llr/apps/worker/src")

from aniso_stage import W601, find_arw, render  # noqa: E402
from make_crop_compare import CROP, PAD, ZOOM, load_font, pick_dark_flat  # noqa: E402
from PIL import ImageDraw  # noqa: E402

TMP = Path("/home/jannchie/llr/tmp")
OOC = Path("/mnt/c/Users/Jannchie/Downloads/DSC03036-直出.jpg")

VARIANTS = [
    ("nodn", "不降噪", dict(denoise_model=None, chroma=True)),
    ("wavelet", "llr 小波", dict(denoise_model="wavelet", chroma=True)),
    ("sony", "Edit 的 RawNRSIMD", dict(denoise_model="sony", chroma=True)),
]


def main():
    stem = sys.argv[1] if len(sys.argv) > 1 else "DSC03036"
    arw = find_arw(stem)
    if arw is None:
        raise SystemExit(f"找不到 {stem}.ARW")
    TMP.mkdir(parents=True, exist_ok=True)

    arrays, labels = [], []
    for key, label, kw in VARIANTS:
        p = TMP / f"{stem}-final-{key}.jpg"
        if not p.exists():
            rgb = render(arw, **kw)
            Image.fromarray(np.clip(rgb * 255.0 + 0.5, 0, 255).astype(np.uint8)).save(
                p, quality=100, subsampling=0)
        arrays.append(np.asarray(Image.open(p).convert("RGB"), np.uint8))
        labels.append(label)
    if OOC.exists():
        arrays.append(np.asarray(Image.open(OOC).convert("RGB"), np.uint8))
        labels.append("机内直出(参照)")

    cy, cx = pick_dark_flat(arrays[0].astype(np.float32) @ W601)
    print(f"取样 (y={cy}, x={cx}) {CROP}x{CROP} 放大 {ZOOM}x")

    font, cjk = load_font()
    if not cjk:
        labels = ["no denoise", "llr wavelet", "Edit RawNRSIMD", "camera JPEG"][:len(labels)]
    tiles = [Image.fromarray(a[cy:cy + CROP, cx:cx + CROP]).resize(
        (CROP * ZOOM, CROP * ZOOM), Image.NEAREST) for a in arrays]

    tw, th = tiles[0].size
    bar, n = 38, len(tiles)
    canvas = Image.new("RGB", (tw * n + PAD * (n + 1), th + bar + PAD * 2), (22, 22, 24))
    d = ImageDraw.Draw(canvas)
    for i, (im, lab) in enumerate(zip(tiles, labels)):
        x = PAD + i * (tw + PAD)
        canvas.paste(im, (x, PAD + bar))
        d.text((x, PAD + 6), lab, fill=(232, 232, 236), font=font)
    out = TMP / f"{stem}-final-compare.png"
    canvas.save(out)
    print(f"写出 {out} ({canvas.size[0]}x{canvas.size[1]})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
