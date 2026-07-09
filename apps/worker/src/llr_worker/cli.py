from __future__ import annotations

import argparse
import functools
import json
import math
import os
import shutil
import struct
import subprocess
import sys
import threading
import traceback
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import rawpy
from PIL import Image, ImageEnhance, ImageFilter, ImageOps

from .dcp import DcpProfile, apply_dcp_profile, load_dcp_profile
from .denoise import DEFAULT_MODEL, denoise_raw_inplace, get_denoiser


RAW_EXTENSIONS = {".arw", ".srf", ".sr2", ".dng", ".cr2", ".cr3", ".nef", ".raf", ".rw2", ".orf"}
LOCAL_CAMERA_PROFILE_ROOT = Path("vendor/adobe-camera-profiles/Camera")
def _build_srgb_u8_lut() -> np.ndarray:
    x = np.linspace(0, 1, 65536, dtype=np.float32)
    encoded = np.where(x <= 0.0031308, x * 12.92, 1.055 * np.power(x, 1 / 2.4) - 0.055)
    return np.round(encoded * 255).astype(np.uint8)


SRGB_U8_LUT = _build_srgb_u8_lut()


@dataclass(frozen=True)
class RawMetadata:
    make: str | None
    model: str | None
    lens_model: str | None
    creative_style: str | None
    white_balance: str | None
    camera_white_balance: list[float] | None
    black_level: list[int] | None
    white_level: int | None
    rgb_xyz_matrix: list[list[float]] | None
    # Full-resolution output dimensions (before half_size binning / max_size
    # downscaling), so the frontend can report zoom relative to the original.
    full_width: int | None
    full_height: int | None


@dataclass
class PreparedLinear:
    linear: np.ndarray
    metadata: RawMetadata
    color_profile: dict[str, Any]


PREPARED_CACHE: OrderedDict[tuple[Any, ...], PreparedLinear] = OrderedDict()
PREPARED_CACHE_MAX = 4

# Cache raw-decoded camera RGB data keyed by (sourcePath, halfSize, maxSize,
# denoiseModel). DCP code changes re-apply DCP on cached data instead of
# re-decoding the RAW file. Noisy and denoised variants of a source are cached
# under separate keys (denoiseModel = "" vs the model id).
RAW_CAMERA_CACHE: OrderedDict[tuple[Any, ...], tuple[np.ndarray, RawMetadata]] = OrderedDict()
RAW_CAMERA_CACHE_MAX = 6

# No-DCP fallback decode cache, keyed like RAW_CAMERA_CACHE. There is no DCP step
# to re-apply, so this stores the final ProPhoto linear — important once denoise
# makes a re-decode cost seconds (so amount tweaks must not re-run inference).
_FALLBACK_CACHE: OrderedDict[tuple[Any, ...], tuple[np.ndarray, RawMetadata, dict[str, Any]]] = OrderedDict()
_FALLBACK_CACHE_MAX = 6


NEUTRAL_RECIPE: dict[str, Any] = {
    "profileId": "neutral",
    "autoTone": False,
    "exposure": 0.0,
    "contrast": 0.0,
    "highlights": 0.0,
    "shadows": 0.0,
    "whites": 0.0,
    "blacks": 0.0,
    "vibrance": 0.0,
    "saturation": 0.0,
    "clarity": 0.0,
    "dehaze": 0.0,
    "sharpen": 0.0,
    "toneCurve": [
        {"x": 0.0, "y": 0.0},
        {"x": 255.0, "y": 255.0},
    ],
}

PROFILES: dict[str, dict[str, Any]] = {
    "neutral": NEUTRAL_RECIPE,
    "standard": {
        **NEUTRAL_RECIPE,
        "profileId": "standard",
        "autoTone": False,
        "exposure": 0.0,
        "contrast": 0.0,
        "highlights": 0.0,
        "shadows": 0.0,
        "vibrance": 0.0,
        "saturation": 0.0,
        "sharpen": 18.0,
        "toneCurve": [
            {"x": 0.0, "y": 0.0},
            {"x": 32.0, "y": 22.0},
            {"x": 128.0, "y": 130.0},
            {"x": 224.0, "y": 236.0},
            {"x": 255.0, "y": 255.0},
        ],
    },
}


def main() -> None:
    argv = sys.argv[1:]
    if argv[:1] == ["--"]:
        argv = argv[1:]

    parser = build_parser()
    args = parser.parse_args(argv)
    root = find_repo_root()

    try:
        if args.command == "extract-preview":
            extract_preview(resolve_path(root, args.input), resolve_path(root, args.output))
            print(f"Wrote {resolve_path(root, args.output)}")
            return

        if args.command == "preview":
            render_preview(args, root)
            return

        if args.command == "export":
            render_export(args, root)
            return

        if args.command == "daemon":
            run_daemon(root)
            return
    except Exception as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1) from error


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="llr-worker")
    subparsers = parser.add_subparsers(dest="command", required=True)

    extract = subparsers.add_parser("extract-preview")
    extract.add_argument("input")
    extract.add_argument("output")

    preview = subparsers.add_parser("preview")
    preview.add_argument("input")
    preview.add_argument("output")

    export = subparsers.add_parser("export")
    export.add_argument("input")
    export.add_argument("output")
    export.add_argument("--profile", default="standard", choices=sorted(PROFILES))
    export.add_argument("--dcp", help="Optional external DCP camera profile. Standard auto-detects local profiles when omitted.")
    export.add_argument("--no-dcp", action="store_true", help="Disable local DCP auto-detection.")
    export.add_argument("--lut", help="Optional .cube LUT applied in display sRGB space.")
    export.add_argument("--auto-tone", dest="auto_tone", action="store_true", default=None)
    export.add_argument("--no-auto-tone", dest="auto_tone", action="store_false")
    export.add_argument("--half-size", dest="half_size", action="store_true", help="Decode the RAW with 2x2 binning for ~2x faster preview.")
    export.add_argument("--max-size", dest="max_size", type=int, help="Down-scale the output JPEG so its long edge fits this many pixels.")
    export.add_argument("--preview", action="store_true", help="Shortcut for --half-size --max-size 1600.")

    subparsers.add_parser("daemon")
    for key in [
        "exposure",
        "contrast",
        "highlights",
        "shadows",
        "whites",
        "blacks",
        "vibrance",
        "saturation",
        "clarity",
        "dehaze",
        "sharpen",
    ]:
        export.add_argument(f"--{key}", type=float)

    return parser


@functools.lru_cache(maxsize=1)
def find_repo_root() -> Path:
    start = Path(os.environ.get("INIT_CWD", os.getcwd())).resolve()
    for candidate in [start, *start.parents]:
        if (candidate / "pnpm-workspace.yaml").exists():
            return candidate
    return start


def resolve_path(root: Path, value: str) -> Path:
    if is_windows_drive_path(value):
        drive = value[0].lower()
        rest = value[2:].lstrip("\\/")
        return (Path("/mnt") / drive / rest.replace("\\", "/")).resolve()

    path = Path(value)
    return path if path.is_absolute() else (root / path).resolve()


def is_windows_drive_path(value: str) -> bool:
    return len(value) >= 3 and value[1] == ":" and value[0].isalpha() and value[2] in {"\\", "/"}


def is_raw(path: Path) -> bool:
    return path.suffix.lower() in RAW_EXTENSIONS


