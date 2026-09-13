"""Colour distance of renders from the camera JPEG, whole frame and per region.

Usage: quant.py <camera.jpg> <regions.json> <label=path> [label=path ...]
regions.json: {"name": [x0, y0, x1, y1, "chroma"|"any"], ...} in frame fractions;
"chroma" keeps only pixels the camera rendered with C* > 18 (the coloured object,
not the gaps between leaves).

Two columns beside the whole-frame dE00: "low-pass" is dE00 after a Gaussian blur
(sigma LOWPASS px at this size), which drops the texture that noise reduction and
sharpening put into the per-pixel number and keeps what the eye sees when the
two are flicked -- a shift of the whole picture; "saturated" is the signed
lightness and chroma offset on the pixels both renders put above C* 40, the
colours the eye reads first. The masks use the mean of the two renders, since
selecting on one side keeps its noise and biases the offset.
"""
import json
import sys

import numpy as np
from PIL import Image, ImageFilter

W, H = 1752, 1168  # 1/4 size: colour, not demosaic/sharpening, is what is measured
LOWPASS = 24  # px of Gaussian blur at this size (~6 px on the 1/16 frame the eye flicks)
SATURATED = 40  # C* above which a pixel counts as a saturated colour


def srgb_to_lab(a):
    a = a / 255.0
    lin = np.where(a <= 0.04045, a / 12.92, ((a + 0.055) / 1.055) ** 2.4)
    M = np.array([[0.4124564, 0.3575761, 0.1804375], [0.2126729, 0.7151522, 0.0721750], [0.0193339, 0.1191920, 0.9503041]])
    xyz = lin @ M.T / np.array([0.95047, 1.0, 1.08883])
    f = np.where(xyz > 0.008856, np.cbrt(xyz), 7.787 * xyz + 16 / 116)
    return np.stack([116 * f[..., 1] - 16, 500 * (f[..., 0] - f[..., 1]), 200 * (f[..., 1] - f[..., 2])], -1)


def de2000(lab1, lab2):
    L1, a1, b1 = lab1[..., 0], lab1[..., 1], lab1[..., 2]
    L2, a2, b2 = lab2[..., 0], lab2[..., 1], lab2[..., 2]
    C1, C2 = np.hypot(a1, b1), np.hypot(a2, b2)
    Cm = (C1 + C2) / 2
    G = 0.5 * (1 - np.sqrt(Cm**7 / (Cm**7 + 25**7)))
    a1p, a2p = (1 + G) * a1, (1 + G) * a2
    C1p, C2p = np.hypot(a1p, b1), np.hypot(a2p, b2)
    h1p = np.degrees(np.arctan2(b1, a1p)) % 360
    h2p = np.degrees(np.arctan2(b2, a2p)) % 360
    dLp, dCp = L2 - L1, C2p - C1p
    dh = h2p - h1p
    dh = np.where(dh > 180, dh - 360, np.where(dh < -180, dh + 360, dh))
    dh = np.where(C1p * C2p == 0, 0, dh)
    dHp = 2 * np.sqrt(C1p * C2p) * np.sin(np.radians(dh / 2))
    Lm, Cmp = (L1 + L2) / 2, (C1p + C2p) / 2
    hs = h1p + h2p
    hm = np.where(np.abs(h1p - h2p) > 180, np.where(hs < 360, hs + 360, hs - 360) / 2, hs / 2)
    hm = np.where(C1p * C2p == 0, hs, hm)
    T = 1 - 0.17 * np.cos(np.radians(hm - 30)) + 0.24 * np.cos(np.radians(2 * hm)) + 0.32 * np.cos(np.radians(3 * hm + 6)) - 0.20 * np.cos(np.radians(4 * hm - 63))
    dth = 30 * np.exp(-(((hm - 275) / 25) ** 2))
    Rc = 2 * np.sqrt(Cmp**7 / (Cmp**7 + 25**7))
    Sl = 1 + 0.015 * (Lm - 50) ** 2 / np.sqrt(20 + (Lm - 50) ** 2)
    Sc, Sh = 1 + 0.045 * Cmp, 1 + 0.015 * Cmp * T
    Rt = -np.sin(np.radians(2 * dth)) * Rc
    return np.sqrt((dLp / Sl) ** 2 + (dCp / Sc) ** 2 + (dHp / Sh) ** 2 + Rt * (dCp / Sc) * (dHp / Sh))


def load(path):
    im = Image.open(path).convert("RGB").resize((W, H), Image.LANCZOS)
    return srgb_to_lab(np.asarray(im, dtype=np.float64)), srgb_to_lab(np.asarray(im.filter(ImageFilter.GaussianBlur(LOWPASS)), dtype=np.float64))


def hue(lab):
    return np.degrees(np.arctan2(lab[..., 2], lab[..., 1])) % 360


def circ_mean_diff(h1, h2):
    d = (h2 - h1 + 180) % 360 - 180
    return d.mean()


cam, cam_lp = load(sys.argv[1])
regions = json.load(open(sys.argv[2]))
renders = [(kv.split("=", 1)[0], *load(kv.split("=", 1)[1])) for kv in sys.argv[3:]]

masks = {}
for name, (x0, y0, x1, y1, mode) in regions.items():
    m = np.zeros((H, W), bool)
    m[round(y0 * H):round(y1 * H), round(x0 * W):round(x1 * W)] = True
    if mode == "chroma":
        m &= np.hypot(cam[..., 1], cam[..., 2]) > 18
    masks[name] = m

head = ["Render", "ΔE00 mean", "ΔE00 median", "ΔE00 p95", "ΔE00 low-pass", "saturated: ΔL* / ΔC*"] + [
    f"{n}: ΔE00 / Δh° / ΔC* / ΔL*" if regions[n][4] == "chroma" else f"{n}: ΔE00 / ΔL*" for n in regions]
rows = []
for label, lab, lab_lp in renders:
    d = de2000(cam, lab)
    sat = (np.hypot(cam[..., 1], cam[..., 2]) + np.hypot(lab[..., 1], lab[..., 2])) / 2 > SATURATED
    row = [label, f"{d.mean():.2f}", f"{np.median(d):.2f}", f"{np.percentile(d, 95):.2f}", f"{de2000(cam_lp, lab_lp).mean():.2f}",
           f"{(lab[..., 0] - cam[..., 0])[sat].mean():+.1f} / {(np.hypot(lab[..., 1], lab[..., 2]) - np.hypot(cam[..., 1], cam[..., 2]))[sat].mean():+.1f}"]
    for n, m in masks.items():
        c, r = cam[m], lab[m]
        dL = f"{(r[:, 0] - c[:, 0]).mean():+.1f}"
        if regions[n][4] == "chroma":  # hue is noise on a neutral region
            row.append(f"{d[m].mean():.2f} / {circ_mean_diff(hue(c), hue(r)):+.1f}° / {(np.hypot(r[:, 1], r[:, 2]) - np.hypot(c[:, 1], c[:, 2])).mean():+.1f} / {dL}")
        else:
            row.append(f"{d[m].mean():.2f} / {dL}")
    rows.append(row)
print("| " + " | ".join(head) + " |")
print("|" + "---|" * len(head))
for r in rows:
    print("| " + " | ".join(r) + " |")
