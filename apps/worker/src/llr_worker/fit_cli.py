"""Fit a camera-match correction table from RAW files and their in-camera JPEGs.

Usage:
    llr-fit --style FL --out <sidecar.npz> <raw files or dirs>
    llr-fit --per-photo <raw file>            # one table for that photo alone

Every RAW carries a full-size in-camera JPEG, so the training pairs come for
free. Mixing Creative Looks in one fit averages two different renderings into a
table that matches neither, so sources are grouped by style and a fit covers one.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
from PIL import Image

from .cli import (
    LOCAL_CAMERA_PROFILE_ROOT,
    extract_preview_image,
    find_dcp_by_code,
    find_repo_root,
    is_raw,
    read_exiftool_metadata,
)
from .dcp import ENCODING_SRGB, load_dcp_profile
from .fit_profile import (
    DEFAULT_DIMS,
    FitReport,
    MIN_SAMPLES,
    accumulate,
    apply_correction,
    camera_match_path,
    dcp_base_linear,
    decode_camera_rgb,
    inverse_tone_curve,
    delta_e,
    measure,
    save,
    solve,
)


def camera_jpeg_linear(raw_path: Path, shape: tuple[int, int]) -> np.ndarray:
    """The in-camera JPEG, decoded and resampled onto the RAW render's grid."""
    from io import BytesIO

    from .imported import decode_image_linear

    buffer = BytesIO()
    extract_preview_image(raw_path).save(buffer, format="JPEG", quality=98)
    buffer.seek(0)
    linear, _ = decode_image_linear(buffer)

    height, width = shape
    # Resample in a gamma-ish space: averaging linear light across a downscale
    # brightens edges, and the camera JPEG is a downscale of the same scene.
    encoded = (np.clip(linear, 0, 1) ** (1 / 2.2) * 255).astype(np.uint8)
    resized = np.asarray(Image.fromarray(encoded).resize((width, height), Image.Resampling.LANCZOS))
    return ((resized.astype(np.float32) / 255.0) ** 2.2).astype(np.float32)


def collect_raws(inputs: list[str]) -> list[Path]:
    """Accept files, directories, or a .txt list — a network share holds more
    paths than a command line can carry."""
    out: list[Path] = []
    for item in inputs:
        path = Path(item)
        if path.suffix.lower() == ".txt" and path.is_file():
            out.extend(Path(line.strip()) for line in path.read_text().splitlines()
                       if line.strip() and is_raw(Path(line.strip())))
        elif path.is_dir():
            out.extend(sorted(p for p in path.rglob("*") if is_raw(p)))
        elif is_raw(path):
            out.append(path)
    return out


# Sony reports a Creative Look either as its two-letter code or as the full menu
# name, depending on which tag the body wrote. The Adobe profiles are named by
# code, so everything is normalised to the code.
STYLE_ALIASES = {
    "STANDARD": "ST", "PORTRAIT": "PT", "NEUTRAL": "NT", "VIVID": "VV",
    "VIVID2": "VV2", "FILM": "FL", "INSTANT": "IN", "SOFTHIGHKEY": "SH",
    "BW": "BW", "B/W": "BW", "BLACK&WHITE": "BW", "SEPIA": "SE",
    "LANDSCAPE": "LD",
}


def normalize_style(value: str | None) -> str | None:
    if not value:
        return None
    key = value.strip().upper()
    return STYLE_ALIASES.get(key.replace(" ", "").replace("-", ""), key)


def style_of(raw_path: Path) -> str | None:
    exif = read_exiftool_metadata(raw_path)
    return normalize_style(exif.get("CreativeStyle"))


def load_index(path: Path) -> dict[str, dict[str, str]]:
    """Pre-scanned exiftool output, keyed by SourceFile.

    Reading maker notes off a network share one file at a time costs more than
    the fit itself at this scale, so the scan is done once up front and the
    result reused here.
    """
    import json

    with path.open() as handle:
        records = json.load(handle)
    return {rec["SourceFile"]: rec for rec in records}


