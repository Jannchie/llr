from __future__ import annotations

import argparse
import functools
import hashlib
import json
import os
import shutil
import struct
import subprocess
import sys
import threading
import traceback
from collections import OrderedDict
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import rawpy
from PIL import Image, ImageOps, UnidentifiedImageError

from .creative_style import normalize_style
from .dcp import DcpProfile, apply_dcp_profile, load_dcp_profile
from .denoise import DEFAULT_MODEL, denoise_raw_inplace, get_denoiser
from .fit_profile import camera_match_path, postprocess_camera_native
from .imported import decode_image_linear
from .sony import apply_sony_profile, calibration_for
from .sony import can_render as sony_can_render
from .sony.sr2 import LookCalibration

RAW_EXTENSIONS = {".arw", ".srf", ".sr2", ".dng", ".cr2", ".cr3", ".nef", ".raf", ".rw2", ".orf"}
LOCAL_CAMERA_PROFILE_ROOT = Path("vendor/adobe-camera-profiles/Camera")


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
    # When the RAW carries a camera crop, these are the cropped dims.
    full_width: int | None
    full_height: int | None
    # Camera-intended crop (DNG DefaultCropOrigin/Size) mapped into the
    # postprocess output frame: ((x, y, w, h), (frame_w, frame_h)), or None.
    camera_crop: tuple[tuple[int, int, int, int], tuple[int, int]] | None = None
    # Per-shot lens correction splines from the RAW's maker notes, mapped to
    # vendor-neutral factor tables (see sony_lens_corrections), or None.
    lens_corr: dict[str, Any] | None = None
    # In-camera tweaks to the Creative Look. Highlights, Shadows and Contrast
    # (-9..+9) ride on the look's factory tone curve (sony/tone.py); Fade (0..9)
    # is a separate stage that pulls luma toward a pivot (sony/chroma.py).
    # Reproducing Sony's rendering needs all four.
    look_highlights: int = 0
    look_shadows: int = 0
    look_contrast: int = 0
    look_fade: int = 0
    # Whether the shot asked for DRO. Sony runs a whole extra stage for it
    # (ZcTaskVatr) that this pipeline does not reproduce, so the render is
    # honest about it only on the shots where it actually applies.
    dro_active: bool = False


@dataclass
class PreparedLinear:
    linear: np.ndarray
    metadata: RawMetadata
    color_profile: dict[str, Any]


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


# The worker only decodes; the browser owns every pixel operation, so a profile
# carries no tone/sharpen values here. `profileId` picks the colour pipeline
# (see resolve_color_renderer): "standard" looks up a DCP and daemon_linear
# layers `dcpCode` on top, "sony" reproduces Imaging Edge from calibration in the
# RAW itself, "neutral" means the libraw-matrix fallback.
PROFILES: dict[str, dict[str, Any]] = {
    "neutral": {"profileId": "neutral"},
    "standard": {"profileId": "standard"},
    "sony": {"profileId": "sony"},
}

# The profileIds that want a camera profile at all; anything else ("neutral")
# is asking for the raw matrix fallback. "sony" is in here because it falls back
# to a DCP whenever the shot has no Sony rendering available — dropping all the
# way to the matrix fallback would be a visible downgrade, not a fallback.
PROFILED_IDS = frozenset({"standard", "sony"})


class UnsupportedSourceError(ValueError):
    """The input is not a format LibRaw can decode for editing."""


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

    subparsers.add_parser("daemon")

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

    image = extract_preview_image(input_path) if is_raw(input_path) else open_rgb(input_path)

    image.thumbnail((2400, 2400), Image.Resampling.LANCZOS)
    save_jpeg(image, output_path, quality=86)
    print(f"Wrote {output_path}")


# Daemon concurrency: requests run on a small thread pool so a multi-second
# denoise render-linear cannot block cheap commands (ping, export) for other
# sources. Requests for the same source (the `input` path, which keys every
# global cache) are serialised via a per-source lock — the caches assume no
# same-source concurrency. _CACHE_LOCK guards the cache dicts themselves
# (lookup/insert/eviction) against cross-source races.
DAEMON_MAX_WORKERS = 3

_STDOUT_LOCK = threading.Lock()
_CACHE_LOCK = threading.Lock()


def _remember(cache: OrderedDict[Any, Any], key: Any, value: Any, cap: int) -> None:
    """Insert into an LRU cache under the shared lock, evicting oldest past `cap`."""
    with _CACHE_LOCK:
        cache[key] = value
        while len(cache) > cap:
            cache.popitem(last=False)
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
    except Exception as error:
        traceback.print_exc(file=sys.stderr)
        emit_response({"id": request_id, "ok": False, "error": str(error)})


def emit_response(payload: dict[str, Any]) -> None:
    line = json.dumps(payload) + "\n"
    with _STDOUT_LOCK:
        sys.stdout.write(line)
        sys.stdout.flush()


def handle_daemon_request(request: dict[str, Any], root: Path) -> dict[str, Any]:
    command = request.get("command")
    if command == "render-linear":
        return daemon_linear(request, root)
    if command == "extract-preview":
        return daemon_extract_preview(request, root)
    if command == "export":
        return daemon_export(request, root)
    if command == "ping":
        return {"pong": True, "cacheSize": len(_LINEAR_CACHE)}
    raise ValueError(f"unknown command: {command}")


def _linear_cache_key(
    input_path: Path,
    profile_id: str,
    half_size: bool,
    max_size: int | None,
    dcp_code: str | None,
    denoise_model: str | None,
    denoise_amount: float,
    camera_match: bool,
) -> tuple[Any, ...]:
    try:
        st = input_path.stat()
        base = (str(input_path), st.st_size, int(st.st_mtime_ns))
    except OSError:
        base = (str(input_path),)
    return (*base, profile_id, bool(half_size), int(max_size or 0), dcp_code or "", denoise_model or "", round(float(denoise_amount), 3), bool(camera_match))


_LINEAR_CACHE: OrderedDict[tuple[Any, ...], tuple[np.ndarray, dict[str, Any]]] = OrderedDict()
_LINEAR_CACHE_MAX = 8