def render_preview(args: argparse.Namespace, root: Path) -> None:
    input_path = resolve_path(root, args.input)
    output_path = resolve_path(root, args.output)

    if is_raw(input_path):
        image = extract_preview_image(input_path)
    else:
        image = open_rgb(input_path)

    image.thumbnail((2400, 2400), Image.Resampling.LANCZOS)
    save_jpeg(image, output_path, quality=86)
    print(f"Wrote {output_path}")


def render_export(args: argparse.Namespace, root: Path) -> None:
    input_path = resolve_path(root, args.input)
    output_path = resolve_path(root, args.output)
    recipe = merge_recipe(PROFILES[args.profile], recipe_overrides(args))
    lut = load_cube_lut(resolve_path(root, args.lut)) if args.lut else None
    half_size = bool(getattr(args, "half_size", False) or getattr(args, "preview", False))
    max_size: int | None = getattr(args, "max_size", None)
    if getattr(args, "preview", False) and max_size is None:
        max_size = 1600
    quality = 86 if half_size else 92

    if is_raw(input_path):
        image, metadata, auto_tone, color_profile = render_raw(
            input_path,
            recipe,
            lut,
            root,
            dcp_arg=args.dcp,
            disable_dcp=bool(args.no_dcp),
            half_size=half_size,
            max_size=max_size,
        )
    else:
        image = apply_display_recipe(open_rgb(input_path), recipe)
        if lut:
            image = Image.fromarray(apply_cube_lut(np.asarray(image, dtype=np.uint8), lut), mode="RGB")
        if max_size:
            image.thumbnail((max_size, max_size), Image.Resampling.LANCZOS)
        metadata = None
        auto_tone = None
        color_profile = None

    save_jpeg(image, output_path, quality=quality)
    print(
        json.dumps(
            {
                "output": str(output_path),
                "pipeline": "rawpy-libraw+dcp" if is_dcp_color_profile(color_profile) else "rawpy-libraw",
                "metadata": metadata_to_json(metadata),
                "recipe": recipe,
                "colorProfile": color_profile,
                "autoTone": auto_tone,
                "lut": str(resolve_path(root, args.lut)) if args.lut else None,
                "halfSize": half_size,
                "maxSize": max_size,
            },
            indent=2,
        )
    )


# Daemon concurrency: requests run on a small thread pool so a multi-second
# denoise render-linear cannot block cheap commands (ping, export) for other
# sources. Requests for the same source (the `input` path, which keys every
# global cache) are serialised via a per-source lock — the caches assume no
# same-source concurrency. _CACHE_LOCK guards the cache dicts themselves
# (lookup/insert/eviction) against cross-source races.
DAEMON_MAX_WORKERS = 3

_STDOUT_LOCK = threading.Lock()
_CACHE_LOCK = threading.Lock()
_SOURCE_LOCKS: dict[str, threading.Lock] = {}
_SOURCE_LOCKS_GUARD = threading.Lock()


def _source_lock(source: str) -> threading.Lock:
    with _SOURCE_LOCKS_GUARD:
        lock = _SOURCE_LOCKS.get(source)
        if lock is None:
            lock = threading.Lock()
            _SOURCE_LOCKS[source] = lock
        return lock


def run_daemon(root: Path) -> None:
    sys.stderr.write("llr-worker daemon ready\n")
    sys.stderr.flush()
    # Exiting the `with` block waits for all in-flight tasks after stdin EOF.
    with ThreadPoolExecutor(max_workers=DAEMON_MAX_WORKERS) as executor:
        for raw_line in sys.stdin:
            line = raw_line.strip()
            if not line:
                continue
            try:
                request = json.loads(line)
            except json.JSONDecodeError as error:
                emit_response({"id": None, "ok": False, "error": f"invalid JSON: {error}"})
                continue
            executor.submit(daemon_worker, request, root)


def daemon_worker(request: dict[str, Any], root: Path) -> None:
    request_id = request.get("id")
    source = request.get("input")
    guard = _source_lock(source) if isinstance(source, str) and source else nullcontext()
    try:
        with guard:
            response = handle_daemon_request(request, root)
        response["id"] = request_id
        response["ok"] = True
        emit_response(response)
    except Exception as error:  # noqa: BLE001
        traceback.print_exc(file=sys.stderr)
        emit_response({"id": request_id, "ok": False, "error": str(error)})


def emit_response(payload: dict[str, Any]) -> None:
    line = json.dumps(payload) + "\n"
    with _STDOUT_LOCK:
        sys.stdout.write(line)
        sys.stdout.flush()


def handle_daemon_request(request: dict[str, Any], root: Path) -> dict[str, Any]:
    command = request.get("command", "render")
    if command == "render":
        return daemon_render(request, root)
    if command == "render-linear":
        return daemon_linear(request, root)
    if command == "extract-preview":
        return daemon_extract_preview(request, root)
    if command == "export":
        return daemon_export(request, root)
    if command == "ping":
        return {"pong": True, "cacheSize": len(PREPARED_CACHE)}
    raise ValueError(f"unknown command: {command}")


def daemon_render(request: dict[str, Any], root: Path) -> dict[str, Any]:
    input_path = resolve_path(root, request["input"])
    output_path = resolve_path(root, request["output"])
    profile_id = request.get("profile") or "standard"
    if profile_id not in PROFILES:
        profile_id = "standard"
    disable_dcp = bool(request.get("disableDcp", False))
    dcp_arg = request.get("dcp")
    half_size = bool(request.get("halfSize", False))
    max_size = request.get("maxSize")
    if isinstance(max_size, str):
        max_size = int(max_size) if max_size else None
    lut_path = request.get("lut")
    auto_tone_value: bool | None = None
    if "autoTone" in request and request["autoTone"] is not None:
        auto_tone_value = bool(request["autoTone"])

    overrides: dict[str, Any] = {}
    raw_recipe = request.get("recipe") or {}
    for key in [
        "exposure",
        "contrast",
        "highlights",
        "shadows",
        "whites",
        "blacks",
        "vibrance",
        "saturation",
        "clarity",
        "dehaze",
        "sharpen",
    ]:
        value = raw_recipe.get(key)
        if value is None:
            continue
        try:
            overrides[key] = float(value)
        except (TypeError, ValueError):
            continue

    if auto_tone_value is not None:
        overrides["autoTone"] = auto_tone_value

    recipe = merge_recipe(PROFILES[profile_id], overrides)
    lut = load_cube_lut(resolve_path(root, lut_path)) if lut_path else None

    cache_key = build_cache_key(input_path, profile_id, dcp_arg, disable_dcp, half_size, max_size)
    with _CACHE_LOCK:
        prepared = PREPARED_CACHE.get(cache_key)
        if prepared is not None:
            PREPARED_CACHE.move_to_end(cache_key)
    cache_hit = prepared is not None
    if prepared is None:
        prepared = prepare_linear(
            input_path,
            recipe,
            root,
            dcp_arg=dcp_arg,
            disable_dcp=disable_dcp,
            half_size=half_size,
            max_size=max_size,
        )
        with _CACHE_LOCK:
            PREPARED_CACHE[cache_key] = prepared
            while len(PREPARED_CACHE) > PREPARED_CACHE_MAX:
                PREPARED_CACHE.popitem(last=False)

    image, auto_tone = finalize_image(prepared, recipe, lut, max_size=None)
    quality = 82 if half_size else 92
    save_jpeg(image, output_path, quality=quality)

    return {
        "output": str(output_path),
        "metadata": metadata_to_json(prepared.metadata),
        "colorProfile": prepared.color_profile,
        "pipeline": "rawpy-libraw+dcp" if is_dcp_color_profile(prepared.color_profile) else "rawpy-libraw",
        "autoTone": auto_tone,
        "cached": cache_hit,
        "halfSize": half_size,
        "maxSize": max_size,
    }