def profile_for(root: Path, camera_dir: str, code: str):
    # Resolve through the same convention parser the renderer uses, so the fit
    # trains against the exact .dcp the frontend would load for this style.
    profile_dir = root / LOCAL_CAMERA_PROFILE_ROOT / camera_dir
    path = find_dcp_by_code(profile_dir, code)
    if path is None:
        raise SystemExit(f"No Adobe profile for {code} in {profile_dir}")
    return load_dcp_profile(path)


def main() -> None:
    parser = argparse.ArgumentParser(prog="llr-fit")
    parser.add_argument("inputs", nargs="+", help="RAW files or directories")
    parser.add_argument("--style", help="Creative Look code to fit (default: infer, must be unanimous)")
    parser.add_argument("--camera", default="Sony ILCE-7CM2", help="Adobe profile directory name")
    parser.add_argument("--out", help="Output sidecar .npz")
    parser.add_argument("--per-photo", action="store_true", help="Fit one table per input photo")
    parser.add_argument("--dims", default=",".join(str(d) for d in DEFAULT_DIMS), help="hue,sat,value grid")
    parser.add_argument("--min-samples", type=int, default=MIN_SAMPLES)
    parser.add_argument("--max-size", type=int, default=1400, help="Long edge used for fitting")
    parser.add_argument("--holdout", type=int, default=0, help="Reserve N files for validation")
    parser.add_argument("--index", help="Pre-scanned exiftool -j output (avoids per-file maker-note reads)")
    parser.add_argument("--dro", default="any", choices=["any", "off"],
                        help="'off' keeps only DRO-disabled shots: DRO is spatially adaptive, "
                             "so a global table cannot reproduce it")
    parser.add_argument("--limit", type=int, default=0, help="Cap files per style (after shuffling)")
    parser.add_argument("--seed", type=int, default=0, help="Shuffle seed for train/holdout split")
    parser.add_argument("--fit-value", action="store_true",
                        help="Also fit the value axis. Off by default: with DRO enabled the value "
                             "residual is local, not per-colour, and fitting it bakes in noise")
    args = parser.parse_args()

    root = find_repo_root()
    dims = tuple(int(v) for v in args.dims.split(","))
    if len(dims) != 3:
        raise SystemExit("--dims needs three numbers: hue,sat,value")

    raws = collect_raws(args.inputs)
    if not raws:
        raise SystemExit("No RAW files found")

    index = load_index(Path(args.index)) if args.index else {}

    def meta_for(raw: Path) -> dict[str, str]:
        return index.get(str(raw), {})

    groups: dict[str, list[Path]] = {}
    skipped_dro = 0
    for raw in raws:
        record = meta_for(raw)
        if args.dro == "off" and str(record.get("DynamicRangeOptimizer", "")).strip().lower() not in ("off", ""):
            skipped_dro += 1
            continue
        # style_of re-reads exiftool per file, so only fall back to it when there
        # is no pre-scanned index to consult (the whole reason --index exists).
        inferred = normalize_style(record.get("CreativeStyle")) or (
            style_of(raw) if not index else None
        )
        code = args.style or inferred or "ST"
        groups.setdefault(code.upper(), []).append(raw)

    if skipped_dro:
        print(f"skipped {skipped_dro} files with DRO enabled", file=sys.stderr)

    if args.limit or args.holdout:
        rng = np.random.default_rng(args.seed)
        for code, files in groups.items():
            order = rng.permutation(len(files))
            shuffled = [files[i] for i in order]
            groups[code] = shuffled[: args.limit] if args.limit else shuffled

    if args.per_photo:
        for raw in raws:
            code = args.style or style_of(raw) or "ST"
            out = Path(args.out) if args.out else raw.with_suffix(".hsm.npz")
            run_fit(root, args, dims, [raw], code.upper(), out, [])
        return

    for code, files in sorted(groups.items()):
        holdout = files[: args.holdout] if args.holdout else []
        train = files[args.holdout:] if args.holdout else files
        if not train:
            print(f"[{code}] every file reserved for holdout; skipping", file=sys.stderr)
            continue
        out = Path(args.out) if args.out else camera_match_path(root, args.camera, code)
        run_fit(root, args, dims, train, code, out, holdout)


