"""Side-by-side comparison figure: whole frame plus one crop, one panel per render.

Usage: compare.py <out.jpg> <label=path> [label=path ...]
"""
import sys
from PIL import Image, ImageDraw, ImageFont

out = sys.argv[1]
panels = [(kv.split("=", 1)[0], kv.split("=", 1)[1]) for kv in sys.argv[2:]]
W = 800                       # each panel width
CROP = (0.30, 0.10, 0.50, 0.42)  # head + red suit, as fractions of the frame
font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 22)
imgs = [Image.open(p).convert("RGB") for _, p in panels]
fw, fh = imgs[0].size
H = round(W * fh / fw)
cw, ch = W, round(W * (CROP[3] - CROP[1]) * fh / ((CROP[2] - CROP[0]) * fw))
label_h, gap = 40, 8
n = len(panels)
sheet = Image.new("RGB", (n * W + (n - 1) * gap, label_h + H + gap + ch), (18, 18, 18))
draw = ImageDraw.Draw(sheet)
for i, (im, (lab, _)) in enumerate(zip(imgs, panels)):
    x = i * (W + gap)
    draw.text((x + 10, 10), lab, fill=(230, 230, 230), font=font)
    sheet.paste(im.resize((W, H), Image.LANCZOS), (x, label_h))
    box = (round(CROP[0] * fw), round(CROP[1] * fh), round(CROP[2] * fw), round(CROP[3] * fh))
    sheet.paste(im.crop(box).resize((cw, ch), Image.LANCZOS), (x, label_h + H + gap))
sheet.save(out, quality=86, optimize=True)
print(out, sheet.size)