def _write_linear_f16(linear_arr: np.ndarray, output_path: Path) -> int:
    """Write scene-linear pixels as float16, halving the transfer size.

    The browser uploads the payload straight into an RGB16F texture, so f16 is
    the precision the render actually uses; its ~11-bit relative mantissa sits
    below sensor noise for 12-14-bit RAW data. The in-memory caches stay
    float32 so repeated DCP/denoise blends never accumulate quantisation.
    """
    out = linear_arr.astype(np.float16)
    with open(output_path, "wb") as f:
        out.tofile(f)
    return out.nbytes


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
    # Fitted camera-match table is layered on top of the DCP. Defaults on — it is
    # the whole point of the profile — but the frontend can switch it off to
    # compare against Adobe's uncorrected rendering. Baked into linear.bin (it
    # rides the DCP), so toggling it re-decodes, hence it keys the linear cache.
    camera_match = bool(request.get("cameraMatch", True))

    # RAW-domain denoise request. amount<=0 (or disabled) is treated as off so
    # the heavy inference and the second decode are skipped entirely.
    denoise_req = request.get("denoise") or {}
    dn_amount = max(0.0, min(1.0, float(denoise_req.get("amount", 1.0))))
    dn_model: str | None = None
    # Denoise operates on the Bayer mosaic, which a rendered image does not have.
    # Forcing it off here (rather than no-op'ing deeper) keeps the two-decode
    # blend below from decoding an identical image twice.
    if bool(denoise_req.get("enabled", False)) and dn_amount > 0.0 and is_raw(input_path):
        dn_model = str(denoise_req.get("model") or DEFAULT_MODEL)

    # Check processed sRGB cache first
    cache_key = _linear_cache_key(input_path, profile_id, half_size, max_size, dcp_code, dn_model, dn_amount, camera_match)
    with _CACHE_LOCK:
        cached_linear = _LINEAR_CACHE.get(cache_key)
        if cached_linear is not None:
            _LINEAR_CACHE.move_to_end(cache_key)
    if cached_linear is not None:
        linear_arr, color_profile = cached_linear
        bytes_written = _write_linear_f16(linear_arr, output_path)
        return {
            "width": linear_arr.shape[1],
            "height": linear_arr.shape[0],
            "fullWidth": color_profile.get("fullWidth"),
            "fullHeight": color_profile.get("fullHeight"),
            "output": str(output_path),
            "colorProfile": color_profile,
            "dtype": "float16",
            "bytesWritten": bytes_written,
        }

    recipe = merge_recipe(PROFILES[profile_id], {})
    recipe["cameraMatch"] = camera_match
    if dcp_code:
        recipe["dcpCode"] = dcp_code

    # The noisy and denoised variants share one lazily opened LibRaw handle:
    # opening is skipped entirely when both variants are cache hits, and a
    # first-time denoise request no longer reads + unpacks the RAW twice.
    shared_raw: rawpy.RawPy | None = None

    def open_raw() -> rawpy.RawPy:
        nonlocal shared_raw
        if shared_raw is None:
            shared_raw = rawpy.imread(str(input_path))
        return shared_raw

    # Same reasoning as the _LINEAR_CACHE gate below: a full-resolution export
    # decode must not be pinned in any cache, so the intent is passed down to
    # the decode caches rather than re-derived there.
    store_cache = half_size or bool(max_size)

    try:
        if dn_model is not None and dn_amount >= 1.0:
            # Full-strength denoise: the noisy decode would be blended away
            # entirely, so skip it and decode only the denoised variant.
            prepared = prepare_linear(
                input_path, recipe, root,
                dcp_arg=None, disable_dcp=False,
                half_size=half_size, max_size=max_size,
                denoise_model=dn_model,
                raw_provider=open_raw,
                store_cache=store_cache,
            )
        else:
            prepared = prepare_linear(
                input_path, recipe, root,
                dcp_arg=None, disable_dcp=False,
                half_size=half_size, max_size=max_size,
                raw_provider=open_raw,
                store_cache=store_cache,
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
                    raw_provider=open_raw,
                    store_cache=store_cache,
                )
                blended = prepared.linear * (1.0 - dn_amount) + prepared_dn.linear * dn_amount
                prepared = PreparedLinear(
                    linear=blended.astype(np.float32),
                    metadata=prepared.metadata,
                    color_profile=prepared.color_profile,
                )
    finally:
        if shared_raw is not None:
            shared_raw.close()

    # Stash full-resolution dims on the color profile so both this response and
    # the _LINEAR_CACHE-hit path above can report zoom relative to the original.
    prepared.color_profile["fullWidth"] = prepared.metadata.full_width
    prepared.color_profile["fullHeight"] = prepared.metadata.full_height
    prepared.color_profile["lensCorr"] = prepared.metadata.lens_corr
    # Whether a fitted table exists for this body+style, independent of whether it
    # was applied this render. The frontend needs this to keep showing the toggle
    # after the user switches the match off (at which point cameraMatch goes null).
    prepared.color_profile["cameraMatchAvailable"] = has_camera_match(root, prepared.color_profile.get("selection"))

    # Cache processed sRGB so switching back to this DCP code is instant. The
    # decode/DCP/downsample paths already yield C-contiguous float32, so this is a
    # no-op there (avoids a full ~tens-of-MB copy) and only copies when it must.
    linear_arr = np.ascontiguousarray(prepared.linear, dtype=np.float32)
    # Skip caching full-resolution exports (half_size off and no max_size cap): a
    # single entry can be hundreds of MB and exports are one-off, so caching them
    # would pin gigabytes with no reuse. Previews (half/max-size capped) still cache.
    if store_cache:
        _remember(_LINEAR_CACHE, cache_key, (linear_arr, prepared.color_profile), _LINEAR_CACHE_MAX)

    bytes_written = _write_linear_f16(linear_arr, output_path)

    return {
        "width": prepared.linear.shape[1],
        "height": prepared.linear.shape[0],
        "fullWidth": prepared.metadata.full_width,
        "fullHeight": prepared.metadata.full_height,
        "output": str(output_path),
        "colorProfile": prepared.color_profile,
        "dtype": "float16",
        "bytesWritten": bytes_written,
    }


def daemon_extract_preview(request: dict[str, Any], root: Path) -> dict[str, Any]:
    input_path = resolve_path(root, request["input"])
    output_path = resolve_path(root, request["output"])
    extract_preview(input_path, output_path)
    return {"output": str(output_path)}


# ── Export: embed edit settings into the rendered JPEG as XMP ──
#
# LLR writes its own XMP schema (the llr: namespace) instead of Adobe's
# camera-raw-settings fields: structured llr:* tags for at-a-glance reading
# plus the lossless llr:Settings JSON blob as the authoritative record.

LLR_XMP_NS = "http://ns.llr.app/xmp/1.0/"


def daemon_export(request: dict[str, Any], root: Path) -> dict[str, Any]:
    """Embed the edit recipe into an already-rendered JPEG as XMP metadata.

    The frontend renders the full-resolution image via WebGL and posts the JPEG;
    here we (best-effort) copy the original camera EXIF for provenance and inject
    an XMP packet carrying structured llr:* fields plus a lossless LLR JSON blob.
    """
    input_path = resolve_path(root, request["input"])
    target_path = resolve_path(root, request["target"])
    settings = request.get("settings") or {}
    strip_private = bool(request.get("stripPrivate", False))

    exif_copied = copy_exif_provenance(input_path, target_path, strip_private=strip_private)

    payloads, xmp_bytes = build_xmp_segments(settings)
    data = embed_xmp_app1(target_path.read_bytes(), payloads)
    target_path.write_bytes(data)

    return {"output": str(target_path), "exifCopied": exif_copied, "xmpBytes": xmp_bytes}


