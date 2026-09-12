"""Three-way comparison figure: camera JPEG / LLR Sony engine / Lightroom.

Usage: compare.py <camera.jpg> <llr.jpg> <lightroom.jpg> <out.jpg>
"""
import sys
from PIL import Image, ImageDraw, ImageFont

cam, llr, lr, out = sys.argv[1:5]
labels = ["Camera JPEG (α7C II, Creative Look FL)", "LLR · Sony engine", sys.argv[5] if len(sys.argv) > 5 else "Lightroom Classic · Adobe Color"]
W = 800                       # each panel width
CROP = (0.30, 0.10, 0.50, 0.42)  # head + red suit, as fractions of the frame
font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 22)
imgs = [Image.open(p).convert("RGB") for p in (cam, llr, lr)]
fw, fh = imgs[0].size
H = round(W * fh / fw)
cw, ch = W, round(W * (CROP[3] - CROP[1]) * fh / ((CROP[2] - CROP[0]) * fw))
label_h, gap = 40, 8
sheet = Image.new("RGB", (3 * W + 2 * gap, label_h + H + gap + ch), (18, 18, 18))
draw = ImageDraw.Draw(sheet)
for i, (im, lab) in enumerate(zip(imgs, labels)):
    x = i * (W + gap)
    draw.text((x + 10, 10), lab, fill=(230, 230, 230), font=font)
    sheet.paste(im.resize((W, H), Image.LANCZOS), (x, label_h))
    box = (round(CROP[0] * fw), round(CROP[1] * fh), round(CROP[2] * fw), round(CROP[3] * fh))
    sheet.paste(im.crop(box).resize((cw, ch), Image.LANCZOS), (x, label_h + H + gap))
sheet.save(out, quality=86, optimize=True)
print(out, sheet.size)
