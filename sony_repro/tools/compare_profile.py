"""三联对比:LibRaw 原始线性 | color_profile 复刻 | Sony TIFF 真值。

用法: python tools/compare_profile.py <ARW> <TIF> <profile.npz> <out.png>
"""
import sys
import numpy as np
import rawpy
import tifffile
from PIL import Image, ImageDraw
from sony_repro.color_profile import ColorTransform

ARW, TIF, PROF, OUT = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
ct = ColorTransform.load(PROF)

raw = rawpy.imread(ARW)
lin = raw.postprocess(use_camera_wb=True, no_auto_bright=True,
                      output_color=rawpy.ColorSpace.sRGB, gamma=(1, 1),
                      output_bps=16, half_size=True).astype(np.float32) / 65535.0

repro = np.clip(ct.apply(lin), 0, 1).astype(np.float32)          # profile 复刻(已 display-referred)
disp_lin = np.clip(lin, 0, 1) ** (1 / 2.2)                        # LibRaw 线性做 gamma 仅为可视

tif = tifffile.imread(TIF)
if tif.ndim == 3 and tif.shape[2] > 3:
    tif = tif[..., :3]
tif = tif.astype(np.float32) / (65535.0 if tif.dtype == np.uint16 else 255.0)
# 缩到 lin 尺寸对齐
if tif.shape[:2] != lin.shape[:2]:
    if tif.shape[0] == lin.shape[1] and tif.shape[1] == lin.shape[0]:
        tif = np.transpose(tif, (1, 0, 2))
    tif = np.asarray(Image.fromarray((np.clip(tif, 0, 1) * 255).astype(np.uint8))
                     .resize((lin.shape[1], lin.shape[0]), Image.BILINEAR)).astype(np.float32) / 255.0


def to_img(a, w=760):
    im = Image.fromarray((np.clip(a, 0, 1) * 255).astype(np.uint8))
    h = int(im.height * w / im.width)
    return im.resize((w, h), Image.LANCZOS)


panels = [("LibRaw 原始 (gamma)", to_img(disp_lin)),
          ("复刻 (profile)", to_img(repro)),
          ("Sony 真值 (TIFF)", to_img(tif))]
w, h = panels[0][1].size
pad, top = 12, 34
canvas = Image.new("RGB", (w * 3 + pad * 4, h + top + pad), (24, 24, 28))
dr = ImageDraw.Draw(canvas)
for i, (label, im) in enumerate(panels):
    x = pad + i * (w + pad)
    canvas.paste(im, (x, top))
    dr.text((x + 4, 10), label, fill=(235, 235, 240))
canvas.save(OUT)
print("saved", OUT, "size", canvas.size)