# Exclusions applied only when the user asks for a privacy strip: GPS
# location, serial numbers, and owner name. Maker notes go as a whole block
# (no :all) because serials hide inside the copied binary blob where
# member-tag exclusions cannot reach.
PRIVATE_TAG_EXCLUSIONS = ["--gps:all", "--makernotes", "--*serialnumber*", "--ownername"]


def copy_exif_provenance(original: Path, target: Path, strip_private: bool = False) -> bool:
    """Copy all camera metadata from the original into the export.

    Copies every writable tag group (EXIF, GPS, maker notes, IPTC, ...) so no
    shooting metadata is lost, then overrides the few tags that must describe
    the export itself: orientation/rotation is baked into the pixels, the EXIF
    pixel dimensions are the export's, the colorspace is the encode's (sRGB),
    and Software identifies the renderer. XMP is excluded because daemon_export
    injects LLR's own packet, and the source ICC profile would mislabel the
    rendered (sRGB) colors. IFD1 is excluded because its embedded thumbnail
    shows the original composition — file managers and gallery grids prefer it
    over the real pixels, so a copied one shows the photo uncropped and unedited.

    With ``strip_private`` the privacy-sensitive tags (GPS location, serial
    numbers, owner name, maker notes) are excluded, so a shared export does
    not reveal where or with whose gear it was shot. Off by default: the
    export carries full provenance unless the user opts out.
    """
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
                "-all:all",
                "--xmp:all",
                "--icc_profile:all",
                "--ifd1:all",
                *(PRIVATE_TAG_EXCLUSIONS if strip_private else []),
                "-tagsFromFile",
                "@",
                "-ExifImageWidth<ImageWidth",
                "-ExifImageHeight<ImageHeight",
                "-Orientation#=1",
                "-ColorSpace#=1",
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


def build_llr_attrs(settings: dict[str, Any]) -> OrderedDict[str, str]:
    """Structured llr:* fields mirroring LLR's own parameter model.

    Names and units follow the internal recipe (not Adobe's), so the schema can
    carry everything the editor has — including midtone grading, flips, and 90°
    orientation, which crs could not express."""
    recipe = settings.get("recipe", {}) or {}
    attrs: OrderedDict[str, str] = OrderedDict()
    attrs["llr:Version"] = "1"
    attrs["llr:Exposure"] = f"{_num(recipe, 'exposure'):+.2f}"
    for key, name in (
        ("contrast", "Contrast"), ("highlights", "Highlights"), ("shadows", "Shadows"),
        ("whites", "Whites"), ("blacks", "Blacks"), ("clarity", "Clarity"),
        ("dehaze", "Dehaze"), ("vibrance", "Vibrance"), ("saturation", "Saturation"),
    ):
        attrs[f"llr:{name}"] = f"{round(_num(recipe, key))}"
    attrs["llr:Temperature"] = f"{round(_num(recipe, 'temperature', 6500))}"
    attrs["llr:Tint"] = f"{round(_num(recipe, 'tint'))}"
    attrs["llr:LensDistortion"] = f"{round(_num(recipe, 'lensDistortion', 100))}"
    attrs["llr:LensVignetting"] = f"{round(_num(recipe, 'lensVignetting', 0))}"

    # Parametric (region) tone curve.
    parametric = _curve_settings(settings).get("parametric", {}) or {}
    attrs["llr:ParametricShadows"] = f"{round(_num(parametric, 'shadows'))}"
    attrs["llr:ParametricDarks"] = f"{round(_num(parametric, 'darks'))}"
    attrs["llr:ParametricLights"] = f"{round(_num(parametric, 'lights'))}"
    attrs["llr:ParametricHighlights"] = f"{round(_num(parametric, 'highlights'))}"
    attrs["llr:ParametricShadowSplit"] = f"{round(_num(parametric, 'shadowSplit', 25))}"
    attrs["llr:ParametricMidtoneSplit"] = f"{round(_num(parametric, 'midtoneSplit', 50))}"
    attrs["llr:ParametricHighlightSplit"] = f"{round(_num(parametric, 'highlightSplit', 75))}"

    # HSL: 8 channels (red→magenta), comma-joined in channel order.
    def joined(values: Any) -> str:
        return ",".join(f"{round(float(v))}" for v in (values or []))

    for key, name in (("hslHue", "HslHue"), ("hslSat", "HslSaturation"), ("hslLum", "HslLuminance")):
        if settings.get(key):
            attrs[f"llr:{name}"] = joined(settings[key])

    # Color grading, including the midtone wheel and blend that crs lacked.
    grading = settings.get("grading", {}) or {}

    def hue360(value: float) -> int:
        return round(((value % 360) + 360) % 360)

    attrs["llr:GradingShadowHue"] = f"{hue360(_num(grading, 'shH'))}"
    attrs["llr:GradingShadowSaturation"] = f"{round(_num(grading, 'shS'))}"
    attrs["llr:GradingMidtoneHue"] = f"{hue360(_num(grading, 'mdH'))}"
    attrs["llr:GradingMidtoneSaturation"] = f"{round(_num(grading, 'mdS'))}"
    attrs["llr:GradingHighlightHue"] = f"{hue360(_num(grading, 'hlH'))}"
    attrs["llr:GradingHighlightSaturation"] = f"{round(_num(grading, 'hlS'))}"
    attrs["llr:GradingBlend"] = f"{round(_num(grading, 'blend', 50))}"
    attrs["llr:GradingBalance"] = f"{round(_num(grading, 'balance'))}"

    dcp = settings.get("dcp")
    if dcp:
        attrs["llr:Dcp"] = str(dcp)
    denoise = settings.get("denoise") or {}
    if denoise.get("enabled"):
        attrs["llr:DenoiseModel"] = str(denoise.get("model", ""))
        attrs["llr:DenoiseAmount"] = f"{round(_num(denoise, 'amount', 100))}"

    _add_crop_attrs(attrs, settings.get("crop") or {})
    return attrs


def _add_crop_attrs(attrs: OrderedDict[str, str], crop: dict[str, Any]) -> None:
    """Crop/recompose fields, written only when the crop has an effect.

    The exported JPEG is already cropped/straightened in pixels; these record
    the recompose in LLR's own model (normalized center/size in image space,
    straighten angle, flips, 90° orientation)."""
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

    attrs["llr:HasCrop"] = "True"
    attrs["llr:CropCenterX"] = f"{cx:.6f}"
    attrs["llr:CropCenterY"] = f"{cy:.6f}"
    attrs["llr:CropWidth"] = f"{w:.6f}"
    attrs["llr:CropHeight"] = f"{h:.6f}"
    attrs["llr:CropAngle"] = f"{angle:.4f}"
    if flip_h:
        attrs["llr:CropFlipH"] = "True"
    if flip_v:
        attrs["llr:CropFlipV"] = "True"
    if orientation:
        attrs["llr:CropOrientation"] = f"{orientation}"