def _linear_cache_key(
    input_path: Path,
    half_size: bool,
    max_size: int | None,
    dcp_code: str | None,
    denoise_model: str | None,
    denoise_amount: float,
) -> tuple[Any, ...]:
    try:
        st = input_path.stat()
        base = (str(input_path), st.st_size, int(st.st_mtime_ns))
    except OSError:
        base = (str(input_path),)
    return base + (
        bool(half_size),
        int(max_size or 0),
        dcp_code or "",
        denoise_model or "",
        round(float(denoise_amount), 3),
    )


_LINEAR_CACHE: OrderedDict[tuple[Any, ...], tuple[np.ndarray, dict[str, Any]]] = OrderedDict()
_LINEAR_CACHE_MAX = 8


def daemon_linear(request: dict[str, Any], root: Path) -> dict[str, Any]:
    """Decode RAW + DCP, return raw float32 linear data (no JPEG)."""
    input_path = resolve_path(root, request["input"])
    output_path = resolve_path(root, request["output"])
    profile_id = request.get("profile") or "standard"
    if profile_id not in PROFILES:
        profile_id = "standard"
    half_size = bool(request.get("halfSize", True))
    max_size: int | None = request.get("maxSize")
    if isinstance(max_size, str):
        max_size = int(max_size) if max_size else None
    dcp_code: str | None = request.get("dcpCode")

    # RAW-domain denoise request. amount<=0 (or disabled) is treated as off so
    # the heavy inference and the second decode are skipped entirely.
    denoise_req = request.get("denoise") or {}
    dn_amount = max(0.0, min(1.0, float(denoise_req.get("amount", 1.0))))
    dn_model: str | None = None
    if bool(denoise_req.get("enabled", False)) and dn_amount > 0.0:
        dn_model = str(denoise_req.get("model") or DEFAULT_MODEL)

    # Check processed sRGB cache first
    cache_key = _linear_cache_key(input_path, half_size, max_size, dcp_code, dn_model, dn_amount)
    with _CACHE_LOCK:
        cached_linear = _LINEAR_CACHE.get(cache_key)
        if cached_linear is not None:
            _LINEAR_CACHE.move_to_end(cache_key)
    if cached_linear is not None:
        linear_arr, color_profile = cached_linear
        linear = linear_arr.tobytes()
        with open(output_path, "wb") as f:
            f.write(linear)
        return {
            "width": linear_arr.shape[1],
            "height": linear_arr.shape[0],
            "fullWidth": color_profile.get("fullWidth"),
            "fullHeight": color_profile.get("fullHeight"),
            "output": str(output_path),
            "colorProfile": color_profile,
            "bytesWritten": len(linear),
        }

    recipe = merge_recipe(PROFILES[profile_id], {})
    if dcp_code:
        recipe["dcpCode"] = dcp_code

    prepared = prepare_linear(
        input_path, recipe, root,
        dcp_arg=None, disable_dcp=False,
        half_size=half_size, max_size=max_size,
    )

    # Blend the denoised decode over the noisy one by the requested amount. The
    # heavy inference happens inside this second prepare_linear and is cached by
    # cache_key (denoise_model), so amount changes only re-run this cheap lerp.
    if dn_model is not None:
        prepared_dn = prepare_linear(
            input_path, recipe, root,
            dcp_arg=None, disable_dcp=False,
            half_size=half_size, max_size=max_size,
            denoise_model=dn_model,
        )
        blended = prepared.linear * (1.0 - dn_amount) + prepared_dn.linear * dn_amount
        prepared = PreparedLinear(
            linear=blended.astype(np.float32),
            metadata=prepared.metadata,
            color_profile=prepared.color_profile,
        )

    # Stash full-resolution dims on the color profile so both this response and
    # the _LINEAR_CACHE-hit path above can report zoom relative to the original.
    prepared.color_profile["fullWidth"] = prepared.metadata.full_width
    prepared.color_profile["fullHeight"] = prepared.metadata.full_height

    # Cache processed sRGB so switching back to this DCP code is instant. The
    # decode/DCP/downsample paths already yield C-contiguous float32, so this is a
    # no-op there (avoids a full ~tens-of-MB copy) and only copies when it must.
    linear_arr = np.ascontiguousarray(prepared.linear, dtype=np.float32)
    # Skip caching full-resolution exports (half_size off and no max_size cap): a
    # single entry can be hundreds of MB and exports are one-off, so caching them
    # would pin gigabytes with no reuse. Previews (half/max-size capped) still cache.
    if half_size or max_size:
        with _CACHE_LOCK:
            _LINEAR_CACHE[cache_key] = (linear_arr, prepared.color_profile)
            while len(_LINEAR_CACHE) > _LINEAR_CACHE_MAX:
                _LINEAR_CACHE.popitem(last=False)

    linear = linear_arr.tobytes()
    with open(output_path, "wb") as f:
        f.write(linear)

    return {
        "width": prepared.linear.shape[1],
        "height": prepared.linear.shape[0],
        "fullWidth": prepared.metadata.full_width,
        "fullHeight": prepared.metadata.full_height,
        "output": str(output_path),
        "colorProfile": prepared.color_profile,
        "bytesWritten": len(linear),
    }


def daemon_extract_preview(request: dict[str, Any], root: Path) -> dict[str, Any]:
    input_path = resolve_path(root, request["input"])
    output_path = resolve_path(root, request["output"])
    extract_preview(input_path, output_path)
    return {"output": str(output_path)}


# ── Export: embed edit settings into the rendered JPEG as XMP ──

LLR_XMP_NS = "http://ns.llr.app/xmp/1.0/"
CRS_XMP_NS = "http://ns.adobe.com/camera-raw-settings/1.0/"
HSL_CRS_NAMES = ["Red", "Orange", "Yellow", "Green", "Aqua", "Blue", "Purple", "Magenta"]


def daemon_export(request: dict[str, Any], root: Path) -> dict[str, Any]:
    """Embed the edit recipe into an already-rendered JPEG as XMP metadata.

    The frontend renders the full-resolution image via WebGL and posts the JPEG;
    here we (best-effort) copy the original camera EXIF for provenance and inject
    an XMP packet carrying both a lossless LLR JSON blob and Adobe crs fields.
    """
    input_path = resolve_path(root, request["input"])
    target_path = resolve_path(root, request["target"])
    settings = request.get("settings") or {}

    exif_copied = copy_exif_provenance(input_path, target_path)

    xmp = build_xmp_packet(settings)
    data = embed_xmp_app1(target_path.read_bytes(), xmp)
    target_path.write_bytes(data)

    return {"output": str(target_path), "exifCopied": exif_copied, "xmpBytes": len(xmp)}


