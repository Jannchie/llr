"""Fit the "camera match" correction: what the in-camera JPEG does on top of
Edit's pipeline, per body and Creative Look, as a smooth display-referred
transform in CIELAB.

    uv run python sony_repro/tools/camera_match_fit.py <sample dir> [--suffix llr] [--out camera_match.json] [--holdout 0.4]

<sample dir> holds <name>.ARW / <name>.JPG (camera) / <name>.<suffix>.jpg
(LLR's own export with the switches the table is meant for -- fit on the
render it will be applied to: camera match ON, so the shader takes the
advanced luma pair, with the worker started under LLR_CAMERA_MATCH_IDENTITY=1
so it sends an identity table and nothing is corrected yet) and
residuals.json from tmp/nas/measure.py (for the exif fields). Frames with a corrupt ISO (65535), a manual DRO level, or
dE00 >= 8 are left out — those are not a fixed transform's business.

Model, applied to LLR's finished sRGB pixel converted to Lab (D65):

    L' = L + dL(L, C)        luma offset, a surface over lightness x chroma
    C' = C * cr(L, C)        chroma gain, the same surface
    h' = h + hs(h)           hue shift, periodic in hue

Why two dimensions: the residual is not one number per lightness. Near
neutrals (C* < 15) LLR sits 3-10% MORE saturated than the camera (its chroma
noise reduction is stronger), while the saturated colours the eye reads first
(C* > 45) sit 1-3% LESS, IN's up to 10% -- a table indexed by lightness alone
averages the two and gets the saturated colours wrong in the wrong direction.

How the surface is fitted: each frame is binned on the LLR pixel's (L*, C*)
and each bin's median residual taken, so one odd scene cannot pull a cell;
the medians are taken across frames; then a dense grid is solved for that
matches those cell medians where there are enough of them (>= MIN_BAND_FRAMES
frames), is smooth (second differences penalised), and sits at identity where
there is no data (ridge toward identity) -- deep shadows at high chroma, the
brightest saturated colours. A look with too few frames is blended toward the
pooled table by n / (n + SHRINK), and every value is clamped to what a fixed
correction can honestly claim (LIMITS). The grid ships as-is; the shader
samples it bilinearly, which at this density is within 1e-3 of the fit.

--holdout reports before/after on frames the tables were not fitted on: the
whole-frame dE00 mean, and separately the saturated pixels (camera C* > 40)
and the bright saturated ones (L* > 60 too), which is where the eye looks and
where CIEDE2000's chroma weighting under-counts.
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
# The fitted grid: lightness 0..100 in steps of 5, chroma 0..90 in steps of 5
# (beyond 90 the last column holds), hue in 24 sectors of 15 degrees.
L_AXIS = np.arange(0, 101, 5.0)
C_AXIS = np.arange(0, 91, 5.0)
H_AXIS = np.arange(7.5, 360, 15.0)
# Measurement bins are coarser than the grid, so each cell has enough pixels.
L_BINS = np.arange(0, 101, 10.0)
C_BINS = np.array([0, 5, 10, 15, 22, 30, 40, 50, 60, 75, 90, 200.0])
MIN_FRAMES = 6
POOLED = "*"
FADE_SUFFIX = "+fade"
# M-size (3584 x 2560) frames stay out of the fit: their DRO is still the
# engine's global fallback rather than the body's grid (PIPELINE.md), so what
# separates them from their camera JPEG is that, not the camera's finishing,
# and their frame offsets spread from -4 to +8 L* where L-size frames of one
# group sit within a unit of each other.
M_SIZE_MAX_WIDTH = 4000
MIN_BAND_FRAMES = 8
# The lightness surface can take a cell on fewer frames: every frame has
# mid-tones at low chroma, and a cell's lightness median is steady on four of
# them where its chroma ratio in the bright saturated corner is not on eight.
# It also lets a look of six frames -- a Fade state of one look -- contribute
# its own mid-tones instead of only a shrunk copy of the pooled table.
MIN_BAND_FRAMES_L = 4
MIN_PIXELS = 200
SHRINK = 6.0
LIMITS = {"dL": 4.0, "cr": (0.85, 1.15), "hs": 4.0}
SMOOTH = 4.0       # second-difference penalty, in units of the data weight
END_RAMP = 8.0     # L* over which the correction fades to identity at black and white
RIDGE = 0.05       # pull toward identity per cell, relative to one measured cell


def is_m_size(meta) -> bool:
    return max(int(v) for v in str(meta["ImageSize"]).replace("x", " ").split()) < M_SIZE_MAX_WIDTH


def load(d: Path, name: str, suffix: str):
    cam = ImageOps.exif_transpose(Image.open(d / f"{name}.JPG")).convert("RGB")
    llr = Image.open(d / f"{name}.{suffix}.jpg").convert("RGB")
    if llr.size != cam.size:
        llr = llr.resize(cam.size, Image.LANCZOS)
    tw, th = (H, W) if cam.height > cam.width else (W, H)
    return (srgb_to_lab(np.asarray(cam.resize((tw, th), Image.LANCZOS), np.float64)),
            srgb_to_lab(np.asarray(llr.resize((tw, th), Image.LANCZOS), np.float64)))


def stats(cam, llr):
    """Per-frame cell medians over (L*, C*): dL, chroma ratio; and per hue
    sector (C* > 15): hue shift. NaN where a cell is thin."""
    L, C = cam[..., 0], np.hypot(cam[..., 1], cam[..., 2])
    Lr, Cr = llr[..., 0], np.hypot(llr[..., 1], llr[..., 2])
    hue = np.degrees(np.arctan2(cam[..., 2], cam[..., 1])) % 360
    huer = np.degrees(np.arctan2(llr[..., 2], llr[..., 1])) % 360
    dh = (hue - huer + 180) % 360 - 180
    nl, nc = len(L_BINS) - 1, len(C_BINS) - 1
    dL = np.full((nl, nc), np.nan); cr = np.full((nl, nc), np.nan)
    # Cells are assigned on the mean of the two renders, not on LLR's value
    # alone: binning on one noisy side selects the pixels whose noise pushed
    # that side up, and every ratio in the cell then reads low (regression to
    # the mean) -- measured as a spurious unit of chroma over-correction at
    # C* > 40. The table is still indexed by LLR's (L*, C*) when applied; the
    # bias only lives in how the measurements were grouped.
    Lm, Cm = (L + Lr) / 2, (C + Cr) / 2
    li = np.clip(np.digitize(Lm, L_BINS) - 1, 0, nl - 1); ci = np.clip(np.digitize(Cm, C_BINS) - 1, 0, nc - 1)
    cell = li * nc + ci
    for k in range(nl * nc):
        m = cell == k
        n = int(m.sum())
        if n < MIN_PIXELS:
            continue
        i, j = divmod(k, nc)
        dL[i, j] = np.median((L - Lr)[m])
        if C_BINS[j] >= 5:
            cr[i, j] = np.median(C[m]) / max(np.median(Cr[m]), 1e-6)
    hs = np.full(len(H_AXIS), np.nan)
    hm = np.degrees(np.arctan2(cam[..., 2] + llr[..., 2], cam[..., 1] + llr[..., 1])) % 360
    for i in range(len(H_AXIS)):
        m = (hm >= i * 15) & (hm < i * 15 + 15) & (Cm > 15)
        if m.sum() >= MIN_PIXELS:
            hs[i] = np.median(dh[m])
    return dL, cr, hs


def smooth_surface(target, count, neutral, min_frames=MIN_BAND_FRAMES):
    """Solve a dense (L_AXIS x C_AXIS) grid that fits the measured cell medians,
    is smooth, and rests at `neutral` where nothing was measured."""
    nl, nc = len(L_AXIS), len(C_AXIS)
    lc = (L_BINS[:-1] + L_BINS[1:]) / 2
    cc = np.minimum((C_BINS[:-1] + C_BINS[1:]) / 2, C_AXIS[-1])
    n = nl * nc
    rows, rhs, wts = [], [], []
    # data terms: each measured cell pins the bilinear sample at its centre
    for i in range(target.shape[0]):
        for j in range(target.shape[1]):
            if count[i, j] < min_frames or np.isnan(target[i, j]):
                continue
            fl = np.clip(lc[i] / 5.0, 0, nl - 1); fc = np.clip(cc[j] / 5.0, 0, nc - 1)
            il, ic = min(int(fl), nl - 2), min(int(fc), nc - 2); tl, tc = fl - il, fc - ic
            r = np.zeros(n)
            for dl_, dc_, w in ((0, 0, (1 - tl) * (1 - tc)), (1, 0, tl * (1 - tc)), (0, 1, (1 - tl) * tc), (1, 1, tl * tc)):
                r[(il + dl_) * nc + ic + dc_] += w
            rows.append(r); rhs.append(target[i, j] - neutral); wts.append(1.0)
    A = np.array(rows) if rows else np.zeros((0, n)); b = np.array(rhs)
    # smoothness: second differences along both axes
    S = []
    for i in range(nl):
        for j in range(nc):
            if 0 < i < nl - 1:
                r = np.zeros(n); r[(i - 1) * nc + j] = 1; r[i * nc + j] = -2; r[(i + 1) * nc + j] = 1; S.append(r)
            if 0 < j < nc - 1:
                r = np.zeros(n); r[i * nc + j - 1] = 1; r[i * nc + j] = -2; r[i * nc + j + 1] = 1; S.append(r)
    S = np.array(S) * SMOOTH
    Rg = np.eye(n) * RIDGE
    M = np.vstack([A, S, Rg]); v = np.concatenate([b, np.zeros(len(S)), np.zeros(n)])
    x, *_ = np.linalg.lstsq(M, v, rcond=None)
    return x.reshape(nl, nc) + neutral


def fit(S, pooled=None):
    """Tables for one set of frames; `pooled` shrinks a small look toward the
    pooled table."""
    def agg(idx):
        rows = np.array([s[idx] for s in S], float)
        return np.nanmedian(rows, 0), np.sum(~np.isnan(rows), 0)
    out = {}
    for k, idx, neutral, min_frames in (("dL", 0, 0.0, MIN_BAND_FRAMES_L), ("cr", 1, 1.0, MIN_BAND_FRAMES)):
        med, count = agg(idx)
        v = smooth_surface(med, count, neutral, min_frames)
        if pooled is not None:
            base = np.asarray(pooled[k], float); w = len(S) / (len(S) + SHRINK)
            v = base + (v - base) * w
        lim = LIMITS[k]
        v = np.clip(v, lim[0], lim[1]) if isinstance(lim, tuple) else np.clip(v, neutral - lim, neutral + lim)
        # The end points stay put. The top L* bin's median says the camera's
        # near-whites sit 2-4 L* below ours, and that is true of L* 90-97 --
        # but a clipped highlight is 255 on both sides, and a table that pulls
        # L* 100 down to 96 leaves every white in the frame a grey 245: the
        # first thing the eye notices. So the correction ramps to identity
        # over the last END_RAMP L* (and the first, for the same reason at
        # black), which compresses the top of the range a little harder than
        # the camera and keeps the whites white.
        ramp = np.clip(np.minimum(L_AXIS, 100 - L_AXIS) / END_RAMP, 0, 1)[:, None]
        v = neutral + (v - neutral) * ramp
        out[k] = np.round(v, 4).tolist()
    med, count = agg(2)
    ok = count >= MIN_BAND_FRAMES
    hs = np.where(ok, np.nan_to_num(med), np.nan)
    if ok.sum() == 0:
        hs = np.zeros(len(H_AXIS))
    else:
        ax = np.concatenate([H_AXIS[ok] - 360, H_AXIS[ok], H_AXIS[ok] + 360]); vv = np.tile(hs[ok], 3)
        hs = np.interp(H_AXIS, ax, vv)
        # a light circular smoothing so one sector cannot stand alone
        hs = (np.roll(hs, 1) + 2 * hs + np.roll(hs, -1)) / 4
    if pooled is not None:
        base = np.asarray(pooled["hs"], float); w = len(S) / (len(S) + SHRINK)
        hs = base + (hs - base) * w
    out["hs"] = np.round(np.clip(hs, -LIMITS["hs"], LIMITS["hs"]), 3).tolist()
    out["frames"] = len(S)
    return out


def apply(llr, model):
    """The correction, in Lab -- the reference the shader mirrors (bilinear on
    the grid, clamped to its edges; linear and periodic in hue)."""
    L, a, b = llr[..., 0], llr[..., 1], llr[..., 2]
    C = np.hypot(a, b); h = np.degrees(np.arctan2(b, a)) % 360
    dL, cr = np.asarray(model["dL"]), np.asarray(model["cr"])
    fl = np.clip(L / 5.0, 0, len(L_AXIS) - 1 - 1e-9); fc = np.clip(C / 5.0, 0, len(C_AXIS) - 1 - 1e-9)
    il, ic = fl.astype(int), fc.astype(int); tl, tc = fl - il, fc - ic

    def sample(g):
        return ((1 - tl) * (1 - tc) * g[il, ic] + tl * (1 - tc) * g[il + 1, ic]
                + (1 - tl) * tc * g[il, ic + 1] + tl * tc * g[il + 1, ic + 1])
    hs = np.asarray(model["hs"])
    ax = np.concatenate([H_AXIS - 360, H_AXIS, H_AXIS + 360])
    L2 = L + sample(dL); C2 = C * sample(cr)
    h2 = np.radians(h + np.interp(h, ax, np.tile(hs, 3)))
    return np.stack([L2, C2 * np.cos(h2), C2 * np.sin(h2)], -1)


def tables(data, names, R):
    """One table per group key. The lightness surface is fitted per group --
    the look and its Fade state -- because Fade is what moves it. The chroma
    and hue tables are fitted per look over both Fade states: Fade is a luma
    stage, the chroma residual does not follow it, and a Fade-state's few
    frames of one look are too thin at high chroma to say otherwise (the
    IN+fade held-out frames came back over-saturated when they did)."""
    by_look = {POOLED: fit([data[n][2] for n in names])}
    looks = [group_key(R[n]["meta"]).removesuffix(FADE_SUFFIX) for n in names]
    for look in sorted(set(looks)):
        ns = [n for n, k in zip(names, looks) if k == look]
        if len(ns) >= MIN_FRAMES:
            by_look[look] = fit([data[n][2] for n in ns], by_look[POOLED])
    out = {}
    keys = [group_key(R[n]["meta"]) for n in names]
    for fade in ("", FADE_SUFFIX):
        ns = [n for n, k in zip(names, keys) if k.endswith(FADE_SUFFIX) == bool(fade)]
        if not ns:
            continue
        # The pooled table for this Fade state, which the looks shrink toward.
        pooled = fit([data[n][2] for n in ns])
        out[POOLED + fade] = pooled
        for key in sorted(set(k for n, k in zip(names, keys) if n in set(ns))):
            ns_k = [n for n, k in zip(names, keys) if k == key]
            if len(ns_k) >= MIN_FRAMES:
                out[key] = fit([data[n][2] for n in ns_k], pooled)
    for key, t in out.items():
        chroma = by_look.get(key.removesuffix(FADE_SUFFIX), by_look[POOLED])
        t["cr"], t["hs"] = chroma["cr"], chroma["hs"]
    if POOLED not in out:
        out[POOLED] = {**by_look[POOLED], "dL": out[POOLED + FADE_SUFFIX]["dL"]}
    return out


def group_key(meta) -> str:
    """Which table a frame is fitted into and applied with: the look, and
    whether the shot's Fade is on. Fade splits the frames in two: with it off
    the camera's mid-tones sit ~2.5 L* below the engine's, with it on (any
    amount -- 1 does the same as 6) they sit on them. A table fitted across
    both lands between and leaves each half a full L* off in opposite
    directions, which on the Fade-0 majority reads as a frame still brighter
    than the camera's."""
    return STYLE[int(meta["CreativeStyle"])] + (FADE_SUFFIX if int(float(meta.get("Fade", 0))) else "")