def _curve_settings(settings: dict[str, Any]) -> dict[str, Any]:
    """Return the tone curve as a dict, tolerating the legacy flat-list format."""
    curve = settings.get("curve")
    if isinstance(curve, list):  # legacy: a bare RGB point list
        return {"rgb": curve}
    if isinstance(curve, dict):
        return curve
    return {}


def _tone_curve_points(points: Any) -> list[str]:
    """Format a list of {x,y} control points as "x, y" in normalized [0,1]."""
    out: list[str] = []
    for point in points or []:
        try:
            x = clamp(float(point["x"]), 0.0, 1.0)
            y = clamp(float(point["y"]), 0.0, 1.0)
        except (KeyError, TypeError, ValueError):
            continue
        out.append(f"{x:.4f}, {y:.4f}")
    return out


def _is_identity_curve(points: list[str]) -> bool:
    return points in ([], ["0.0000, 0.0000", "1.0000, 1.0000"])


def _xml_escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _xml_attr(text: str) -> str:
    return _xml_escape(text).replace('"', "&quot;")


def _tone_curve_block(tag: str, points: list[str]) -> str:
    if not points:
        return ""
    items = "\n     ".join(f"<rdf:li>{_xml_escape(point)}</rdf:li>" for point in points)
    return (
        f"\n   <llr:{tag}>\n    <rdf:Seq>\n     "
        + items
        + f"\n    </rdf:Seq>\n   </llr:{tag}>"
    )


def build_xmp_packet(
    settings: dict[str, Any],
    extended_guid: str | None = None,
    include_curves: bool = True,
) -> str:
    """The standard XMP packet.

    With `extended_guid` set, the llr:Settings JSON is omitted and replaced by
    an xmpNote:HasExtendedXMP pointer (XMP Spec part 3) — used when the JSON
    blob would overflow the 64 KB APP1 segment limit. `include_curves=False`
    additionally drops the tone-curve Seq blocks (they are still recoverable
    from llr:Settings) when even those overflow the main packet.
    """
    llr_attrs = build_llr_attrs(settings)
    curve = _curve_settings(settings)

    attr_lines = "\n   ".join(f'{key}="{_xml_attr(value)}"' for key, value in llr_attrs.items())

    # Point tone curves, one Seq per channel, only when non-identity.
    tone_block = ""
    if include_curves:
        for tag, key in (("ToneCurveRgb", "rgb"), ("ToneCurveRed", "red"), ("ToneCurveGreen", "green"), ("ToneCurveBlue", "blue")):
            pts = _tone_curve_points(curve.get(key))
            if not _is_identity_curve(pts):
                tone_block += _tone_curve_block(tag, pts)

    if extended_guid is None:
        llr_json = json.dumps(settings, separators=(",", ":"), ensure_ascii=False)
        ext_ns = ""
        ext_attr = ""
        settings_block = f"\n   <llr:Settings>{_xml_escape(llr_json)}</llr:Settings>"
    else:
        ext_ns = '\n   xmlns:xmpNote="http://ns.adobe.com/xmp/note/"'
        ext_attr = f'\n   xmpNote:HasExtendedXMP="{extended_guid}"'
        settings_block = ""

    return (
        '<?xpacket begin="﻿" id="W5M0MpCehiHzreSzNTczkc9d"?>\n'
        '<x:xmpmeta xmlns:x="adobe:ns:meta/" x:xmptk="LLR">\n'
        ' <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">\n'
        '  <rdf:Description rdf:about=""\n'
        f'   xmlns:llr="{LLR_XMP_NS}"{ext_ns}\n'
        f"   {attr_lines}{ext_attr}>"
        f"{tone_block}"
        f"{settings_block}\n"
        "  </rdf:Description>\n"
        " </rdf:RDF>\n"
        "</x:xmpmeta>\n"
        '<?xpacket end="w"?>'
    )


def build_extended_xmp(settings: dict[str, Any]) -> str:
    """The ExtendedXMP serialization carrying only llr:Settings (no xpacket PI)."""
    llr_json = json.dumps(settings, separators=(",", ":"), ensure_ascii=False)
    return (
        '<x:xmpmeta xmlns:x="adobe:ns:meta/" x:xmptk="LLR">\n'
        ' <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">\n'
        '  <rdf:Description rdf:about=""\n'
        f'   xmlns:llr="{LLR_XMP_NS}">\n'
        f"   <llr:Settings>{_xml_escape(llr_json)}</llr:Settings>\n"
        "  </rdf:Description>\n"
        " </rdf:RDF>\n"
        "</x:xmpmeta>"
    )


XMP_STD_HEADER = b"http://ns.adobe.com/xap/1.0/\x00"
XMP_EXT_HEADER = b"http://ns.adobe.com/xmp/extension/\x00"
MAX_APP1_PAYLOAD = 0xFFFF - 2  # the 2-byte length field counts itself
# Each extension chunk header: namespace + 32-char GUID + u32 total + u32 offset.
EXT_CHUNK_SIZE = MAX_APP1_PAYLOAD - len(XMP_EXT_HEADER) - 32 - 8


def build_xmp_segments(settings: dict[str, Any]) -> tuple[list[bytes], int]:
    """APP1 payloads for the settings XMP, plus the total XMP byte count.

    A normal edit fits one standard XMP APP1. A heavy edit (big curves + HSL +
    grading JSON) can overflow the 64 KB segment limit; then the llr:Settings
    blob moves to Extended XMP chunks so export never fails on packet size.
    """
    packet = build_xmp_packet(settings).encode("utf-8")
    if len(XMP_STD_HEADER) + len(packet) <= MAX_APP1_PAYLOAD:
        return [XMP_STD_HEADER + packet], len(packet)

    extended = build_extended_xmp(settings).encode("utf-8")
    guid = hashlib.md5(extended).hexdigest().upper()
    main = build_xmp_packet(settings, extended_guid=guid).encode("utf-8")
    if len(XMP_STD_HEADER) + len(main) > MAX_APP1_PAYLOAD:
        # Huge tone curves inflate the main packet too; drop the Seq blocks
        # (the full curves still live in llr:Settings in the extended packet).
        main = build_xmp_packet(settings, extended_guid=guid, include_curves=False).encode("utf-8")
    if len(XMP_STD_HEADER) + len(main) > MAX_APP1_PAYLOAD:
        raise ValueError("XMP packet too large even without the settings blob")
    payloads = [XMP_STD_HEADER + main]
    for offset in range(0, len(extended), EXT_CHUNK_SIZE):
        chunk = extended[offset : offset + EXT_CHUNK_SIZE]
        payloads.append(
            XMP_EXT_HEADER
            + guid.encode("ascii")
            + struct.pack(">II", len(extended), offset)
            + chunk
        )
    return payloads, len(main) + len(extended)