def copy_exif_provenance(original: Path, target: Path) -> bool:
    """Copy a curated set of camera EXIF tags from the original into the export."""
    command = detect_exiftool()
    if command is None:
        return False
    try:
        run_capture(
            [
                command,
                "-overwrite_original",
                "-tagsFromFile",
                str(original),
                "-Make",
                "-Model",
                "-LensModel",
                "-LensInfo",
                "-DateTimeOriginal",
                "-CreateDate",
                "-OffsetTime",
                "-FNumber",
                "-ExposureTime",
                "-ISO",
                "-FocalLength",
                "-FocalLengthIn35mmFormat",
                "-ExposureProgram",
                "-MeteringMode",
                "-Flash",
                "-Orientation#=1",
                "-Software=LLR",
                str(target),
            ],
            env=exiftool_env(),
        )
        return True
    except subprocess.CalledProcessError:
        return False


def _num(source: dict[str, Any], key: str, default: float = 0.0) -> float:
    try:
        return float(source.get(key, default))
    except (TypeError, ValueError):
        return default


def build_crs_attrs(settings: dict[str, Any]) -> "OrderedDict[str, str]":
    recipe = settings.get("recipe", {}) or {}
    attrs: OrderedDict[str, str] = OrderedDict()
    attrs["crs:Version"] = "15.0"
    attrs["crs:ProcessVersion"] = "11.0"
    attrs["crs:Exposure2012"] = f"{_num(recipe, 'exposure'):+.2f}"
    attrs["crs:Contrast2012"] = f"{int(round(_num(recipe, 'contrast')))}"
    attrs["crs:Highlights2012"] = f"{int(round(_num(recipe, 'highlights')))}"
    attrs["crs:Shadows2012"] = f"{int(round(_num(recipe, 'shadows')))}"
    attrs["crs:Whites2012"] = f"{int(round(_num(recipe, 'whites')))}"
    attrs["crs:Blacks2012"] = f"{int(round(_num(recipe, 'blacks')))}"
    attrs["crs:Clarity2012"] = f"{int(round(_num(recipe, 'clarity')))}"
    attrs["crs:Dehaze"] = f"{int(round(_num(recipe, 'dehaze')))}"
    attrs["crs:Vibrance"] = f"{int(round(_num(recipe, 'vibrance')))}"
    attrs["crs:Saturation"] = f"{int(round(_num(recipe, 'saturation')))}"
    attrs["crs:WhiteBalance"] = "Custom"
    attrs["crs:Temperature"] = f"{int(round(_num(recipe, 'temperature', 6500)))}"
    attrs["crs:Tint"] = f"{int(round(_num(recipe, 'tint')))}"

    # Parametric (region) tone curve — Lightroom crs:Parametric* fields.
    parametric = _curve_settings(settings).get("parametric", {}) or {}
    attrs["crs:ParametricShadows"] = f"{int(round(_num(parametric, 'shadows')))}"
    attrs["crs:ParametricDarks"] = f"{int(round(_num(parametric, 'darks')))}"
    attrs["crs:ParametricLights"] = f"{int(round(_num(parametric, 'lights')))}"
    attrs["crs:ParametricHighlights"] = f"{int(round(_num(parametric, 'highlights')))}"
    attrs["crs:ParametricShadowSplit"] = f"{int(round(_num(parametric, 'shadowSplit', 25)))}"
    attrs["crs:ParametricMidtoneSplit"] = f"{int(round(_num(parametric, 'midtoneSplit', 50)))}"
    attrs["crs:ParametricHighlightSplit"] = f"{int(round(_num(parametric, 'highlightSplit', 75)))}"

    hue = settings.get("hslHue") or []
    sat = settings.get("hslSat") or []
    lum = settings.get("hslLum") or []
    for i, name in enumerate(HSL_CRS_NAMES):
        if i < len(hue):
            attrs[f"crs:HueAdjustment{name}"] = f"{int(round(float(hue[i])))}"
        if i < len(sat):
            attrs[f"crs:SaturationAdjustment{name}"] = f"{int(round(float(sat[i])))}"
        if i < len(lum):
            attrs[f"crs:LuminanceAdjustment{name}"] = f"{int(round(float(lum[i])))}"

    grading = settings.get("grading", {}) or {}

    def hue360(value: float) -> int:
        return int(round(((value % 360) + 360) % 360))

    attrs["crs:SplitToningShadowHue"] = f"{hue360(_num(grading, 'shH'))}"
    attrs["crs:SplitToningShadowSaturation"] = f"{int(round(_num(grading, 'shS')))}"
    attrs["crs:SplitToningHighlightHue"] = f"{hue360(_num(grading, 'hlH'))}"
    attrs["crs:SplitToningHighlightSaturation"] = f"{int(round(_num(grading, 'hlS')))}"
    attrs["crs:SplitToningBalance"] = f"{int(round(_num(grading, 'balance')))}"

    _add_crop_attrs(attrs, settings.get("crop") or {})
    return attrs


def _add_crop_attrs(attrs: "OrderedDict[str, str]", crop: dict[str, Any]) -> None:
    """Adobe crs crop fields, written only when the crop has an effect.

    The exported JPEG is already cropped/straightened in pixels; these mirror the
    recompose for Lightroom-compatible round-tripping alongside the lossless
    llr:Settings blob (which also carries flips/orientation)."""
    cx = _num(crop, "cx", 0.5)
    cy = _num(crop, "cy", 0.5)
    w = _num(crop, "w", 1.0)
    h = _num(crop, "h", 1.0)
    angle = _num(crop, "angle", 0.0)
    flip_h = bool(crop.get("flipH"))
    flip_v = bool(crop.get("flipV"))
    orientation = int(_num(crop, "orientation", 0.0))

    def near(a: float, b: float) -> bool:
        return abs(a - b) < 1e-3

    is_default = (
        near(cx, 0.5) and near(cy, 0.5) and near(w, 1.0) and near(h, 1.0)
        and near(angle, 0.0) and not flip_h and not flip_v and orientation == 0
    )
    if is_default:
        return

    left = clamp(cx - w / 2, 0.0, 1.0)
    right = clamp(cx + w / 2, 0.0, 1.0)
    top = clamp(cy - h / 2, 0.0, 1.0)
    bottom = clamp(cy + h / 2, 0.0, 1.0)
    attrs["crs:HasCrop"] = "True"
    attrs["crs:CropLeft"] = f"{left:.6f}"
    attrs["crs:CropTop"] = f"{top:.6f}"
    attrs["crs:CropRight"] = f"{right:.6f}"
    attrs["crs:CropBottom"] = f"{bottom:.6f}"
    attrs["crs:CropAngle"] = f"{angle:.4f}"
    attrs["crs:CropConstrainToWarp"] = "1"


def _curve_settings(settings: dict[str, Any]) -> dict[str, Any]:
    """Return the tone curve as a dict, tolerating the legacy flat-list format."""
    curve = settings.get("curve")
    if isinstance(curve, list):  # legacy: a bare RGB point list
        return {"rgb": curve}
    if isinstance(curve, dict):
        return curve
    return {}


def _tone_curve_points(points: Any) -> list[str]:
    """Convert a list of {x,y} (0-1) control points to Lightroom "X, Y" (0-255)."""
    out: list[str] = []
    for point in points or []:
        try:
            x = int(round(clamp(float(point["x"]) * 255, 0, 255)))
            y = int(round(clamp(float(point["y"]) * 255, 0, 255)))
        except (KeyError, TypeError, ValueError):
            continue
        out.append(f"{x}, {y}")
    return out


def _is_identity_curve(points: list[str]) -> bool:
    return points in ([], ["0, 0", "255, 255"])