def run_fit(root, args, dims, train, code, out: Path, holdout) -> None:
    profile = profile_for(root, args.camera, code)
    if profile.tone_curve is None:
        raise SystemExit(f"Profile {code} has no tone curve; cannot invert to scene-linear")
    curve = np.asarray(profile.tone_curve, dtype=np.float32)

    acc = None
    pairs: list[tuple[np.ndarray, np.ndarray]] = []
    for raw in train:
        src, tgt = build_pair(raw, profile, curve, args.max_size)
        acc = accumulate(src, tgt, dims, ENCODING_SRGB, acc)
        pairs.append((src, tgt))
        print(f"[{code}] {raw.name}", file=sys.stderr)

    if acc is None:
        return
    table, fitted = solve(acc, dims, args.min_samples, fit_value=args.fit_value)

    hb = sb = ha = sa = 0.0
    for src, tgt in pairs:
        h0, s0 = measure(src, tgt, ENCODING_SRGB)
        h1, s1 = measure(apply_correction(src, table, dims, ENCODING_SRGB), tgt, ENCODING_SRGB)
        hb += h0; sb += s0; ha += h1; sa += s1
    n = len(pairs)
    report = FitReport(
        cells_total=dims[0] * dims[1] * dims[2], cells_fitted=fitted,
        samples=int(acc["n"].sum()), hue_error_before=hb / n, hue_error_after=ha / n,
        sat_ratio_before=sb / n, sat_ratio_after=sa / n,
    )
    print(f"\n[{code}] train ({n} file{'s' if n != 1 else ''})")
    print("  " + report.summary().replace("\n", "\n  "))

    if holdout:
        print(f"\n[{code}] holdout ({len(holdout)} files, unseen during fit)")
        agg = {"before": [], "after": []}
        for raw in holdout:
            src, tgt = build_pair(raw, profile, curve, args.max_size)
            corrected = apply_correction(src, table, dims, ENCODING_SRGB)
            e0 = delta_e(src, tgt, curve)
            e1 = delta_e(corrected, tgt, curve)
            agg["before"].append(e0["mean"]); agg["after"].append(e1["mean"])
            print(f"  {raw.name}: dE {e0['mean']:.2f} -> {e1['mean']:.2f}  "
                  f"chroma {e0['chroma']:.2f} -> {e1['chroma']:.2f}  (dL {e1['lightness']:.2f})")
        b, a = np.mean(agg["before"]), np.mean(agg["after"])
        wins = sum(x < y for x, y in zip(agg["after"], agg["before"]))
        print(f"  holdout mean dE {b:.2f} -> {a:.2f}  ({100*(b-a)/b:+.0f}%)  "
              f"improved on {wins}/{len(holdout)} files")

    save(out, table, dims, ENCODING_SRGB, {
        "camera": args.camera, "style": code, "files": [p.name for p in train],
        "dims": dims, "minSamples": args.min_samples,
    })
    print(f"  wrote {out}")


def build_pair(raw_path: Path, profile, curve: np.ndarray, max_size: int):
    camera_rgb = decode_camera_rgb(raw_path, half_size=True)
    src = dcp_base_linear(camera_rgb, profile)
    if max_size:
        h, w = src.shape[:2]
        long_edge = max(h, w)
        if long_edge > max_size:
            scale = max_size / long_edge
            nw, nh = max(1, round(w * scale)), max(1, round(h * scale))
            src = np.stack([
                np.asarray(Image.fromarray(src[..., c]).resize((nw, nh), Image.Resampling.LANCZOS))
                for c in range(3)
            ], axis=-1).astype(np.float32)
    display = camera_jpeg_linear(raw_path, src.shape[:2])
    return src, inverse_tone_curve(display, curve)


if __name__ == "__main__":
    main()