def embed_xmp_app1(jpeg: bytes, payloads: list[bytes]) -> bytes:
    """Insert XMP APP1 segment(s) into a JPEG without re-encoding pixels."""
    if jpeg[:2] != b"\xff\xd8":
        raise ValueError("export target is not a JPEG file")
    segments = bytearray()
    for payload in payloads:
        if len(payload) + 2 > 0xFFFF:
            raise ValueError("XMP APP1 payload exceeds segment limit")
        segments += b"\xff\xe1" + struct.pack(">H", len(payload) + 2) + payload

    # Insert after the SOI and any leading APP0/APP1 (JFIF/EXIF) segments.
    insert_at = 2
    while jpeg[insert_at : insert_at + 2] in (b"\xff\xe0", b"\xff\xe1"):
        seg_len = struct.unpack(">H", jpeg[insert_at + 2 : insert_at + 4])[0]
        insert_at += 2 + seg_len
    return jpeg[:insert_at] + bytes(segments) + jpeg[insert_at:]


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


def _raw_cache_key(
    input_path: Path, half_size: bool, max_size: int | None, denoise_model: str | None
) -> tuple[Any, ...]:
    try:
        stat = input_path.stat()
        base = (str(input_path), bool(half_size), int(max_size or 0), stat.st_size, int(stat.st_mtime_ns))
    except OSError:
        base = (str(input_path), bool(half_size), int(max_size or 0))
    return (*base, denoise_model or "")


@dataclass(frozen=True)
class ColorRenderer:
    """Which colour pipeline a recipe selects, decided before the RAW is decoded.

    Both branches consume camera-native RGB and deliver scene-linear ProPhoto,
    so the choice only changes what happens after postprocess. `active` False
    means neither is available and the decode falls back to LibRaw's ProPhoto.
    """

    sony_look: LookCalibration | None = None
    sony_style: str | None = None
    dcp_profile: DcpProfile | None = None
    dcp_selection: dict[str, Any] | None = None

    @property
    def active(self) -> bool:
        return self.sony_look is not None or self.dcp_profile is not None


def resolve_color_renderer(
    root: Path,
    dcp_arg: str | None,
    disable_dcp: bool,
    recipe: dict[str, Any],
    metadata: RawMetadata,
    input_path: Path,
) -> ColorRenderer:
    """Pick the colour pipeline: Sony's own rendering, a DCP, or neither.

    profileId "sony" reproduces Imaging Edge from calibration inside the RAW
    itself (see sony/profile.py), so it needs no profile files. It takes two
    things the shot may not have: that calibration (Sony RAWs only) and a
    Creative Look these two stages can express — Black & White and Sepia
    desaturate in stages this pipeline does not reproduce. Missing either, and
    an explicit --dcp, falls through to the DCP lookup.
    """
    if not disable_dcp and dcp_arg is None and recipe.get("profileId") == "sony":
        style = normalize_style(metadata.creative_style)
        look = calibration_for(input_path, style) if sony_can_render(style) else None
        if look is not None:
            return ColorRenderer(sony_look=look, sony_style=style)
    profile, selection = resolve_dcp_profile(root, dcp_arg, disable_dcp, recipe, metadata)
    return ColorRenderer(dcp_profile=profile, dcp_selection=selection)


def render_color(
    renderer: ColorRenderer,
    camera_rgb: np.ndarray,
    metadata: RawMetadata,
    root: Path,
    recipe: dict[str, Any],
) -> tuple[np.ndarray, dict[str, Any]]:
    """Camera RGB -> scene-linear ProPhoto plus its colour-profile report."""
    if renderer.sony_look is not None:
        linear, info = apply_sony_profile(
            camera_rgb, renderer.sony_look, renderer.sony_style,
            highlights=metadata.look_highlights, shadows=metadata.look_shadows,
            contrast=metadata.look_contrast, fade=metadata.look_fade,
            dro=metadata.dro_active,
        )
        return linear, info.to_json()

    correction = find_camera_match(root, renderer.dcp_selection) if recipe.get("cameraMatch", True) else None
    linear, dcp_info = apply_dcp_profile(camera_rgb, renderer.dcp_profile, correction)
    color_profile = dcp_info.to_json()
    color_profile["selection"] = renderer.dcp_selection
    return linear, color_profile


def prepare_linear(
    input_path: Path,
    recipe: dict[str, Any],
    root: Path,
    dcp_arg: str | None,
    disable_dcp: bool,
    half_size: bool = False,
    max_size: int | None = None,
    denoise_model: str | None = None,
    raw_provider: Callable[[], rawpy.RawPy] | None = None,
    store_cache: bool = True,
) -> PreparedLinear:
    """Decode RAW into linear working-space RGB (cached per variant).

    `raw_provider` lets a caller that needs several variants of the same file
    (noisy + denoised) share one opened LibRaw handle instead of re-reading and
    re-unpacking the RAW per variant. The provider owns the handle's lifetime;
    it is only invoked on a cache miss. A denoise variant mutates the shared
    handle's Bayer data in place, so decode the noisy variant first.

    `store_cache=False` decodes without populating the decode caches: a
    full-resolution export is hundreds of MB and one-off, so caching it would
    pin gigabytes with no reuse and evict the cheap preview entries.
    """
    if not is_raw(input_path):
        return prepare_rendered_image(input_path, half_size=half_size, max_size=max_size)

    cache_key = _raw_cache_key(input_path, half_size, max_size, denoise_model)

    # Cache hit: re-apply DCP on cached camera RGB without re-decoding RAW
    with _CACHE_LOCK:
        cached_camera = RAW_CAMERA_CACHE.get(cache_key)
        if cached_camera is not None:
            RAW_CAMERA_CACHE.move_to_end(cache_key)
    if cached_camera is not None:
        camera_rgb, metadata = cached_camera
        renderer = resolve_color_renderer(root, dcp_arg, disable_dcp, recipe, metadata, input_path)
        if renderer.active:
            linear, color_profile = render_color(renderer, camera_rgb, metadata, root, recipe)
            return PreparedLinear(linear=linear, metadata=metadata, color_profile=color_profile)
        # No colour pipeline available any more; fall through to re-decode
        with _CACHE_LOCK:
            RAW_CAMERA_CACHE.pop(cache_key, None)

    # Cache hit for the no-profile fallback: linear is final unless a colour
    # pipeline became available again.
    with _CACHE_LOCK:
        cached_fallback = _FALLBACK_CACHE.get(cache_key)
        if cached_fallback is not None:
            _FALLBACK_CACHE.move_to_end(cache_key)
    if cached_fallback is not None:
        linear, metadata, color_profile = cached_fallback
        if not resolve_color_renderer(root, dcp_arg, disable_dcp, recipe, metadata, input_path).active:
            return PreparedLinear(linear=linear, metadata=metadata, color_profile=color_profile)
        with _CACHE_LOCK:
            _FALLBACK_CACHE.pop(cache_key, None)

    with rawpy.imread(str(input_path)) if raw_provider is None else nullcontext(raw_provider()) as raw:
        metadata = read_raw_metadata(input_path, raw)
        # RAW-domain denoise: clean the Bayer mosaic in place so the postprocess
        # calls below demosaic the denoised data. Heavy, so it is cached via
        # cache_key (which includes denoise_model) like any other camera RGB.
        # Returns None (mosaic untouched) on non-2x2 CFAs such as Fuji X-Trans.
        if denoise_model:
            denoise_raw_inplace(raw, get_denoiser(denoise_model))
        renderer = resolve_color_renderer(root, dcp_arg, disable_dcp, recipe, metadata, input_path)
        if not renderer.active:
            # Scene-referred fallback: deliver linear ProPhoto (D50) so the browser
            # edits in the same wide-gamut working space as the DCP path. LibRaw
            # still normalises the white level, so this path does not carry true
            # >1.0 highlight headroom (acceptable for a no-profile fallback).
            # np.divide with an explicit dtype scales straight from the uint16
            # postprocess output into one float32 buffer; an .astype() first
            # would leave a second full-resolution copy live.
            linear = np.divide(
                raw.postprocess(
                    use_camera_wb=True,
                    no_auto_bright=True,
                    output_color=rawpy.ColorSpace.ProPhoto,
                    gamma=(1, 1),
                    output_bps=16,
                    half_size=half_size,
                ),
                65535.0,
                dtype=np.float32,
            )
            linear = apply_camera_crop(linear, metadata.camera_crop)
            if max_size:
                linear = downsample_linear(linear, max_size)
            color_profile = libraw_color_profile_info()
            if store_cache:
                _remember(_FALLBACK_CACHE, cache_key, (linear, metadata, color_profile), _FALLBACK_CACHE_MAX)
        else:
            camera_rgb = postprocess_camera_native(raw, half_size=half_size)
            camera_rgb = apply_camera_crop(camera_rgb, metadata.camera_crop)
            if camera_rgb.shape[-1] != 3:
                raise ValueError("Profiled rendering currently supports only three-channel camera RGB data")
            if max_size:
                camera_rgb = downsample_linear(camera_rgb, max_size)
            # Cache camera RGB so colour-pipeline changes skip RAW re-decode
            if store_cache:
                _remember(RAW_CAMERA_CACHE, cache_key, (camera_rgb, metadata), RAW_CAMERA_CACHE_MAX)
            linear, color_profile = render_color(renderer, camera_rgb, metadata, root, recipe)

    return PreparedLinear(linear=linear, metadata=metadata, color_profile=color_profile)


