"""Fit the "camera match" correction: what the in-camera JPEG does on top of
Edit's pipeline, per body and Creative Look, as a small display-referred
transform in CIELAB.

    uv run python sony_repro/tools/camera_match_fit.py <sample dir> [--out camera_match.json] [--holdout 0.4]

<sample dir> holds <name>.ARW / <name>.JPG (camera) / <name>.llr.jpg (LLR's own
export at defaults, geometry already matching the camera's) and residuals.json
from tmp/nas/measure.py (used for the exif fields). Frames with a corrupt ISO
(65535), a manual DRO level, or dE00 >= 8 are left out — those are not a fixed
transform's business.

Model, applied to LLR's finished sRGB pixel converted to Lab (D65):

    L' = L + dL(L)          ten 10-L*-wide bands, linear between band centres
    C' = C * cr(L)          same bands, chroma ratio, fitted on C > 10
    h' = h + hs(h)          twelve 30-degree sectors, fitted on C > 15

Each band value is the median over frames of that frame's median residual, so
one odd scene cannot pull the table. A band that fewer than MIN_BAND_FRAMES
frames could measure (deep shadows, magenta) takes the pooled table's value; a
look's table is shrunk toward the pooled one by n / (n + SHRINK) so six frames
of SH cannot swing it as far as forty of FL; and every band is clamped to what
a fixed correction can honestly claim (LIMITS). --holdout reports before/after
dE00 on frames the tables were not fitted on, then refits on everything for
the file that ships.
"""
import json
import subprocess
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageOps

_q = {}
exec((Path(__file__).resolve().parents[2] / "docs" / "readme" / "tools" / "quant.py").read_text().split("cam = load")[0], _q)
srgb_to_lab, de2000 = _q["srgb_to_lab"], _q["de2000"]

STYLE = {0: "ST", 1: "VV", 2: "NT", 3: "PT", 15: "FL", 16: "VV2", 17: "IN", 18: "SH", 255: "Off"}
W, H = 876, 584
L_C = np.arange(5, 100, 10.0)
H_C = np.arange(15, 360, 30.0)
MIN_FRAMES = 6
MIN_BAND_FRAMES = 4
SHRINK = 6.0
LIMITS = {"dL": 4.0, "cr": (0.85, 1.15), "hs": 4.0}


def load(d: Path, name: str):
    cam = ImageOps.exif_transpose(Image.open(d / f"{name}.JPG")).convert("RGB")
    llr = Image.open(d / f"{name}.llr.jpg").convert("RGB")
    if llr.size != cam.size:
        llr = llr.resize(cam.size, Image.LANCZOS)
    tw, th = (H, W) if cam.height > cam.width else (W, H)
    return (srgb_to_lab(np.asarray(cam.resize((tw, th), Image.LANCZOS), np.float64)),
            srgb_to_lab(np.asarray(llr.resize((tw, th), Image.LANCZOS), np.float64)))


def stats(cam, llr):
    L, C = cam[..., 0], np.hypot(cam[..., 1], cam[..., 2])
    Lr, Cr = llr[..., 0], np.hypot(llr[..., 1], llr[..., 2])
    hue = np.degrees(np.arctan2(cam[..., 2], cam[..., 1])) % 360
    huer = np.degrees(np.arctan2(llr[..., 2], llr[..., 1])) % 360
    dh = (hue - huer + 180) % 360 - 180
    dL = np.full(10, np.nan); cr = np.full(10, np.nan); hs = np.full(12, np.nan)
    for i in range(10):
        m = (Lr >= i * 10) & (Lr < i * 10 + 10)
        if m.sum() > 300:
            dL[i] = np.median((L - Lr)[m])
        m2 = m & (Cr > 10)
        if m2.sum() > 300:
            cr[i] = np.median(C[m2]) / np.median(Cr[m2])
    for i in range(12):
        m = (huer >= i * 30) & (huer < i * 30 + 30) & (Cr > 15)
        if m.sum() > 300:
            hs[i] = np.median(dh[m])
    return dL, cr, hs


def interp_band(centres, values, x, period=None):
    v = np.asarray(values, float); ok = ~np.isnan(v)
    if ok.sum() == 0:
        return np.zeros_like(x)
    if period:
        c = np.concatenate([centres[ok] - period, centres[ok], centres[ok] + period])
        return np.interp(x, c, np.tile(v[ok], 3))
    return np.interp(x, centres[ok], v[ok])