def pick(tables, key):
    """The table for a group key: the look's own, else the pooled one for the
    same Fade state -- Fade moves more than any look does -- else the pooled."""
    fade = key.endswith(FADE_SUFFIX)
    return tables.get(key) or tables.get(POOLED + FADE_SUFFIX if fade else POOLED) or tables[POOLED]


def evaluate(cam, llr):
    """Whole-frame dE00 mean, saturated-pixel dE00 mean and dC*, bright saturated dE00,
    and the mid-tone (L* 30-80) lightness offset llr - cam.

    The saturated / bright masks are taken on the mean of the two renders, not
    on the camera alone: selecting on one side's chroma keeps the pixels whose
    noise pushed that side up, and dC* then reads +0.5 before any correction
    and -0.5 after a correct one.
    """
    d = de2000(cam, llr)
    C = np.hypot(cam[..., 1], cam[..., 2]); Cr = np.hypot(llr[..., 1], llr[..., 2])
    Cm, Lm = (C + Cr) / 2, (cam[..., 0] + llr[..., 0]) / 2
    sat = Cm > 40; bright = sat & (Lm > 60)
    dc = Cr - C
    mid = (Lm > 30) & (Lm < 80)
    return (d.mean(), d[sat].mean() if sat.sum() > 200 else np.nan, dc[sat].mean() if sat.sum() > 200 else np.nan,
            d[bright].mean() if bright.sum() > 200 else np.nan,
            np.median((llr[..., 0] - cam[..., 0])[mid]) if mid.sum() > 200 else np.nan)