def prepare_rendered_image(
    input_path: Path,
    half_size: bool = False,
    max_size: int | None = None,
) -> PreparedLinear:
    """Decode an already-rendered image (JPEG/PNG/TIFF) into the RAW path's contract.

    No decode-level cache: Pillow decode is milliseconds against LibRaw's
    seconds, and the two caches the RAW path needs exist to make *DCP switching*
    and *denoise amount* cheap — neither applies here. daemon_linear's
    _LINEAR_CACHE still covers repeat requests for the same variant.
    """
    try:
        linear, color_profile = decode_image_linear(input_path)
    except (UnidentifiedImageError, OSError) as error:
        raise UnsupportedSourceError(
            f"{input_path.name} could not be decoded as an image"
        ) from error
    full_height, full_width = linear.shape[:2]

    # half_size is LibRaw's Bayer binning; the closest honest equivalent for a
    # demosaiced image is a plain half-resolution downsample, and it keeps the
    # preview/export cost ratio the frontend assumes. Fold both constraints into
    # one target so a full->half->max_size chain doesn't resize twice.
    targets = []
    if half_size:
        targets.append(max(1, max(full_width, full_height) // 2))
    if max_size:
        targets.append(max_size)
    if targets:
        linear = downsample_linear(linear, min(targets))

    exif = read_exiftool_metadata(input_path)
    metadata = RawMetadata(
        make=exif.get("Make"),
        model=exif.get("Model"),
        lens_model=exif.get("LensModel"),
        creative_style=None,
        white_balance=exif.get("WhiteBalance"),
        # A rendered image has no mosaic and no per-shot correction data, so every
        # RAW-only field stays None and the frontend hides the controls that need it.
        camera_white_balance=None,
        black_level=None,
        white_level=None,
        rgb_xyz_matrix=None,
        full_width=full_width,
        full_height=full_height,
        camera_crop=None,
        lens_corr=None,
    )
    return PreparedLinear(linear=linear, metadata=metadata, color_profile=color_profile)


def downsample_linear(linear: np.ndarray, max_size: int) -> np.ndarray:
    height, width = linear.shape[:2]
    long_edge = max(height, width)
    if long_edge <= max_size:
        return linear
    scale = max_size / float(long_edge)
    new_w = max(1, round(width * scale))
    new_h = max(1, round(height * scale))
    channels = linear.shape[2]
    out = np.empty((new_h, new_w, channels), dtype=np.float32)
    for channel in range(channels):
        plane = Image.fromarray(linear[..., channel], mode="F")
        plane = plane.resize((new_w, new_h), Image.Resampling.BILINEAR)
        out[..., channel] = np.asarray(plane, dtype=np.float32)
    return out


def _parse_int_pair(value: Any) -> tuple[int, int] | None:
    if isinstance(value, list):
        parts = value
    elif isinstance(value, str):
        parts = value.split()
    else:
        return None
    if len(parts) != 2:
        return None
    try:
        return int(float(parts[0])), int(float(parts[1]))
    except (TypeError, ValueError):
        return None


def camera_crop_rect(
    raw: rawpy.RawPy, exif: dict[str, Any]
) -> tuple[tuple[int, int, int, int], tuple[int, int]] | None:
    """The camera-intended crop (DNG DefaultCropOrigin/Size) in the postprocess
    output frame.

    LibRaw renders the full visible sensor area, which extends a few pixels
    past the region the camera actually framed (and carries the camera's
    aspect-mode crop, e.g. 4:3/16:9 shooting modes). The DNG tags are relative
    to the visible area's top-left in sensor orientation; postprocess applies
    the camera flip, so the rect is transformed into the flipped frame here.
    Returns ((x, y, w, h), (frame_w, frame_h)), or None when absent/degenerate.
    """
    origin = _parse_int_pair(exif.get("DefaultCropOrigin"))
    size = _parse_int_pair(exif.get("DefaultCropSize"))
    sizes = raw.sizes
    if origin is None or size is None or sizes is None:
        return None
    x, y = origin
    w, h = size
    vw, vh = int(sizes.width), int(sizes.height)
    if w <= 0 or h <= 0 or x < 0 or y < 0 or x + w > vw or y + h > vh:
        return None
    if w == vw and h == vh:
        return None
    flip = int(sizes.flip or 0)
    if flip == 3:  # 180°
        x, y = vw - x - w, vh - y - h
    elif flip == 5:  # 90° counter-clockwise
        x, y, w, h = y, vw - x - w, h, w
        vw, vh = vh, vw
    elif flip == 6:  # 90° clockwise
        x, y, w, h = vh - y - h, x, h, w
        vw, vh = vh, vw
    return (x, y, w, h), (vw, vh)


def apply_camera_crop(
    arr: np.ndarray, crop: tuple[tuple[int, int, int, int], tuple[int, int]] | None
) -> np.ndarray:
    """Trim a postprocessed image to the camera crop, scaling the full-frame
    rect down when the decode is half-size."""
    if crop is None:
        return arr
    (x, y, w, h), (fw, fh) = crop
    ah, aw = arr.shape[:2]
    sx = aw / fw
    sy = ah / fh
    x0 = min(max(round(x * sx), 0), aw - 1)
    y0 = min(max(round(y * sy), 0), ah - 1)
    x1 = min(max(round((x + w) * sx), x0 + 1), aw)
    y1 = min(max(round((y + h) * sy), y0 + 1), ah)
    if x0 == 0 and y0 == 0 and x1 == aw and y1 == ah:
        return arr
    return np.ascontiguousarray(arr[y0:y1, x0:x1])


def read_raw_metadata(input_path: Path, raw: rawpy.RawPy) -> RawMetadata:
    exif = read_exiftool_metadata(input_path)
    sizes = raw.sizes
    camera_crop = camera_crop_rect(raw, exif)
    if camera_crop:
        full_width, full_height = camera_crop[0][2], camera_crop[0][3]
    else:
        full_width = int(sizes.iwidth) if sizes else None
        full_height = int(sizes.iheight) if sizes else None
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
        full_width=full_width,
        full_height=full_height,
        camera_crop=camera_crop,
        lens_corr=sony_lens_corrections(exif),
        look_highlights=_exif_int(exif.get("Highlights")),
        look_shadows=_exif_int(exif.get("Shadows")),
        look_fade=_exif_int(exif.get("Fade")),
        # exiftool prints Sony's zero for these as "Normal", not "0".
        look_contrast=_exif_int(exif.get("Contrast")),
        dro_active=str(exif.get("DynamicRangeOptimizer") or "Off").strip().lower() != "off",
    )


def _exif_int(value: Any) -> int:
    """One signed exif integer, 0 when absent or unparseable.

    exiftool renders these as "+1" / "-6" strings, which int() handles, but a
    body that doesn't write the tag at all is the common case and simply means
    no tweak was applied.
    """
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _exif_int_list(value: Any) -> list[int] | None:
    # exiftool -j renders int16u arrays as a space-separated string.
    if isinstance(value, str):
        try:
            return [int(token) for token in value.split()]
        except ValueError:
            return None
    if isinstance(value, list):
        try:
            return [int(v) for v in value]
        except (TypeError, ValueError):
            return None
    return None


def sony_lens_corrections(exif: dict[str, Any]) -> dict[str, Any] | None:
    """Map Sony's per-shot correction splines (SubIFD DistortionCorrParams /
    VignettingCorrParams / ChromaticAberrationCorrParams) to vendor-neutral
    factor tables. Each array is `nc` followed by nc int16 knot values (CA: 2*nc,
    R then B), knots evenly spaced in radius normalised to the frame's
    half-diagonal: knots[i] = (i + 0.5) / (nc - 1). Fixed-point scales are the
    community-documented ones (exiftool / darktable's reverse engineering):
    distortion sampling factor p*2^-14 + 1 (corrected position -> distorted
    source position), vignetting gain 1 / 2^(0.5 - 2^(p*2^-13 - 1)), lateral CA
    per-channel radial factor p*2^-21 + 1.

    The output frames these as "sample the recorded frame at factor*r and
    multiply by gain(r)", which is exactly the form the WebGL sampler consumes.
    """
    dist = _exif_int_list(exif.get("DistortionCorrParams"))
    vig = _exif_int_list(exif.get("VignettingCorrParams"))
    ca = _exif_int_list(exif.get("ChromaticAberrationCorrParams"))
    if not dist or not vig:
        return None
    nc = dist[0]
    if nc < 2 or nc > 16 or len(dist) < nc + 1 or vig[0] != nc or len(vig) < nc + 1:
        return None
    out: dict[str, Any] = {
        "knots": [(i + 0.5) / (nc - 1) for i in range(nc)],
        "distortion": [dist[i + 1] * 2**-14 + 1 for i in range(nc)],
        "vignetting": [1 / 2 ** (0.5 - 2 ** (vig[i + 1] * 2**-13 - 1)) for i in range(nc)],
    }
    if ca and ca[0] == 2 * nc and len(ca) >= 2 * nc + 1:
        out["caR"] = [ca[i + 1] * 2**-21 + 1 for i in range(nc)]
        out["caB"] = [ca[nc + i + 1] * 2**-21 + 1 for i in range(nc)]
    return out


def read_exiftool_metadata(input_path: Path) -> dict[str, Any]:
    # Memoized by (path, size, mtime): each call spawns a Perl process
    # (~100-200 ms) and every decode of the same unchanged file asked again.
    try:
        stat = input_path.stat()
    except OSError:
        return {}
    return dict(_read_exiftool_metadata_cached(str(input_path), stat.st_size, stat.st_mtime_ns))


@functools.lru_cache(maxsize=64)
def _read_exiftool_metadata_cached(path: str, size: int, mtime_ns: int) -> dict[str, Any]:
    input_path = Path(path)
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
                "-DefaultCropOrigin",
                "-DefaultCropSize",
                "-DistortionCorrParams",
                "-VignettingCorrParams",
                "-ChromaticAberrationCorrParams",
                # The in-camera Creative Look tweaks. Group-qualified on
                # purpose: Sony writes these alongside ExifIFD tags of the same
                # bare name, and an unqualified request can pick the wrong one.
                "-Sony:Highlights",
                "-Sony:Shadows",
                "-Sony:Fade",
                "-Sony:Contrast",
                # DRO is a whole stage (ZcTaskVatr) that this pipeline does not
                # reproduce, and it runs only when the shot asked for it — 2 of
                # 65 in the reference set. Knowing which shots those are is the
                # difference between a limitation and a lie.
                "-DynamicRangeOptimizer",
                str(input_path),
            ],
            env=exiftool_env(),
        )
    except subprocess.CalledProcessError:
        return {}

    records = json.loads(output)
    record = records[0] if records else {}
    out: dict[str, Any] = {key: string_or_none(record.get(key)) for key in ["Make", "Model", "LensModel", "CreativeStyle", "WhiteBalance"]}
    for key in [
        "DefaultCropOrigin",
        "DefaultCropSize",
        "DistortionCorrParams",
        "VignettingCorrParams",
        "ChromaticAberrationCorrParams",
        "Highlights",
        "Shadows",
        "Fade",
        "Contrast",
        "DynamicRangeOptimizer",
    ]:
        out[key] = record.get(key)
    return out


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