def _xml_escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _xml_attr(text: str) -> str:
    return _xml_escape(text).replace('"', "&quot;")


def _tone_curve_block(tag: str, points: list[str]) -> str:
    if not points:
        return ""
    items = "\n     ".join(f"<rdf:li>{_xml_escape(point)}</rdf:li>" for point in points)
    return (
        f"\n   <crs:{tag}>\n    <rdf:Seq>\n     "
        + items
        + f"\n    </rdf:Seq>\n   </crs:{tag}>"
    )


def build_xmp_packet(settings: dict[str, Any]) -> str:
    crs_attrs = build_crs_attrs(settings)
    curve = _curve_settings(settings)
    llr_json = json.dumps(settings, separators=(",", ":"), ensure_ascii=False)

    attr_lines = "\n   ".join(f'{key}="{_xml_attr(value)}"' for key, value in crs_attrs.items())

    # RGB master curve is always written; per-channel curves only when non-identity.
    tone_block = _tone_curve_block("ToneCurvePV2012", _tone_curve_points(curve.get("rgb")))
    for tag, key in (("ToneCurvePV2012Red", "red"), ("ToneCurvePV2012Green", "green"), ("ToneCurvePV2012Blue", "blue")):
        pts = _tone_curve_points(curve.get(key))
        if not _is_identity_curve(pts):
            tone_block += _tone_curve_block(tag, pts)

    return (
        '<?xpacket begin="﻿" id="W5M0MpCehiHzreSzNTczkc9d"?>\n'
        '<x:xmpmeta xmlns:x="adobe:ns:meta/" x:xmptk="LLR">\n'
        ' <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">\n'
        '  <rdf:Description rdf:about=""\n'
        f'   xmlns:crs="{CRS_XMP_NS}"\n'
        f'   xmlns:llr="{LLR_XMP_NS}"\n'
        f"   {attr_lines}\n"
        '   llr:Version="1">'
        f"{tone_block}\n"
        f"   <llr:Settings>{_xml_escape(llr_json)}</llr:Settings>\n"
        "  </rdf:Description>\n"
        " </rdf:RDF>\n"
        "</x:xmpmeta>\n"
        '<?xpacket end="w"?>'
    )


def embed_xmp_app1(jpeg: bytes, xmp_packet: str) -> bytes:
    """Insert an XMP APP1 segment into a JPEG without re-encoding pixels."""
    if jpeg[:2] != b"\xff\xd8":
        raise ValueError("export target is not a JPEG file")
    payload = b"http://ns.adobe.com/xap/1.0/\x00" + xmp_packet.encode("utf-8")
    if len(payload) + 2 > 0xFFFF:
        raise ValueError("XMP packet too large for a single APP1 segment")
    segment = b"\xff\xe1" + struct.pack(">H", len(payload) + 2) + payload

    # Insert after the SOI and any leading APP0/APP1 (JFIF/EXIF) segments.
    insert_at = 2
    while jpeg[insert_at : insert_at + 2] in (b"\xff\xe0", b"\xff\xe1"):
        seg_len = struct.unpack(">H", jpeg[insert_at + 2 : insert_at + 4])[0]
        insert_at += 2 + seg_len
    return jpeg[:insert_at] + segment + jpeg[insert_at:]


def build_cache_key(
    input_path: Path,
    profile_id: str,
    dcp_arg: str | None,
    disable_dcp: bool,
    half_size: bool,
    max_size: int | None,
) -> tuple[Any, ...]:
    try:
        stat = input_path.stat()
        signature: tuple[Any, ...] = (stat.st_size, int(stat.st_mtime_ns))
    except OSError:
        signature = (None,)
    return (
        str(input_path),
        profile_id,
        dcp_arg or "",
        bool(disable_dcp),
        bool(half_size),
        int(max_size) if max_size else 0,
        signature,
    )


def recipe_overrides(args: argparse.Namespace) -> dict[str, Any]:
    overrides = {
        key: getattr(args, key)
        for key in [
            "exposure",
            "contrast",
            "highlights",
            "shadows",
            "whites",
            "blacks",
            "vibrance",
            "saturation",
            "clarity",
            "dehaze",
            "sharpen",
        ]
    }
    overrides["autoTone"] = args.auto_tone
    return overrides


def merge_recipe(base: dict[str, Any], overrides: dict[str, Any]) -> dict[str, Any]:
    return {**base, **{key: value for key, value in overrides.items() if value is not None}}


def extract_preview(input_path: Path, output_path: Path) -> None:
    image = extract_preview_image(input_path) if is_raw(input_path) else open_rgb(input_path)
    save_jpeg(image, output_path, quality=92)


def extract_preview_image(input_path: Path) -> Image.Image:
    with rawpy.imread(str(input_path)) as raw:
        try:
            thumb = raw.extract_thumb()
        except rawpy.LibRawNoThumbnailError:
            rendered = raw.postprocess(
                use_camera_wb=True,
                no_auto_bright=True,
                output_color=rawpy.ColorSpace.sRGB,
                gamma=(2.222, 4.5),
                output_bps=8,
            )
            return Image.fromarray(rendered, mode="RGB")

        if thumb.format == rawpy.ThumbFormat.JPEG:
            from io import BytesIO

            return open_rgb(BytesIO(thumb.data))

        if thumb.format == rawpy.ThumbFormat.BITMAP:
            return Image.fromarray(thumb.data, mode="RGB")

    raise ValueError(f"Unsupported RAW thumbnail in {input_path}")


def render_raw(
    input_path: Path,
    recipe: dict[str, Any],
    lut: CubeLut | None,
    root: Path,
    dcp_arg: str | None,
    disable_dcp: bool,
    half_size: bool = False,
    max_size: int | None = None,
) -> tuple[Image.Image, RawMetadata, dict[str, Any] | None, dict[str, Any]]:
    prepared = prepare_linear(
        input_path,
        recipe,
        root,
        dcp_arg=dcp_arg,
        disable_dcp=disable_dcp,
        half_size=half_size,
        max_size=max_size,
    )
    image, auto_tone = finalize_image(prepared, recipe, lut, max_size=None)
    return image, prepared.metadata, auto_tone, prepared.color_profile


def _raw_cache_key(
    input_path: Path, half_size: bool, max_size: int | None, denoise_model: str | None
) -> tuple[Any, ...]:
    try:
        stat = input_path.stat()
        base = (str(input_path), bool(half_size), int(max_size or 0), stat.st_size, int(stat.st_mtime_ns))
    except OSError:
        base = (str(input_path), bool(half_size), int(max_size or 0))
    return base + (denoise_model or "",)


