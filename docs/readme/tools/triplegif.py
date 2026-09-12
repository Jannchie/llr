"""Three toggle pairs side by side in one GIF, swapping in step: panel k shows
A_k on frame 0 and B_k on frame 1, labels burnt into the corner. One palette
across all six images so the swap shows only the render difference.

Usage: triplegif.py <out.gif|out.png> <x0 y0 x1 y1> <panel width> <a1> <labelA1> <b1> <labelB1> <a2> ... <b3> <labelB3>
A .png output is an APNG (lossless, 24-bit); .gif is 256 colours and only for
where APNG cannot be shown.
"""
import sys
from PIL import Image, ImageDraw, ImageFont

out = sys.argv[1]
crop = tuple(map(float, sys.argv[2:6]))
W = int(sys.argv[6])
pairs = [tuple(sys.argv[7 + 4 * k:11 + 4 * k]) for k in range(3)]
GAP = 6
font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 22)


def panel(path, label):
    im = Image.open(path).convert("RGB")
    fw, fh = im.size
    im = im.crop((round(crop[0] * fw), round(crop[1] * fh), round(crop[2] * fw), round(crop[3] * fh)))
    im = im.resize((W, round(W * im.size[1] / im.size[0])), Image.LANCZOS)
    d = ImageDraw.Draw(im)
    tw = d.textlength(label, font=font)
    d.rectangle((0, 0, tw + 24, 38), fill=(0, 0, 0))
    d.text((12, 7), label, fill=(255, 255, 255), font=font)
    return im


def sheet(images):
    h = images[0].height
    s = Image.new("RGB", (3 * W + 2 * GAP, h), (18, 18, 18))
    for k, im in enumerate(images):
        s.paste(im, (k * (W + GAP), 0))
    return s


fa = sheet([panel(a, la) for a, la, _, _ in pairs])
fb = sheet([panel(b, lb) for _, _, b, lb in pairs])
if out.lower().endswith(".png"):
    # APNG: full 24-bit colour, lossless -- a comparison of renders that differ
    # by a unit or two of L* cannot survive a 256-colour palette.
    fa.save(out, save_all=True, append_images=[fb], duration=1100, loop=0)
else:
    both = Image.new("RGB", (fa.width, fa.height * 2))
    both.paste(fa, (0, 0)); both.paste(fb, (0, fa.height))
    pal = both.quantize(colors=256, method=Image.Quantize.MEDIANCUT)
    qa = fa.quantize(palette=pal, dither=Image.Dither.FLOYDSTEINBERG)
    qb = fb.quantize(palette=pal, dither=Image.Dither.FLOYDSTEINBERG)
    qa.save(out, save_all=True, append_images=[qb], duration=1100, loop=0, optimize=False)
print(out, fa.size)
