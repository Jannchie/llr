"""Two-frame toggle GIF of one crop: A vs B, shared palette so the swap shows
only the render difference, not a palette shift.

Usage: togglegif.py <a.jpg> <labelA> <b.jpg> <labelB> <out.gif> [x0 y0 x1 y1 fractions] [width]
"""
import sys
from PIL import Image, ImageDraw, ImageFont

a, la, b, lb, out = sys.argv[1:6]
crop = tuple(map(float, sys.argv[6:10])) if len(sys.argv) > 9 else (0.0, 0.0, 1.0, 1.0)
W = int(sys.argv[10]) if len(sys.argv) > 10 else 900
font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 26)


def frame(path, label):
    im = Image.open(path).convert("RGB")
    fw, fh = im.size
    im = im.crop((round(crop[0] * fw), round(crop[1] * fh), round(crop[2] * fw), round(crop[3] * fh)))
    im = im.resize((W, round(W * im.size[1] / im.size[0])), Image.LANCZOS)
    d = ImageDraw.Draw(im)
    tw = d.textlength(label, font=font)
    d.rectangle((0, 0, tw + 28, 44), fill=(0, 0, 0))
    d.text((14, 8), label, fill=(255, 255, 255), font=font)
    return im


fa, fb = frame(a, la), frame(b, lb)
both = Image.new("RGB", (fa.width, fa.height * 2))
both.paste(fa, (0, 0)); both.paste(fb, (0, fa.height))
pal = both.quantize(colors=256, method=Image.Quantize.MEDIANCUT)
qa = fa.quantize(palette=pal, dither=Image.Dither.FLOYDSTEINBERG)
qb = fb.quantize(palette=pal, dither=Image.Dither.FLOYDSTEINBERG)
qa.save(out, save_all=True, append_images=[qb], duration=1100, loop=0, optimize=False)
print(out, fa.size)