def prepare_linear(
    input_path: Path,
    recipe: dict[str, Any],
    root: Path,
    dcp_arg: str | None,
    disable_dcp: bool,
    half_size: bool = False,
    max_size: int | None = None,
    denoise_model: str | None = None,
) -> PreparedLinear:
    cache_key = _raw_cache_key(input_path, half_size, max_size, denoise_model)

    # Cache hit: re-apply DCP on cached camera RGB without re-decoding RAW
    with _CACHE_LOCK:
        cached_camera = RAW_CAMERA_CACHE.get(cache_key)
        if cached_camera is not None:
            RAW_CAMERA_CACHE.move_to_end(cache_key)
    if cached_camera is not None:
        camera_rgb, metadata = cached_camera
        dcp_profile, dcp_selection = resolve_dcp_profile(root, dcp_arg, disable_dcp, recipe, metadata)
        if dcp_profile is not None:
            linear, dcp_info = apply_dcp_profile(camera_rgb, dcp_profile)
            color_profile = dcp_info.to_json()
            color_profile["selection"] = dcp_selection
            return PreparedLinear(linear=linear, metadata=metadata, color_profile=color_profile)
        # DCP no longer available; fall through to re-decode
        with _CACHE_LOCK:
            RAW_CAMERA_CACHE.pop(cache_key, None)

    # Cache hit for the no-DCP fallback: linear is final unless DCP reappeared.
    with _CACHE_LOCK:
        cached_fallback = _FALLBACK_CACHE.get(cache_key)
        if cached_fallback is not None:
            _FALLBACK_CACHE.move_to_end(cache_key)
    if cached_fallback is not None:
        linear, metadata, color_profile = cached_fallback
        dcp_profile, _ = resolve_dcp_profile(root, dcp_arg, disable_dcp, recipe, metadata)
        if dcp_profile is None:
            return PreparedLinear(linear=linear, metadata=metadata, color_profile=color_profile)
        with _CACHE_LOCK:
            _FALLBACK_CACHE.pop(cache_key, None)

    with rawpy.imread(str(input_path)) as raw:
        metadata = read_raw_metadata(input_path, raw)
        # RAW-domain denoise: clean the Bayer mosaic in place so the postprocess
        # calls below demosaic the denoised data. Heavy, so it is cached via
        # cache_key (which includes denoise_model) like any other camera RGB.
        if denoise_model:
            denoise_raw_inplace(raw, get_denoiser(denoise_model))
        dcp_profile, dcp_selection = resolve_dcp_profile(root, dcp_arg, disable_dcp, recipe, metadata)
        if dcp_profile is None:
            # Scene-referred fallback: deliver linear ProPhoto (D50) so the browser
            # edits in the same wide-gamut working space as the DCP path. LibRaw
            # still normalises the white level, so this path does not carry true
            # >1.0 highlight headroom (acceptable for a no-profile fallback).
            linear = raw.postprocess(
                use_camera_wb=True,
                no_auto_bright=True,
                output_color=rawpy.ColorSpace.ProPhoto,
                gamma=(1, 1),
                output_bps=16,
                half_size=half_size,
            ).astype(np.float32) / 65535.0
            if max_size:
                linear = downsample_linear(linear, max_size)
            color_profile = libraw_color_profile_info()
            with _CACHE_LOCK:
                _FALLBACK_CACHE[cache_key] = (linear, metadata, color_profile)
                while len(_FALLBACK_CACHE) > _FALLBACK_CACHE_MAX:
                    _FALLBACK_CACHE.popitem(last=False)
        else:
            camera_rgb = raw.postprocess(
                use_camera_wb=True,
                no_auto_bright=True,
                output_color=rawpy.ColorSpace.raw,
                gamma=(1, 1),
                output_bps=16,
                half_size=half_size,
            ).astype(np.float32) / 65535.0
            if camera_rgb.shape[-1] != 3:
                raise ValueError("DCP rendering currently supports only three-channel camera RGB data")
            if max_size:
                camera_rgb = downsample_linear(camera_rgb, max_size)
            # Cache camera RGB so DCP code changes skip RAW re-decode
            with _CACHE_LOCK:
                RAW_CAMERA_CACHE[cache_key] = (camera_rgb, metadata)
                while len(RAW_CAMERA_CACHE) > RAW_CAMERA_CACHE_MAX:
                    RAW_CAMERA_CACHE.popitem(last=False)
            linear, dcp_info = apply_dcp_profile(camera_rgb, dcp_profile)
            color_profile = dcp_info.to_json()
            color_profile["selection"] = dcp_selection

    return PreparedLinear(linear=linear, metadata=metadata, color_profile=color_profile)


def downsample_linear(linear: np.ndarray, max_size: int) -> np.ndarray:
    height, width = linear.shape[:2]
    long_edge = max(height, width)
    if long_edge <= max_size:
        return linear
    scale = max_size / float(long_edge)
    new_w = max(1, int(round(width * scale)))
    new_h = max(1, int(round(height * scale)))
    channels = linear.shape[2]
    out = np.empty((new_h, new_w, channels), dtype=np.float32)
    for channel in range(channels):
        plane = Image.fromarray(linear[..., channel], mode="F")
        plane = plane.resize((new_w, new_h), Image.Resampling.BILINEAR)
        out[..., channel] = np.asarray(plane, dtype=np.float32)
    return out


def finalize_image(
    prepared: PreparedLinear,
    recipe: dict[str, Any],
    lut: CubeLut | None,
    max_size: int | None = None,
) -> tuple[Image.Image, dict[str, Any] | None]:
    linear = prepared.linear
    color_profile = prepared.color_profile

    auto_tone = None
    if bool(recipe.get("autoTone", False)):
        linear, auto_tone = apply_auto_tone(linear)

    linear = apply_linear_controls(linear, recipe)
    linear = apply_contrast_linear(linear, float(recipe["contrast"]))
    linear = apply_saturation_linear(linear, float(recipe["saturation"]), float(recipe["vibrance"]))

    display = linear_to_srgb(linear)
    if int(color_profile.get("toneCurveSamples", 0)) == 0:
        display = apply_tone_curve(display, recipe)

    if lut:
        display = apply_cube_lut(display, lut)

    image = Image.fromarray(display, mode="RGB")
    image = apply_sharpen(image, float(recipe["sharpen"]))
    if max_size:
        image.thumbnail((max_size, max_size), Image.Resampling.BILINEAR)
    return image, auto_tone


def apply_contrast_linear(linear: np.ndarray, contrast: float) -> np.ndarray:
    if contrast == 0:
        return linear
    pivot = 0.18
    strength = contrast / 100.0
    radius = 0.5
    delta = linear - pivot
    falloff = np.maximum(0.0, 1.0 - np.square(delta / radius))
    return np.maximum(0.0, linear + strength * delta * falloff)


def apply_saturation_linear(linear: np.ndarray, saturation: float, vibrance: float) -> np.ndarray:
    if saturation == 0 and vibrance == 0:
        return linear
    luma = luminance(linear)[..., None]
    chroma = linear - luma
    sat_scale = 1.0 + saturation / 100.0
    if vibrance != 0:
        max_chroma = np.max(np.abs(chroma), axis=2, keepdims=True)
        muted_mask = np.clip(1.0 - max_chroma * 2.5, 0.0, 1.0)
        vib_scale = 1.0 + vibrance / 100.0 * muted_mask
        chroma = chroma * vib_scale
    return np.clip(luma + chroma * sat_scale, 0.0, None)


def read_raw_metadata(input_path: Path, raw: rawpy.RawPy) -> RawMetadata:
    exif = read_exiftool_metadata(input_path)
    sizes = raw.sizes
    return RawMetadata(
        make=exif.get("Make"),
        model=exif.get("Model"),
        lens_model=exif.get("LensModel"),
        creative_style=exif.get("CreativeStyle"),
        white_balance=exif.get("WhiteBalance"),
        camera_white_balance=[float(value) for value in raw.camera_whitebalance] if raw.camera_whitebalance else None,
        black_level=[int(value) for value in raw.black_level_per_channel] if raw.black_level_per_channel else None,
        white_level=int(raw.white_level) if raw.white_level else None,
        rgb_xyz_matrix=raw.rgb_xyz_matrix.astype(float).tolist() if raw.rgb_xyz_matrix is not None else None,
        full_width=int(sizes.iwidth) if sizes else None,
        full_height=int(sizes.iheight) if sizes else None,
    )