@functools.lru_cache(maxsize=32)
def _load_camera_match(path_str: str, size: int, mtime_ns: int) -> tuple[Any, int] | None:
    """Load a fitted camera-match table, memoised on the file's identity.

    Keyed by size+mtime like the other profile caches, so re-fitting a table is
    picked up on the next decode without restarting the daemon.
    """
    from .dcp import DcpHueSatMap
    from .fit_profile import load as load_table

    loaded = load_table(Path(path_str))
    if loaded is None:
        return None
    table, dims, encoding = loaded
    return DcpHueSatMap(dimensions=dims, data=table), encoding


def _camera_match_path_for(root: Path, selection: dict[str, Any] | None) -> Path | None:
    if not selection:
        return None
    camera = selection.get("camera")
    code = selection.get("matchedCode")
    if not camera or not code:
        return None
    return camera_match_path(root, str(camera), str(code))


def has_camera_match(root: Path, selection: dict[str, Any] | None) -> bool:
    """Whether a fitted table exists for this body+style, regardless of the toggle."""
    path = _camera_match_path_for(root, selection)
    return path is not None and path.exists()


def find_camera_match(root: Path, selection: dict[str, Any] | None) -> tuple[Any, int] | None:
    """A camera-match table for the camera+style the DCP lookup settled on."""
    path = _camera_match_path_for(root, selection)
    if path is None:
        return None
    try:
        st = path.stat()
    except OSError:
        return None
    return _load_camera_match(str(path), st.st_size, int(st.st_mtime_ns))