def apply(llr, model):
    """The correction, in Lab — the reference the shader mirrors."""
    L, a, b = llr[..., 0], llr[..., 1], llr[..., 2]
    C = np.hypot(a, b); h = np.degrees(np.arctan2(b, a)) % 360
    L2 = L + interp_band(L_C, model["dL"], L)
    C2 = C * interp_band(L_C, model["cr"], L)
    h2 = np.radians(h + interp_band(H_C, model["hs"], h, period=360))
    return np.stack([L2, C2 * np.cos(h2), C2 * np.sin(h2)], -1)


def fit(S, pooled=None):
    """Median over frames of each band; thin bands take the pooled value (or,
    for the pooled table itself, their neighbours'); a look's table is shrunk
    toward the pooled one; everything is clamped to LIMITS."""
    out = {}
    for k, idx, neutral in (("dL", 0, 0.0), ("cr", 1, 1.0), ("hs", 2, 0.0)):
        rows = np.array([s[idx] for s in S], float)
        med = np.nanmedian(rows, 0); count = np.sum(~np.isnan(rows), 0)
        if pooled is None:
            c = H_C if k == "hs" else L_C
            filled = interp_band(c, np.where(count >= MIN_BAND_FRAMES, med, np.nan), c, period=360 if k == "hs" else None)
            v = np.where(count >= MIN_BAND_FRAMES, med, filled)
            v = np.where(np.isnan(v), neutral, v)
        else:
            base = np.asarray(pooled[k], float)
            w = len(S) / (len(S) + SHRINK)
            v = np.where(count >= MIN_BAND_FRAMES, base + (np.nan_to_num(med, nan=0.0) - base) * w, base)
        lim = LIMITS[k]
        v = np.clip(v, lim[0], lim[1]) if isinstance(lim, tuple) else np.clip(v, neutral - lim, neutral + lim)
        out[k] = np.round(v, 4).tolist()
    out["frames"] = len(S)
    return out


def tables(data, names, R):
    pooled = fit([data[n][2] for n in names])
    out = {"*": pooled}
    for st in sorted(set(STYLE[int(R[n]["meta"]["CreativeStyle"])] for n in names)):
        ns = [n for n in names if STYLE[int(R[n]["meta"]["CreativeStyle"])] == st]
        if len(ns) >= MIN_FRAMES:
            out[st] = fit([data[n][2] for n in ns], pooled)
    return out


def main() -> int:
    d = Path(sys.argv[1])
    out = Path(sys.argv[sys.argv.index("--out") + 1]) if "--out" in sys.argv else d / "camera_match.json"
    holdout = float(sys.argv[sys.argv.index("--holdout") + 1]) if "--holdout" in sys.argv else 0.0
    R = json.load(open(d / "residuals.json"))
    names = sorted(n for n, v in R.items() if "meta" in v and int(float(v["meta"]["ISO"])) < 65535
                   and str(v["meta"]["DynamicRangeOptimizer"]) != "20" and 0.05 < v["dE_mean"] < 8)
    body = subprocess.run(["exiftool", "-fast", "-s", "-s", "-s", "-Model", str(d / f"{names[0]}.ARW")], capture_output=True, text=True).stdout.strip()
    data = {}
    for n in names:
        cam, llr = load(d, n); data[n] = (cam, llr, stats(cam, llr))
    print(f"{len(names)} frames of {body}")
    if holdout:
        rng = np.random.default_rng(3); order = list(names); rng.shuffle(order)
        test = order[: int(len(order) * holdout)]; train = [n for n in order if n not in set(test)]
        t = tables(data, train, R)
        rows = []
        for n in test:
            cam, llr, _ = data[n]; st = STYLE[int(R[n]["meta"]["CreativeStyle"])]
            rows.append((de2000(cam, llr).mean(), de2000(cam, apply(llr, t.get(st, t["*"]))).mean()))
        rows = np.array(rows)
        print(f"held-out {len(test)} frames: dE00 mean {rows[:, 0].mean():.2f} -> {rows[:, 1].mean():.2f}, median {np.median(rows[:, 0]):.2f} -> {np.median(rows[:, 1]):.2f}, "
              f"share <= 2: {np.mean(rows[:, 0] <= 2):.0%} -> {np.mean(rows[:, 1] <= 2):.0%}")
    t = tables(data, names, R)
    for st, m in t.items():
        print(f"  {st:4s} n={m['frames']:3d} dL " + " ".join(f"{x:+.1f}" for x in m["dL"]) + "  cr " + " ".join(f"{x:.2f}" for x in m["cr"]) + "  hs " + " ".join(f"{x:+.1f}" for x in m["hs"]))
    existing = json.load(open(out)) if out.exists() else {}
    existing[body] = t
    out.write_text(json.dumps(existing, indent=1))
    print("wrote", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