def read_exiftool_metadata(input_path: Path) -> dict[str, str | None]:
    command = detect_exiftool()
    if command is None:
        return {}

    try:
        output = run_capture(
            [
                command,
                "-j",
                "-s",
                "-Make",
                "-Model",
                "-LensModel",
                "-CreativeStyle",
                "-WhiteBalance",
                str(input_path),
            ],
            env=exiftool_env(),
        )
    except subprocess.CalledProcessError:
        return {}

    records = json.loads(output)
    record = records[0] if records else {}
    return {key: string_or_none(record.get(key)) for key in ["Make", "Model", "LensModel", "CreativeStyle", "WhiteBalance"]}


@functools.lru_cache(maxsize=1)
def detect_exiftool() -> str | None:
    root = find_repo_root()
    local = root / "vendor/exiftool/usr/bin/exiftool"
    if local.exists():
        return str(local)
    return shutil.which("exiftool")


def exiftool_env() -> dict[str, str]:
    root = find_repo_root()
    env = dict(os.environ)
    local_perl = root / "vendor/exiftool/usr/share/perl5"
    if local_perl.exists():
        env["PERL5LIB"] = ":".join(filter(None, [str(local_perl), env.get("PERL5LIB")]))
    return env


def metadata_to_json(metadata: RawMetadata | None) -> dict[str, Any] | None:
    if metadata is None:
        return None
    return {
        "make": metadata.make,
        "model": metadata.model,
        "lensModel": metadata.lens_model,
        "creativeStyle": metadata.creative_style,
        "whiteBalance": metadata.white_balance,
        "cameraWhiteBalance": metadata.camera_white_balance,
        "blackLevel": metadata.black_level,
        "whiteLevel": metadata.white_level,
        "rgbXyzMatrix": metadata.rgb_xyz_matrix,
    }


def resolve_dcp_profile(
    root: Path, dcp_arg: str | None, disable_dcp: bool, recipe: dict[str, Any], metadata: RawMetadata
) -> tuple[DcpProfile | None, dict[str, Any] | None]:
    if disable_dcp:
        return None, None

    if dcp_arg:
        path = resolve_path(root, dcp_arg)
        return load_dcp_profile(path), {"mode": "explicit", "path": str(path)}

    if recipe.get("profileId") != "standard":
        return None, None

    # Check for explicit DCP code override
    dcp_code: str | None = recipe.get("dcpCode")
    if dcp_code:
        match = find_local_camera_dcp(root, metadata, override_code=dcp_code)
        if match is None:
            return None, None
        path, selection = match
        return load_dcp_profile(path), selection

    match = find_local_camera_dcp(root, metadata)
    if match is None:
        return None, None

    path, selection = match
    return load_dcp_profile(path), selection


def find_local_camera_dcp(root: Path, metadata: RawMetadata, override_code: str | None = None) -> tuple[Path, dict[str, Any]] | None:
    profile_root = root / LOCAL_CAMERA_PROFILE_ROOT
    if not metadata.model or not profile_root.exists():
        return None

    profile_dir = find_camera_profile_dir(profile_root, metadata)
    if profile_dir is None:
        return None

    # If user specified a code, use it directly
    if override_code:
        code = normalize_profile_code(override_code)
        if code:
            profile_path = find_dcp_by_code(profile_dir, code)
            if profile_path is not None:
                return profile_path, {
                    "mode": "auto",
                    "root": str(profile_root),
                    "camera": profile_dir.name,
                    "matchedCode": code,
                    "reason": "user-selected",
                }
        return None

    creative_style = normalize_profile_code(metadata.creative_style)
    for code, reason in [(creative_style, "creativeStyle"), ("ST", "fallback-standard")]:
        if not code:
            continue
        profile_path = find_dcp_by_code(profile_dir, code)
        if profile_path is not None:
            return profile_path, {
                "mode": "auto",
                "root": str(profile_root),
                "camera": profile_dir.name,
                "creativeStyle": metadata.creative_style,
                "matchedCode": code,
                "reason": reason,
            }

    profiles = sorted(profile_dir.glob("*.dcp"))
    if not profiles:
        return None

    return profiles[0], {
        "mode": "auto",
        "root": str(profile_root),
        "camera": profile_dir.name,
        "creativeStyle": metadata.creative_style,
        "matchedCode": None,
        "reason": "first-available",
    }


def find_camera_profile_dir(profile_root: Path, metadata: RawMetadata) -> Path | None:
    exact_name = f"{format_camera_make(metadata.make)} {metadata.model}".strip()
    exact_dir = profile_root / exact_name
    if exact_dir.exists():
        return exact_dir

    model_key = normalize_profile_name(metadata.model)
    make_key = normalize_profile_name(metadata.make)
    for candidate in sorted(profile_root.iterdir()):
        if not candidate.is_dir():
            continue
        name_key = normalize_profile_name(candidate.name)
        if model_key and model_key in name_key and (not make_key or make_key in name_key):
            return candidate

    return None


def find_dcp_by_code(profile_dir: Path, code: str) -> Path | None:
    suffix = f" camera {code.lower()}.dcp"
    for candidate in sorted(profile_dir.glob("*.dcp")):
        if candidate.name.lower().endswith(suffix):
            return candidate
    return None


def format_camera_make(value: str | None) -> str:
    if not value:
        return ""
    if value.upper() == "SONY":
        return "Sony"
    return value.strip()


def normalize_profile_code(value: str | None) -> str | None:
    if value is None:
        return None
    code = value.strip().upper()
    return code or None


def normalize_profile_name(value: str | None) -> str:
    if value is None:
        return ""
    return "".join(char.lower() for char in value if char.isalnum())


def is_dcp_color_profile(color_profile: dict[str, Any] | None) -> bool:
    return bool(color_profile and color_profile.get("kind") == "dcp")


def libraw_color_profile_info() -> dict[str, Any]:
    return {
        "kind": "libraw-matrix",
        "matrix": "rawpy/LibRaw camera to linear ProPhoto",
        "toneCurveSamples": 0,
        "workingSpace": "linear-prophoto-d50",
        "profileToneCurve": None,
        "note": "No DCP profile supplied; this is a matrix fallback, not a calibrated camera profile.",
    }