def resolve_dcp_profile(
    root: Path, dcp_arg: str | None, disable_dcp: bool, recipe: dict[str, Any], metadata: RawMetadata
) -> tuple[DcpProfile | None, dict[str, Any] | None]:
    if disable_dcp:
        return None, None

    if dcp_arg:
        path = resolve_path(root, dcp_arg)
        return load_dcp_profile(path), {"mode": "explicit", "path": str(path)}

    if recipe.get("profileId") not in PROFILED_IDS:
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

    def selection(**extra: Any) -> dict[str, Any]:
        return {
            "mode": "auto",
            "root": str(profile_root),
            "camera": profile_dir.name,
            "availableCodes": available_profile_codes(profile_dir),
            **extra,
        }

    # A user-picked style wins, but only if this body ships it. Switching to a
    # camera without that style falls through to the automatic match rather than
    # dropping the profile entirely — the picker has no "auto" entry to fall back
    # to, so an unresolvable code must not leave the image unprofiled.
    if override_code:
        code = normalize_profile_code(override_code)
        if code:
            profile_path = find_dcp_by_code(profile_dir, code)
            if profile_path is not None:
                return profile_path, selection(matchedCode=code, reason="user-selected")

    creative_style = normalize_profile_code(metadata.creative_style)
    for code, reason in [(creative_style, "creativeStyle"), ("ST", "fallback-standard")]:
        if not code:
            continue
        profile_path = find_dcp_by_code(profile_dir, code)
        if profile_path is not None:
            return profile_path, selection(
                creativeStyle=metadata.creative_style, matchedCode=code, reason=reason
            )

    # The camera's style is one we have no profile for; fall back to whatever the
    # body does ship and report the code so the picker reflects what is applied.
    # Code and path come from one list, so the reported code always names the
    # file actually loaded. A profile whose filename breaks the convention still
    # gets applied, just unlabelled — better unprofiled-but-rendered than neither.
    shipped = profile_codes_with_paths(profile_dir)
    if shipped:
        fallback_code, fallback_path = shipped[0]
    else:
        uncoded = sorted(profile_dir.glob("*.dcp"))
        if not uncoded:
            return None
        fallback_code, fallback_path = None, uncoded[0]

    return fallback_path, selection(
        creativeStyle=metadata.creative_style,
        matchedCode=fallback_code,
        reason="first-available",
    )


def find_camera_profile_dir(profile_root: Path, metadata: RawMetadata) -> Path | None:
    return _find_camera_profile_dir_cached(profile_root, metadata.make, metadata.model)


# Both lookups scan the vendored Adobe profile tree (hundreds of camera dirs /
# dozens of .dcp files) and run on every decode and DCP re-apply, so they are
# memoized. The tree is static vendored data; profiles installed while the
# daemon runs are picked up on restart.
@functools.lru_cache(maxsize=64)
def _find_camera_profile_dir_cached(profile_root: Path, make: str | None, model: str | None) -> Path | None:
    exact_name = f"{format_camera_make(make)} {model}".strip()
    exact_dir = profile_root / exact_name
    if exact_dir.exists():
        return exact_dir

    model_key = normalize_profile_name(model)
    make_key = normalize_profile_name(make)
    for candidate in sorted(profile_root.iterdir()):
        if not candidate.is_dir():
            continue
        name_key = normalize_profile_name(candidate.name)
        if model_key and model_key in name_key and (not make_key or make_key in name_key):
            return candidate

    return None


@functools.lru_cache(maxsize=256)
def profile_codes_with_paths(profile_dir: Path) -> list[tuple[str, Path]]:
    """The camera's own style codes paired with the .dcp file each came from.

    The "<name> camera <code>.dcp" convention is parsed here and nowhere else,
    so a vendor that names files differently breaks in one place rather than
    desynchronising the code the picker offers from the file that gets loaded.
    """
    found: list[tuple[str, Path]] = []
    seen: set[str] = set()
    marker = " camera "
    for candidate in sorted(profile_dir.glob("*.dcp")):
        stem = candidate.stem
        index = stem.lower().rfind(marker)
        if index == -1:
            continue
        code = stem[index + len(marker):].strip().upper()
        if code and code not in seen:
            seen.add(code)
            found.append((code, candidate))
    return found


def available_profile_codes(profile_dir: Path) -> list[str]:
    """The style codes this body ships.

    The frontend builds its style picker from this rather than a hardcoded list,
    so a body that ships no VV2 (or ships a code we have never seen) cannot be
    offered a style that resolves to nothing.
    """
    return [code for code, _ in profile_codes_with_paths(profile_dir)]


def find_dcp_by_code(profile_dir: Path, code: str) -> Path | None:
    wanted = code.upper()
    for candidate_code, path in profile_codes_with_paths(profile_dir):
        if candidate_code == wanted:
            return path
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


def libraw_color_profile_info() -> dict[str, Any]:
    return {
        "kind": "libraw-matrix",
        "matrix": "rawpy/LibRaw camera to linear ProPhoto",
        "toneCurveSamples": 0,
        "workingSpace": "linear-prophoto-d50",
        "profileToneCurve": None,
        "note": "No DCP profile supplied; this is a matrix fallback, not a calibrated camera profile.",
    }


def open_rgb(path: Path | Any) -> Image.Image:
    with Image.open(path) as image:
        return ImageOps.exif_transpose(image).convert("RGB")


def save_jpeg(image: Image.Image, output_path: Path, quality: int) -> None:
    ensure_parent(output_path)
    image.save(output_path, "JPEG", quality=quality, optimize=True)


def run_capture(command: list[str], env: dict[str, str] | None = None) -> str:
    result = subprocess.run(command, check=True, env=env, text=True, capture_output=True)
    return result.stdout


def string_or_none(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def clamp(value: float, minimum: float, maximum: float) -> float:
    return min(maximum, max(minimum, value))


def ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


if __name__ == "__main__":
    main()
