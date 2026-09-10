"""看**最坏**的那一块,不是挑出来的那一块。

上一轮把 `"sony"` 设成默认,依据是两个统计量(平坦块的频谱、结构块的细节量),
而给用户看的对照图取的是"又平又暗"的一块 —— 那正是这个算子表现最好的地方。
用户一看实图就发现全错了。教训很直接:**验一个算子,要去它最可能崩的地方看。**

所以这里的取块规则是反过来的:找 `|降噪后 − 不降噪|` 最大的那个块。三格并排,
不降噪 / 小波 / Sony,同一坐标、NEAREST 放大,不做任何美化。
"""
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import numpy as np
import rawpy
from PIL import Image, ImageDraw

sys.path.insert(0, "/home/jannchie/llr/sony_repro/tools")
sys.path.insert(0, "/home/jannchie/llr/apps/worker/src")

from aniso_stage import find_arw  # noqa: E402
from make_crop_compare import load_font  # noqa: E402
from llr_worker.denoise import (  # noqa: E402
    _plane_black_levels, _plane_colors, get_denoiser, pack_bayer, unpack_bayer)
from llr_worker.sony.rawnr import detail_restore, noise_model  # noqa: E402

CROP, ZOOM, PAD = 300, 2, 14
TMP = Path("/home/jannchie/llr/tmp")


def develop(path, planes_override=None):
    with rawpy.imread(str(path)) as raw:
        if planes_override is not None:
            visible = raw.raw_image_visible
            h, w = visible.shape
            he, we = h - (h % 2), w - (w % 2)
            visible[:he, :we] = unpack_bayer(planes_override)
        return raw.postprocess(use_camera_wb=True, no_auto_bright=True,
                               output_color=rawpy.ColorSpace.sRGB,
                               gamma=(2.222, 4.5), output_bps=8)


def main():
    stem = sys.argv[1] if len(sys.argv) > 1 else "DSC03036"
    arw = find_arw(stem)
    with rawpy.imread(str(arw)) as raw:
        visible = raw.raw_image_visible
        h, w = visible.shape
        he, we = h - (h % 2), w - (w % 2)
        planes = pack_bayer(np.ascontiguousarray(visible[:he, :we])).astype(np.float32)
        sizes = raw.sizes
        rp = int(getattr(sizes, "top_margin", 0) or 0)
        cp = int(getattr(sizes, "left_margin", 0) or 0)
        black = _plane_black_levels(raw, rp, cp)
        cfa = _plane_colors(raw, rp, cp)
        white = float(raw.white_level)
        norm = np.clip((planes - black) / np.maximum(white - black, 1.0), 0.0, 1.0)
        curve, rest = noise_model(arw), detail_restore(arw)
        det = None if rest is None or curve is None else (
            rest.fraction, rest.limit_in_thresholds(curve))

    outs = {}
    for name in ("wavelet", "sony"):
        a = get_denoiser(name)(norm.copy(), None, cfa, curve, det,
                               sensor_levels=(black, white))
        outs[name] = np.rint(np.clip(a * np.maximum(white - black, 1.0) + black,
                                     0, white)).astype(visible.dtype)

    # 最坏的块:Sony 与不降噪差最大的地方。CROP 是全分辨率的,平面坐标要减半。
    d = np.abs(outs["sony"].astype(np.float32) - planes).max(axis=2)
    g = CROP // 2
    hh, ww = d.shape[0] // g * g, d.shape[1] // g * g
    tiles = d[:hh, :ww].reshape(hh // g, g, ww // g, g).mean(axis=(1, 3))
    k = int(np.argmax(tiles))
    py, px = k // tiles.shape[1] * g, k % tiles.shape[1] * g
    cy, cx = py * 2, px * 2
    print(f"最坏块 @ 全分辨率 ({cy}, {cx})")

    rgbs = [develop(arw), develop(arw, outs["wavelet"]), develop(arw, outs["sony"])]
    font, cjk = load_font()
    labels = (["不降噪", "llr 小波", "Sony RawNRSIMD"] if cjk
              else ["no denoise", "llr wavelet", "Sony RawNRSIMD"])
    tiles_im = [Image.fromarray(a[cy:cy + CROP, cx:cx + CROP]).resize(
        (CROP * ZOOM, CROP * ZOOM), Image.NEAREST) for a in rgbs]

    tw, th = tiles_im[0].size
    bar, n = 38, len(tiles_im)
    canvas = Image.new("RGB", (tw * n + PAD * (n + 1), th + bar + PAD * 2), (22, 22, 24))
    dr = ImageDraw.Draw(canvas)
    for i, (im, lab) in enumerate(zip(tiles_im, labels)):
        x = PAD + i * (tw + PAD)
        canvas.paste(im, (x, PAD + bar))
        dr.text((x, PAD + 6), lab, fill=(232, 232, 236), font=font)
    out = TMP / f"{stem}-worst-crop.png"
    canvas.save(out)
    print(f"写出 {out} ({canvas.size[0]}x{canvas.size[1]})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