def apply_auto_tone(linear: np.ndarray) -> tuple[np.ndarray, dict[str, Any]]:
    image = np.nan_to_num(linear, nan=0.0, posinf=1.0, neginf=0.0)
    luma = luminance(image)
    sample = luma[np.isfinite(luma) & (luma > 0)]

    if sample.size < 16:
        return np.clip(image, 0, 1), {"enabled": True, "reason": "insufficient-sample"}

    black_percentile = 0.2
    white_percentile = 99.9
    black_point = float(np.percentile(sample, black_percentile))
    white_point = float(np.percentile(sample, white_percentile))

    if white_point <= black_point + 1e-5:
        return np.clip(image, 0, 1), {"enabled": True, "reason": "flat-image"}

    image = np.clip((image - black_point) / (white_point - black_point), 0, 1)
    normalized_luma = luminance(image)
    mid_sample = normalized_luma[np.isfinite(normalized_luma) & (normalized_luma > 0.001) & (normalized_luma < 0.98)]
    source_median = float(np.percentile(mid_sample, 50)) if mid_sample.size else 0.22
    target_median = 0.16
    midtone_power = 1.0

    if 0 < source_median < 1:
        midtone_power = clamp(math.log(target_median) / math.log(clamp(source_median, 0.001, 0.999)), 0.65, 1.45)
        image = np.power(image, midtone_power)

    return np.clip(image, 0, 1), {
        "enabled": True,
        "space": "linear-srgb",
        "blackPercentile": black_percentile,
        "whitePercentile": white_percentile,
        "blackPoint": round(black_point, 6),
        "whitePoint": round(white_point, 6),
        "sourceMedian": round(source_median, 6),
        "targetMedian": target_median,
        "midtonePower": round(float(midtone_power), 6),
    }


def apply_linear_controls(linear: np.ndarray, recipe: dict[str, Any]) -> np.ndarray:
    image = linear * (2 ** float(recipe["exposure"]))
    image = apply_highlight_shadow_controls(image, recipe)
    return np.clip(image, 0, 1)


def apply_highlight_shadow_controls(image: np.ndarray, recipe: dict[str, Any]) -> np.ndarray:
    shadows = float(recipe["shadows"]) / 100
    highlights = float(recipe["highlights"]) / 100
    whites = float(recipe["whites"]) / 100
    blacks = float(recipe["blacks"]) / 100
    if shadows == 0 and highlights == 0 and whites == 0 and blacks == 0:
        return image

    luma = luminance(image)
    shadow_mask = np.clip((0.55 - luma) / 0.55, 0, 1)[..., None]
    highlight_mask = np.clip((luma - 0.45) / 0.55, 0, 1)[..., None]
    image = image * (1 + shadows * 0.35 * shadow_mask)
    image = image * (1 + highlights * 0.30 * highlight_mask)
    image = image + whites * 0.06 * highlight_mask
    image = image + blacks * 0.04 * (1 - shadow_mask)
    return image


def linear_to_srgb(linear: np.ndarray) -> np.ndarray:
    indexes = np.clip(linear * 65535, 0, 65535).astype(np.uint16)
    return SRGB_U8_LUT[indexes]


def apply_tone_curve(display: np.ndarray, recipe: dict[str, Any]) -> np.ndarray:
    tone_curve = recipe.get("toneCurve", [])
    if len(tone_curve) < 3:
        return display

    xs = np.array([clamp(float(point["x"]), 0, 255) for point in tone_curve], dtype=np.float32)
    ys = np.array([clamp(float(point["y"]), 0, 255) for point in tone_curve], dtype=np.float32)
    curve = np.interp(np.arange(256, dtype=np.float32), xs, ys).astype(np.uint8)
    return curve[display]


def apply_display_recipe(image: Image.Image, recipe: dict[str, Any], skip_tone_curve: bool = False) -> Image.Image:
    contrast = 1 + float(recipe["contrast"]) / 100 + float(recipe["clarity"]) / 220 + float(recipe["dehaze"]) / 260
    saturation = 1 + (float(recipe["saturation"]) + float(recipe["vibrance"]) * 0.6) / 100
    image = ImageEnhance.Contrast(image).enhance(clamp(contrast, 0.1, 4))
    image = ImageEnhance.Color(image).enhance(clamp(saturation, 0, 3))
    if not skip_tone_curve:
        image = Image.fromarray(apply_tone_curve(np.asarray(image, dtype=np.uint8), recipe), mode="RGB")
    return image


def apply_sharpen(image: Image.Image, amount: float) -> Image.Image:
    if amount <= 0:
        return image
    return image.filter(ImageFilter.UnsharpMask(radius=1.0, percent=int(clamp(amount * 4, 0, 180)), threshold=2))


@dataclass(frozen=True)
class CubeLut:
    size: int
    values: np.ndarray


def load_cube_lut(path: Path) -> CubeLut:
    size: int | None = None
    rows: list[list[float]] = []
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        parts = stripped.split()
        key = parts[0].upper()
        if key == "LUT_3D_SIZE":
            size = int(parts[1])
            continue
        if key in {"TITLE", "DOMAIN_MIN", "DOMAIN_MAX", "LUT_1D_SIZE"}:
            continue
        if len(parts) >= 3 and all(is_float(part) for part in parts[:3]):
            rows.append([float(parts[0]), float(parts[1]), float(parts[2])])

    if size is None:
        raise ValueError(f"{path} is missing LUT_3D_SIZE")
    if len(rows) != size**3:
        raise ValueError(f"{path} has {len(rows)} LUT rows, expected {size ** 3}")

    return CubeLut(size=size, values=np.array(rows, dtype=np.float32).reshape((size, size, size, 3)))


def apply_cube_lut(image: np.ndarray, lut: CubeLut) -> np.ndarray:
    source = image.astype(np.float32) / 255
    max_index = lut.size - 1
    scaled = source * max_index
    lower = np.floor(scaled).astype(np.int32)
    upper = np.clip(lower + 1, 0, max_index)
    amount = scaled - lower

    c000 = lut.values[lower[..., 0], lower[..., 1], lower[..., 2]]
    c100 = lut.values[upper[..., 0], lower[..., 1], lower[..., 2]]
    c010 = lut.values[lower[..., 0], upper[..., 1], lower[..., 2]]
    c110 = lut.values[upper[..., 0], upper[..., 1], lower[..., 2]]
    c001 = lut.values[lower[..., 0], lower[..., 1], upper[..., 2]]
    c101 = lut.values[upper[..., 0], lower[..., 1], upper[..., 2]]
    c011 = lut.values[lower[..., 0], upper[..., 1], upper[..., 2]]
    c111 = lut.values[upper[..., 0], upper[..., 1], upper[..., 2]]

    tx = amount[..., 0:1]
    ty = amount[..., 1:2]
    tz = amount[..., 2:3]
    c00 = c000 * (1 - tx) + c100 * tx
    c10 = c010 * (1 - tx) + c110 * tx
    c01 = c001 * (1 - tx) + c101 * tx
    c11 = c011 * (1 - tx) + c111 * tx
    c0 = c00 * (1 - ty) + c10 * ty
    c1 = c01 * (1 - ty) + c11 * ty
    return np.clip((c0 * (1 - tz) + c1 * tz) * 255, 0, 255).astype(np.uint8)


def open_rgb(path: Path | Any) -> Image.Image:
    with Image.open(path) as image:
        return ImageOps.exif_transpose(image).convert("RGB")


def save_jpeg(image: Image.Image, output_path: Path, quality: int) -> None:
    ensure_parent(output_path)
    image.save(output_path, "JPEG", quality=quality, optimize=True)


def luminance(image: np.ndarray) -> np.ndarray:
    return image[..., 0] * 0.2126 + image[..., 1] * 0.7152 + image[..., 2] * 0.0722


def run_capture(command: list[str], env: dict[str, str] | None = None) -> str:
    result = subprocess.run(command, check=True, env=env, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return result.stdout


def string_or_none(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def is_float(value: str) -> bool:
    try:
        float(value)
    except ValueError:
        return False
    return True


def clamp(value: float, minimum: float, maximum: float) -> float:
    return min(maximum, max(minimum, value))


def ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


if __name__ == "__main__":
    main()