def main() -> int:
    d = Path(sys.argv[1])
    suffix = sys.argv[sys.argv.index("--suffix") + 1] if "--suffix" in sys.argv else "llr"
    out = Path(sys.argv[sys.argv.index("--out") + 1]) if "--out" in sys.argv else d / "camera_match.json"
    holdout = float(sys.argv[sys.argv.index("--holdout") + 1]) if "--holdout" in sys.argv else 0.0
    R = json.load(open(d / "residuals.json"))
    names = sorted(n for n, v in R.items() if "meta" in v and int(float(v["meta"]["ISO"])) < 65535
                   and str(v["meta"]["DynamicRangeOptimizer"]) != "20" and 0.05 < v["dE_mean"] < 8
                   and (d / f"{n}.{suffix}.jpg").exists() and not is_m_size(v["meta"]))
    body = subprocess.run(["exiftool", "-fast", "-s", "-s", "-s", "-Model", str(d / f"{names[0]}.ARW")], capture_output=True, text=True).stdout.strip()
    data = {}
    for n in names:
        cam, llr = load(d, n, suffix); data[n] = (cam, llr, stats(cam, llr))
    print(f"{len(names)} frames of {body}, renders *.{suffix}.jpg")
    if holdout:
        rng = np.random.default_rng(3); order = list(names); rng.shuffle(order)
        test = order[: int(len(order) * holdout)]; train = [n for n in order if n not in set(test)]
        t = tables(data, train, R)
        rows = []
        for n in test:
            cam, llr, _ = data[n]
            rows.append(evaluate(cam, llr) + evaluate(cam, apply(llr, pick(t, group_key(R[n]["meta"])))))
        rows = np.array(rows); k = len(rows[0]) // 2
        print(f"held-out {len(test)} frames, before -> after:")
        print(f"  whole frame dE00 mean      {rows[:, 0].mean():.2f} -> {rows[:, k].mean():.2f}   median {np.median(rows[:, 0]):.2f} -> {np.median(rows[:, k]):.2f}")
        print(f"  saturated (C*>40) dE00     {np.nanmean(rows[:, 1]):.2f} -> {np.nanmean(rows[:, k + 1]):.2f}   dC* {np.nanmean(rows[:, 2]):+.2f} -> {np.nanmean(rows[:, k + 2]):+.2f}")
        print(f"  bright saturated dE00      {np.nanmean(rows[:, 3]):.2f} -> {np.nanmean(rows[:, k + 3]):.2f}")
        # The number the eye reads as "brighter than the camera": the frame's
        # mid-tone offset. Its mean says which way the sample leans, its mean
        # absolute value how far a typical frame still is from its camera JPEG.
        print(f"  mid-tone dL* llr-cam       mean {np.nanmean(rows[:, 4]):+.2f} -> {np.nanmean(rows[:, k + 4]):+.2f}   mean |dL*| {np.nanmean(np.abs(rows[:, 4])):.2f} -> {np.nanmean(np.abs(rows[:, k + 4])):.2f}")
    t = tables(data, names, R)
    for st, m in sorted(t.items()):
        cr = np.asarray(m["cr"]); dL = np.asarray(m["dL"])
        print(f"  {st:8s} n={m['frames']:3d}  chroma gain at L*50: C*10 {cr[10, 2]:.3f} C*30 {cr[10, 6]:.3f} C*60 {cr[10, 12]:.3f} C*90 {cr[10, 18]:.3f} | at L*75: C*60 {cr[15, 12]:.3f}"
              f" | dL at C*10: L*30 {dL[6, 2]:+.1f} L*60 {dL[12, 2]:+.1f} L*85 {dL[17, 2]:+.1f} | hue {min(m['hs']):+.1f}..{max(m['hs']):+.1f}")
    existing = json.load(open(out)) if out.exists() else {}
    existing[body] = {"lAxis": L_AXIS.tolist(), "cAxis": C_AXIS.tolist(), "hAxis": H_AXIS.tolist(), **t}
    out.write_text(json.dumps(existing))
    print("wrote", out, out.stat().st_size, "bytes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
