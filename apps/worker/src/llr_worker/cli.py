from __future__ import annotations

import argparse
import functools
import hashlib
import io
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
from contextlib import nullcontext, suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import rawpy
from PIL import Image, ImageOps, UnidentifiedImageError

from . import denoise as denoise_module
from .creative_style import normalize_style
from .dcp import DcpProfile, apply_dcp_profile, load_dcp_profile
from .denoise import (
    DEFAULT_MODEL,
    denoise_raw_inplace,
    effective_model,
    get_denoiser,
    model_uses_chroma,
)
from .fit_profile import camera_match_path, libraw_wb_kwargs, postprocess_camera_native
from .imported import decode_image_linear
from .sony import (
    NO_TWEAKS,
    LookTweaks,
    apply_look_overrides,
    apply_sony_profile,
    calibration_for,
    looks_in_file,
    rawnr_simd,
    stops_to_panel,
)
from .sony import as_shot_look as sony_as_shot_look
from .sony import can_render as sony_can_render
from .sony import is_borrowed as sony_is_borrowed
from .sony import itp as sony_itp
from .sony.chromasuppres import chroma_suppres_from_file
from .sony.dro import dro_gain_table, dro_grid, dro_grid_json
from .sony.dro_presets import DRO_LEVEL_AUTO, DRO_LEVEL_MAX
from .sony.marble import marble_block
from .sony.profile import camera_match_table, look_render_info
from .sony.rawnr import detail_restore as sony_detail_restore
from .sony.rawnr import noise_model as sony_noise_model
from .sony.rawnr_simd import iso_strength, manual_strength
from .sony.sharpness import (
    SHARPNESS_DEFAULT,
    SHARPNESS_RANGE_DEFAULT,
    sharpness_block,
    sharpness_calibration,
)
from .sony.spica import spica_block, spica_gain_scale, spica_iso_gain, spica_range_shift
from .sony.sr2 import LookCalibration, dro_strength, ycc_wb_scale

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
    # pulls luma toward a pivot and Saturation (-9..+9) scales the chroma either
    # side of a clamp (sony/chroma.py). Reproducing Sony's rendering needs all.
    look: LookTweaks = NO_TWEAKS
    # In-camera sharpening, already in wire form (sony/sharpness.py). A setting
    # of its own rather than one of the look tweaks, and read here for the same
    # reason the DRO curve is — this is where the file is already open.
    sharpen: dict[str, Any] | None = None
    # The fine half of the same control (sony/spica.py), also in wire form. Its
    # ISO term needs the exif this function already has, which is the other
    # reason it is read here rather than derived in the browser.
    spica: dict[str, Any] | None = None
    # ChromaSuppres' four terms in wire form (sony/chromasuppres.py), derived
    # from four tags of the RAW's own SR2 block. Read here for the same reason
    # the DRO curve is, and cached on the file so a profile rebuild costs no
    # decrypt. None for a file that carries none.
    chroma_suppres: dict[str, Any] | None = None
    # Marble's chroma cleanup: the shot's ISO and the body's thresholds
    # (sony/marble.py), in wire form. None when the exif carries no ISO.
    marble: dict[str, Any] | None = None
    # Whether DRO actually shaped this shot — not merely whether the body was in
    # Auto, which is a weaker claim (see dro_from_exif).
    dro_active: bool = False
    # The camera's own DRO curve for this shot, as a gain against log luminance
    # (sony/dro.py). None when the RAW carries no curve. Read here because this
    # is where the file is open; the render only ever applies a scaled copy.
    dro_gain: list[float] | None = None
    # The engine's bilateral grid for this shot, in wire form. Built here for
    # the same reason as dro_gain — it is the only point the RAW is open — but
    # it costs a pass over the Bayer data, so it is only built when there is a
    # curve to index with it. None means the shader falls back to the global
    # approximation.
    dro_grid: dict[str, Any] | None = None
    # The M/S-size YCbCr frame's white-balance ratio (sony/sr2.ycc_wb_scale),
    # which Imaging Edge multiplies into the camera's WB and LibRaw does not
    # know about. None for a mosaic frame. Read here because the tag is the
    # file's, and every LibRaw decode of the file has to see the same one.
    wb_scale: tuple[float, float, float] | None = None


@dataclass
class PreparedLinear:
    linear: np.ndarray
    metadata: RawMetadata
    color_profile: dict[str, Any]


# The pixel caches below are bounded by bytes rather than entry count: the
# preview decodes at full sensor resolution, so one entry is ~290 MB for a 24 MP
# frame and ~730 MB for a 61 MP one. A fixed entry count would swing between
# holding almost nothing and pinning several GB depending on the body in use.
_MIB = 1 << 20

# Cache raw-decoded camera RGB data keyed by (sourcePath, halfSize, maxSize,
# denoiseModel). DCP code changes re-apply DCP on cached data instead of
# re-decoding the RAW file. Noisy and denoised variants of a source are cached
# under separate keys (denoiseModel = "" vs the model id).
RAW_CAMERA_CACHE: OrderedDict[tuple[Any, ...], tuple[np.ndarray, RawMetadata]] = OrderedDict()
RAW_CAMERA_CACHE_BYTES_MAX = 1536 * _MIB

# No-DCP fallback decode cache, keyed like RAW_CAMERA_CACHE. There is no DCP step
# to re-apply, so this stores the final ProPhoto linear — important once denoise
# makes a re-decode cost seconds (so amount tweaks must not re-run inference).
# Smaller budget than the two above: it only fills for a body with no profile.
_FALLBACK_CACHE: OrderedDict[tuple[Any, ...], tuple[np.ndarray, RawMetadata, dict[str, Any]]] = OrderedDict()
_FALLBACK_CACHE_BYTES_MAX = 768 * _MIB


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


def _remember_pixels(cache: OrderedDict[Any, Any], key: Any, value: Any, cap_bytes: int) -> None:
    """LRU-insert a tuple whose first element is the pixel array, bounded by bytes.

    The newest entry is always retained, even when it alone exceeds `cap_bytes`:
    a single 61 MP frame is ~730 MB, and evicting it on arrival would mean
    re-decoding the RAW on every DCP, denoise or profile change.
    """
    with _CACHE_LOCK:
        cache[key] = value
        cache.move_to_end(key)  # plain assignment keeps an existing key's position
        total = sum(v[0].nbytes for v in cache.values())
        while len(cache) > 1 and total > cap_bytes:
            _, evicted = cache.popitem(last=False)
            total -= evicted[0].nbytes
_SOURCE_LOCKS: dict[str, threading.Lock] = {}
_SOURCE_LOCKS_GUARD = threading.Lock()


def _source_lock(source: str) -> threading.Lock:
    with _SOURCE_LOCKS_GUARD:
        lock = _SOURCE_LOCKS.get(source)
        if lock is None:
            lock = threading.Lock()
            _SOURCE_LOCKS[source] = lock
        return lock


def _warm_kernels() -> None:
    """Compile the numba kernels before the first frame asks for them.

    ITP, RawNR and the plane plumbing either side of it are JIT-compiled
    (sony/itp.py, sony/rawnr_simd.py, denoise.py); with `cache=True` the
    compiled code comes back from disk on later runs, but the very first run on
    a machine pays seconds of LLVM. Doing it here, on a thread, hides that
    behind the upload of the first image.
    """
    for mod in (sony_itp, rawnr_simd, denoise_module):
        try:
            mod.warmup()
        except Exception as error:
            sys.stderr.write(f"kernel warm-up ({mod.__name__}) failed: {error}\n")
            sys.stderr.flush()


def run_daemon(root: Path) -> None:
    sys.stderr.write("llr-worker daemon ready\n")
    sys.stderr.flush()
    threading.Thread(target=_warm_kernels, name="kernel-warmup", daemon=True).start()
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
    if command == "look-profile":
        return daemon_look_profile(request, root)
    if command == "extract-preview":
        return daemon_extract_preview(request, root)
    if command == "export":
        return daemon_export(request, root)
    if command == "ping":
        return {"pong": True, "cacheSize": len(_LINEAR_CACHE)}
    raise ValueError(f"unknown command: {command}")


def _key_tweaks(denoise_model: str | None,
                denoise_tweaks: tuple[float, float]) -> tuple[float, float]:
    """The denoise tweaks that actually reach this model's pixels, rounded.

    A tweak the chosen denoiser ignores must not enter a cache key: it would
    make every value of an inert control mint its own decode — hundreds of MB
    each, evicting the entries the user is working with — to return a
    byte-identical image. `sony` ignores Color NR (it reproduces a filter that
    has no such control), the wavelet does not, so this cannot be a constant.

    ⚠️ `denoise_model` must already be the *effective* one. Resolving the
    fallback later would mean deciding this for a denoiser that never ran, and
    two chroma settings would then collide on one entry with the wavelet's
    pixels — see `denoise.effective_model`.
    """
    edge, chroma = denoise_tweaks
    if denoise_model and not model_uses_chroma(denoise_model):
        chroma = 50.0  # any constant would do; 50 is the slider's neutral
    return (round(float(edge), 3), round(float(chroma), 3))


def _linear_cache_key(
    input_path: Path,
    profile_id: str,
    half_size: bool,
    max_size: int | None,
    dcp_code: str | None,
    denoise_model: str | None,
    denoise_amount: float,
    denoise_tweaks: tuple[float, float] = (50.0, 50.0),
    denoise_strength: float = 1.0,
) -> tuple[Any, ...]:
    """Everything that changes a decoded pixel, and nothing that does not.

    cameraMatch is deliberately absent: its table is a HueSatMap, and every
    HueSatMap now travels to the shader instead of being baked in here, so the
    toggle serves one cached decode. `dcp_code` stays — it still selects the
    colour matrix, which is baked.
    """
    try:
        st = input_path.stat()
        base = (str(input_path), st.st_size, int(st.st_mtime_ns))
    except OSError:
        base = (str(input_path),)
    return (*base, profile_id, bool(half_size), int(max_size or 0), dcp_code or "", denoise_model or "", round(float(denoise_amount), 3), _key_tweaks(denoise_model, denoise_tweaks), round(float(denoise_strength), 4))


_LINEAR_CACHE: OrderedDict[tuple[Any, ...], tuple[np.ndarray, dict[str, Any]]] = OrderedDict()
_LINEAR_CACHE_BYTES_MAX = 1536 * _MIB

# Sony's DRO bilateral grid, keyed by file identity alone — it comes off the
# Bayer data, so no render setting can change it. The look-profile path exists
# precisely to answer without touching pixels, and rebuilding the grid there
# would put a full raw read behind the look picker; a decode fills this first.
_DRO_GRID_CACHE: OrderedDict[tuple[Any, ...], dict[str, Any] | None] = OrderedDict()
_DRO_GRID_CACHE_MAX = 6

# Open, unpacked LibRaw handles, keyed by file identity. Unpacking a Sony
# lossless-compressed 33 MP frame costs LibRaw about 7 s single-threaded, and
# every denoise variant used to pay it again: daemon_linear opened the file,
# decoded, and closed it, so switching Noise Reduction off and on re-read the
# RAW twice over (measured 7.2 s of a 17.5 s toggle). The handle's mosaic is
# mutable -- the RAW-domain denoiser writes it in place -- so each entry keeps a
# pristine copy and restores it before handing the handle out. Per-file locking
# in daemon_worker keeps two requests off one handle at the same time. Small
# on purpose: a handle holds ~70 MB of mosaic plus its pristine copy.
_RAW_HANDLE_CACHE: OrderedDict[tuple[Any, ...], tuple[rawpy.RawPy, np.ndarray]] = OrderedDict()
_RAW_HANDLE_CACHE_MAX = 3


def open_raw_in_memory(input_path: Path) -> rawpy.RawPy:
    """rawpy.imread, but off one sequential read of the file.

    LibRaw unpacks through many small seeks and reads. On a local ext4 disk that
    is nothing; through a Windows drive mounted into WSL (/mnt/e, 9P) it is the
    whole cost: 7.6 s to unpack a 42 MB lossless-compressed ARW against 0.5 s
    for the same file on /tmp. One read of the file is a fraction of a second
    either way, and LibRaw's buffer path does the rest in memory.
    """
    with open(input_path, "rb") as f:
        data = f.read()
    return rawpy.imread(io.BytesIO(data))


def _checkout_raw(input_path: Path) -> rawpy.RawPy:
    """An unpacked LibRaw handle for this file, its mosaic reset to the file's.

    Cached across requests (see _RAW_HANDLE_CACHE). The caller must not close
    it; the cache evicts and closes. A mosaic the previous request denoised in
    place is put back before the handle is returned, so every caller sees the
    RAW as read.
    """
    key = _file_stamp(input_path)
    with _CACHE_LOCK:
        hit = _RAW_HANDLE_CACHE.get(key)
        if hit is not None:
            _RAW_HANDLE_CACHE.move_to_end(key)
    if hit is not None:
        raw, pristine = hit
        np.copyto(raw.raw_image, pristine)
        return raw
    raw = open_raw_in_memory(input_path)
    pristine = raw.raw_image.copy()   # forces the unpack, and keeps the untouched mosaic
    evicted: list[rawpy.RawPy] = []
    with _CACHE_LOCK:
        _RAW_HANDLE_CACHE[key] = (raw, pristine)
        while len(_RAW_HANDLE_CACHE) > _RAW_HANDLE_CACHE_MAX:
            _, (old, _) = _RAW_HANDLE_CACHE.popitem(last=False)
            evicted.append(old)
    for old in evicted:
        old.close()
    return raw


def _file_stamp(input_path: Path) -> tuple[Any, ...]:
    try:
        st = input_path.stat()
        return (str(input_path), st.st_size, int(st.st_mtime_ns))
    except OSError:
        return (str(input_path),)


def dro_grid_cached(input_path: Path, exif: dict[str, Any]) -> dict[str, Any] | None:
    """This shot's DRO grid, opening the RAW only if a decode has not already.

    Returns None both for "no grid possible" and for a file that cannot be read;
    the consumer's fallback is the same either way, so they do not need telling
    apart. A cached None is a real answer and is honoured rather than retried.
    """
    key = _file_stamp(input_path)
    with _CACHE_LOCK:
        if key in _DRO_GRID_CACHE:
            _DRO_GRID_CACHE.move_to_end(key)
            return _DRO_GRID_CACHE[key]
    try:
        with rawpy.imread(str(input_path)) as raw:
            crop = camera_crop_rect(raw, exif)
            value = dro_grid_json(dro_grid(raw, crop[0] if crop else None))
    except (rawpy.LibRawError, OSError, ValueError):
        value = None
    _remember(_DRO_GRID_CACHE, key, value, _DRO_GRID_CACHE_MAX)
    return value


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


# On-disk tier under the in-memory linear cache: the same f16 the response
# carries, kept next to the source as `cache-<key>.f16` plus a `.json` sidecar
# with the profile *before* look/DRO overrides (which are re-derived per request,
# exactly as on a memory hit). Serving a hit is a hardlink into the response
# path, so it costs nothing and the API's per-request unlink leaves the entry
# alone. Bounded by bytes across every session; deleting a source from the
# library drops its entries with it. The worker sources' newest mtime is part of the key so
# a pipeline change cannot serve yesterday's pixels.
_DISK_CACHE_BYTES_MAX = int(os.environ.get("LLR_DISK_CACHE_MB", "8192")) * _MIB
_CODE_STAMP = max(p.stat().st_mtime_ns for p in Path(__file__).parent.rglob("*.py"))


def _disk_cache_path(cache_key: tuple[Any, ...], output_path: Path) -> Path:
    digest = hashlib.sha1(repr((_CODE_STAMP, *cache_key)).encode()).hexdigest()[:24]
    return output_path.with_name(f"cache-{digest}.f16")


def _link_or_copy(src: Path, dst: Path) -> None:
    dst.unlink(missing_ok=True)
    try:
        os.link(src, dst)
    except OSError:  # another filesystem
        shutil.copyfile(src, dst)


def _read_disk_cache(disk: Path, output_path: Path) -> dict[str, Any] | None:
    """The sidecar of a cached decode, with its pixels linked into output_path."""
    try:
        meta = json.loads(disk.with_suffix(".json").read_text())
        os.utime(disk)  # recency for eviction
        _link_or_copy(disk, output_path)
        meta["bytesWritten"] = disk.stat().st_size
        return meta
    except (OSError, ValueError):
        return None


def _write_disk_cache(linear_arr: np.ndarray, meta: dict[str, Any], disk: Path, output_path: Path) -> int:
    """Write the f16 once into the cache, link it to output_path, evict past the cap."""
    tmp = disk.with_suffix(".tmp")
    n = _write_linear_f16(linear_arr, tmp)
    os.replace(tmp, disk)
    disk.with_suffix(".json").write_text(json.dumps(meta))
    _link_or_copy(disk, output_path)
    # ponytail: whole-tree stat on every decode; sessions/<id>/ is the layout the API keeps
    # An entry can carry two names (the DCP alias below), so count and evict by inode.
    by_inode: dict[int, tuple[float, int, list[Path]]] = {}
    for f in disk.parent.parent.glob("*/cache-*.f16"):
        with suppress(OSError):  # swept by the API meanwhile
            st = f.stat()
            by_inode.setdefault(st.st_ino, (st.st_mtime, st.st_size, []))[2].append(f)
    keep = disk.stat().st_ino
    total = sum(size for _, size, _ in by_inode.values())
    for ino, (_, size, names) in sorted(by_inode.items(), key=lambda kv: kv[1][0]):
        if total <= _DISK_CACHE_BYTES_MAX or ino == keep:
            continue
        for f in names:
            f.unlink(missing_ok=True)
            f.with_suffix(".json").unlink(missing_ok=True)
        total -= size
    return n


def _stamp_look_choices(profile: dict[str, Any], input_path: Path) -> dict[str, Any]:
    """Add the picker's two facts: which looks this file has, and the shot's own.

    Properties of the RAW rather than of the request, but the frontend needs them
    the moment a decode lands or the picker cannot be drawn until something else
    asks for a profile. Read from the file so a body shipping more than the ten
    known looks offers its own instead of a hardcoded list.
    """
    if profile.get("kind") != "sony":
        return profile
    profile["availableLooks"] = looks_in_file(input_path)
    profile.setdefault("lookAsShotStyle", profile.get("creativeLook"))
    # The body and its camera-match table are the same kind of fact, and a
    # decode cached before the table existed carries neither (the disk tier
    # has no version). The exif read is memoised, so this costs nothing on a
    # profile that already has them.
    if not profile.get("cameraBody"):
        profile["cameraBody"] = camera_body_from_exif(read_exiftool_metadata(input_path))
    profile.setdefault("profileCameraMatch",
                       camera_match_table(profile["cameraBody"], profile.get("creativeLook"),
                                          LookTweaks.from_json(profile.get("lookTweaks")).fade))
    return profile


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
    # The fitted camera-match table now travels to the shader with the rest of
    # the HueSatMaps, so the toggle is the frontend's alone: the table always
    # ships, and whether to apply it is decided there. Nothing here reads it —
    # it is off both the decode and the cache key.

    # RAW-domain denoise request. amount<=0 (or disabled) is treated as off so
    # the heavy inference and the second decode are skipped entirely.
    denoise_req = request.get("denoise") or {}
    dn_amount = max(0.0, min(1.0, float(denoise_req.get("amount", 1.0))))
    # Edit's Auto: the engine's RAW stage writes back `iso_strength(ISO)` of
    # its own change -- four tenths up to ISO 400, all of it from ISO 1600,
    # linear in ISO between (sony/rawnr_simd.py apply_strength; measured on the
    # engine's input/output planes). Resolved below once the file's denoiser is
    # known: Sony's own filter takes it as `strength` and applies it exactly;
    # the wavelet fallback has no such rule, so there it still rides on the
    # noisy/denoised decode blend as the approximation it always was.
    dn_auto = bool(denoise_req.get("auto", False))
    dn_strength = 1.0
    # Which filter runs is not the client's to pick: llr has one denoiser and
    # the goal is that it is Edit's. The request says whether to denoise and how
    # much, never with what; migrating means moving DEFAULT_MODEL, once.
    #
    # Resolved for the *file*, before anything is keyed on it and whether or
    # not this request denoises. Which of the two ran used to be decided inside
    # prepare_linear, i.e. after both cache keys were built from the requested
    # model — and the two disagree about whether Color NR reaches the pixels,
    # so the key would have been built on the wrong answer. It stays cheap: the
    # tags are lru_cached on file identity and the render reads them anyway.
    #
    # Denoise operates on the Bayer mosaic, which a rendered image does not
    # have, so a rendered image has no denoiser at all.
    file_model = effective_model(DEFAULT_MODEL, input_path) if is_raw(input_path) else None
    if file_model is not None and file_model != DEFAULT_MODEL:
        sys.stderr.write(
            f"denoise {input_path.name}: no Sony noise tags, "
            f"falling back from {DEFAULT_MODEL!r} to {file_model!r}\n")
    # Whether Color NR reaches this file's denoiser, which the UI hides the
    # slider on. A property of the file, not of the request: it goes out on
    # every return below, denoising on or off, so the answer cannot flip with
    # the toggle. Sony's own filter has no such control; the wavelet does.
    dn_uses_chroma = file_model is not None and model_uses_chroma(file_model)
    if dn_auto and file_model is not None:
        iso = _exif_int(read_exiftool_metadata(input_path).get("ISO"))
        # An unreadable ISO leaves the request alone rather than silently
        # denoising at four tenths -- guessing here would be invisible.
        if iso:
            if file_model == "sony":
                dn_strength = iso_strength(float(iso))
            else:
                dn_amount = iso_strength(float(iso))
    elif file_model == "sony":
        # Edit's manual panel, measured at export: its default amount of 50 is
        # Auto to the bit, 0 is off, and in between the write-back strength is
        # the honest straight line (sony/rawnr_simd.py manual_strength). Above
        # 50 the engine changes the filter itself, which this cannot follow, so
        # the strength holds at Auto's. The amount is spent here, as the
        # engine's own write-back, not as a blend of two decodes.
        iso = _exif_int(read_exiftool_metadata(input_path).get("ISO"))
        if iso:
            dn_strength = manual_strength(dn_amount * 100.0, float(iso))
            dn_amount = 1.0 if dn_strength > 0.0 else 0.0
    # Forcing denoise off here for a rendered image (rather than no-op'ing
    # deeper) keeps the two-decode blend below from decoding an identical image
    # twice.
    dn_model: str | None = None
    if bool(denoise_req.get("enabled", False)) and dn_amount > 0.0:
        dn_model = file_model
    # Edge and colour ride Edit.exe's own 0..100 scale with 50 neutral, so a
    # recipe that names them means the same thing in both applications. Unlike
    # `amount` they are not a blend of two decodes — they change the denoised
    # result itself — so they belong in the cache key rather than in the lerp.
    # ⚠️ "They change the result" is per-denoiser, not universal: `_key_tweaks`
    # drops the ones the chosen denoiser ignores, or every value of an inert
    # slider mints its own full-size decode.
    dn_edge = max(0.0, min(100.0, float(denoise_req.get("edge", 50.0))))
    dn_chroma = max(0.0, min(100.0, float(denoise_req.get("chroma", 50.0))))

    # Overrides for the shot's Creative Look tweaks, and for which look to render
    # at all. Both are deliberately absent from the cache key: neither the five
    # tweaks nor the choice of look reaches the pixels — the looks share the
    # body's one hue-segmented matrix — so a cached decode serves every setting
    # and only the profile that rides along is re-derived.
    look_overrides = request.get("look")
    look_style = normalize_style(request.get("style"))
    # DRO belongs in this list too: it is one gain per pixel, and a gain
    # commutes with the colour matrix, so the shader applies it to the same
    # cached pixels and only the table riding along changes.
    dro_request = request.get("dro")
    dro_override = None if dro_request is None else float(dro_request)
    # Absent means "leave the level alone", which is not the same as Auto — the
    # rebuild has to be able to keep a manual level across a look change.
    dro_level_override = (None if request.get("droLevel") is None
                          else dro_level_from_request(request))

    cache_key = _linear_cache_key(input_path, profile_id, half_size, max_size, dcp_code, dn_model, dn_amount, (dn_edge, dn_chroma), dn_strength)

    # What is cached is always the camera's own rendering; this request's
    # overrides are layered on the way out, so they never outlive it. Stamped
    # first so lookAsShotStyle captures the look the body chose.
    def finish(color_profile: dict[str, Any]) -> dict[str, Any]:
        return apply_look_overrides(_stamp_look_choices(color_profile, input_path), input_path, look_overrides,
                                    look_style, dro_override, dro_level_override)

    # Disk tier first: a hit is a hardlink, cheaper than re-encoding the memory
    # tier's float32. Read regardless of purpose (an export wants the same pixels
    # a preview cached), written only below where store_cache says so.
    disk = _disk_cache_path(cache_key, output_path)
    cached_meta = _read_disk_cache(disk, output_path)
    if cached_meta is not None:
        cached_meta["colorProfile"] = finish(cached_meta["colorProfile"])
        cached_meta["output"] = str(output_path)
        return cached_meta

    with _CACHE_LOCK:
        cached_linear = _LINEAR_CACHE.get(cache_key)
        if cached_linear is not None:
            _LINEAR_CACHE.move_to_end(cache_key)
    if cached_linear is not None:
        linear_arr, color_profile = cached_linear
        color_profile = finish(color_profile)
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
            "denoiseUsesChroma": dn_uses_chroma,
        }

    recipe = merge_recipe(PROFILES[profile_id], {})
    if dcp_code:
        recipe["dcpCode"] = dcp_code

    # The noisy and denoised variants share one unpacked LibRaw handle, and so
    # does the next request for this file: _checkout_raw keeps the handle open
    # across requests (unpacking is the single most expensive step on a
    # lossless-compressed frame) and resets its mosaic between uses. Opening is
    # skipped entirely when every variant is a cache hit.
    def open_raw() -> rawpy.RawPy:
        return _checkout_raw(input_path)

    # Same reasoning as the _LINEAR_CACHE gate below: a one-off export decode
    # must not be pinned in any cache, so the intent is passed down to the decode
    # caches rather than re-derived there. It has to be stated by the caller —
    # the preview decodes at full resolution too, so the request shape no longer
    # tells the two apart.
    store_cache = request.get("purpose") != "export"

    try:
        if dn_model is not None and dn_amount >= 1.0:
            # Full-strength denoise: the noisy decode would be blended away
            # entirely, so skip it and decode only the denoised variant.
            prepared = prepare_linear(
                input_path, recipe, root,
                dcp_arg=None, disable_dcp=False,
                half_size=half_size, max_size=max_size,
                denoise_model=dn_model,
                denoise_tweaks=(dn_edge, dn_chroma),
                denoise_strength=dn_strength,
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
                    denoise_tweaks=(dn_edge, dn_chroma),
                    denoise_strength=dn_strength,
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
        pass   # the handle stays in _RAW_HANDLE_CACHE; eviction closes it

    # Stash full-resolution dims on the color profile so both this response and
    # the _LINEAR_CACHE-hit path above can report zoom relative to the original.
    prepared.color_profile["fullWidth"] = prepared.metadata.full_width
    prepared.color_profile["fullHeight"] = prepared.metadata.full_height
    prepared.color_profile["lensCorr"] = prepared.metadata.lens_corr
    # Whether a fitted table exists for this body+style, independent of whether it
    # was applied this render. The frontend needs this to keep showing the toggle
    # after the user switches the match off (at which point cameraMatch goes null).
    prepared.color_profile["cameraMatchAvailable"] = has_camera_match(root, prepared.color_profile.get("selection"))
    # Before the cache insert and before the overrides: these describe the file,
    # so they belong on the cached profile too, and lookAsShotStyle has to be
    # read while creativeLook still names the look the body chose.
    _stamp_look_choices(prepared.color_profile, input_path)

    # Cache processed sRGB so switching back to this DCP code is instant. The
    # decode/DCP/downsample paths already yield C-contiguous float32, so this is a
    # no-op there (avoids a full ~tens-of-MB copy) and only copies when it must.
    linear_arr = np.ascontiguousarray(prepared.linear, dtype=np.float32)
    # Skip caching exports: they are one-off, so an entry that can run to
    # hundreds of MB would pin memory with no reuse and evict live preview
    # entries. Previews still cache — that is what keeps a DCP or denoise change
    # off the RAW decode path.
    if store_cache:
        _remember_pixels(_LINEAR_CACHE, cache_key, (linear_arr, prepared.color_profile), _LINEAR_CACHE_BYTES_MAX)

    meta = {
        "width": prepared.linear.shape[1],
        "height": prepared.linear.shape[0],
        "fullWidth": prepared.metadata.full_width,
        "fullHeight": prepared.metadata.full_height,
        "colorProfile": prepared.color_profile,
        "dtype": "float16",
        "denoiseUsesChroma": dn_uses_chroma,
    }
    bytes_written = (_write_disk_cache(linear_arr, meta, disk, output_path) if store_cache
                     else _write_linear_f16(linear_arr, output_path))
    # The browser answers an auto-matched decode by asking for that code by name,
    # which is the same DCP file under a different key: link the entry there too
    # rather than decode the same pixels twice.
    matched = (prepared.color_profile.get("selection") or {}).get("matchedCode")
    if store_cache and not dcp_code and matched:
        alias_key = _linear_cache_key(input_path, profile_id, half_size, max_size, matched, dn_model, dn_amount,
                                      (dn_edge, dn_chroma), dn_strength)
        alias = _disk_cache_path(alias_key, output_path)
        for suffix in (".f16", ".json"):
            with suppress(OSError):
                os.link(disk.with_suffix(suffix), alias.with_suffix(suffix))

    return {
        **meta,
        "output": str(output_path),
        "colorProfile": finish(prepared.color_profile),
        "bytesWritten": bytes_written,
    }


def daemon_look_profile(request: dict[str, Any], root: Path) -> dict[str, Any]:
    """The Sony profile for a set of Creative Look tweaks, with no pixels at all.

    Not one of the five reaches the matrix (sony.profile.look_render_info), so a
    moved slider needs nothing more than this: ~75 kB of curve and chroma terms,
    against the tens of megabytes a re-decode would send back for a frame the
    browser is already holding. Reading the calibration means reading and
    decrypting the RAW's SR2 block, which is cached, and the exif read alongside
    it is memoised — no decode happens on this path.

    The same holds for the choice of look itself: every ARW carries all of them
    and they share the body's one matrix, so switching is this request too, not a
    re-decode. `style` picks one; absent, the shot renders as the body recorded.

    A null profile is the honest answer for a shot this path cannot render (a
    non-Sony body, a look with no calibration): the caller keeps what it has.
    """
    input_path = resolve_path(root, request["input"])
    exif = read_exiftool_metadata(input_path)
    as_shot_style = resolve_as_shot_style(exif.get("CreativeStyle"), input_path)
    style = normalize_style(request.get("style")) or as_shot_style
    cal = calibration_for(input_path, style) if sony_can_render(style, input_path) else None
    if cal is None and style != as_shot_style:
        # A look this file does not carry — a session restored onto a different
        # body, say. Render what the body chose rather than returning null, which
        # the client reads as "no Sony rendering" and answers by keeping a
        # profile for the wrong look on screen.
        style = as_shot_style
        cal = calibration_for(input_path, style) if sony_can_render(style, input_path) else None
    if style is None or cal is None:
        return {"colorProfile": None}
    as_shot = look_from_exif(exif)
    dro_strength = request.get("dro")
    info = look_render_info(cal, style, as_shot.merged(request.get("look")), as_shot,
                            dro=dro_from_exif(exif, input_path), borrowed=sony_is_borrowed(input_path, style),
                            dro_gain=dro_gain_table(input_path),
                            dro_strength=None if dro_strength is None else float(dro_strength),
                            dro_grid=dro_grid_cached(input_path, exif),
                            dro_level=dro_level_from_request(request),
                            sharpen=sharpness_from_exif(exif, input_path),
                            spica=spica_from_exif(exif),
                            chroma_suppres=chroma_suppres_block(input_path),
                            marble=marble_from_exif(exif),
                            # Keys the camera-match table, which is per look and
                            # so has to follow a look change through here.
                            body=camera_body_from_exif(exif))
    profile = info.to_json()
    # Which looks this particular file can offer, and which one the body chose.
    # Read from the RAW rather than from a constant so a body shipping more than
    # the ten known looks populates the picker with its own.
    profile["availableLooks"] = looks_in_file(input_path)
    profile["lookAsShotStyle"] = as_shot_style
    return {"colorProfile": profile}


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
        # `settings` is the browser's own state, so amount here is 0..100 — not
        # the 0..1 that render-linear receives (see denoisePayload in App.vue).
        # Same field name, two scales; the export path must keep reading the
        # UI state rather than the request payload or this drifts by 100x.
        # No `llr:DenoiseModel`: llr ships one denoiser, nothing on the wire
        # names a model, and the browser state has no such key to read. Writing
        # it meant emitting "" for everyone -- or a stale "wavelet" for anyone
        # whose persisted session predates the picker's removal -- while the
        # pixels were Sony's either way. Which denoiser actually ran (the
        # fallback can still pick wavelet) is known in the render path, not
        # here; it goes to stderr with the rest of the per-file notes.
        attrs["llr:DenoiseAmount"] = f"{round(_num(denoise, 'amount', 100))}"
        # Under Auto the worker derives the strength from ISO, so the amount
        # above is the slider's position rather than what was applied. Record
        # the flag so the pair is self-describing.
        if denoise.get("auto"):
            attrs["llr:DenoiseAuto"] = "1"

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
    input_path: Path, half_size: bool, max_size: int | None, denoise_model: str | None,
    denoise_tweaks: tuple[float, float] = (50.0, 50.0), denoise_strength: float = 1.0,
) -> tuple[Any, ...]:
    try:
        stat = input_path.stat()
        base = (str(input_path), bool(half_size), int(max_size or 0), stat.st_size, int(stat.st_mtime_ns))
    except OSError:
        base = (str(input_path), bool(half_size), int(max_size or 0))
    # `amount` is deliberately absent (it blends this entry with the noisy one),
    # but edge and colour change the denoised pixels themselves — for whichever
    # denoiser actually consumes them; see `_key_tweaks`.
    # The ISO strength changes the denoised pixels too (it is the engine's
    # write-back blend, applied inside the Sony denoiser), so it is keyed.
    return (*base, denoise_model or "", _key_tweaks(denoise_model, denoise_tweaks), round(float(denoise_strength), 4))


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
        style = resolve_as_shot_style(metadata.creative_style, input_path)
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
            # Always the camera's own settings, never a client override: the
            # linear cache holds this profile, and a request that overrode a
            # slider must not leave its answer behind for the next one. Overrides
            # are re-derived per request instead (sony.apply_look_overrides).
            tweaks=metadata.look, dro=metadata.dro_active, dro_gain=metadata.dro_gain,
            dro_grid=metadata.dro_grid, sharpen=metadata.sharpen,
            spica=metadata.spica, chroma_suppres=metadata.chroma_suppres,
            marble=metadata.marble,
            # The exif Model keys the camera-match table (sony/profile.py
            # camera_match_table); a body the fit never saw gets none.
            body=camera_body_from_exif({"Model": metadata.model}),
        )
        return linear, info.to_json()

    # The camera-match table is looked up regardless of the toggle, and every
    # table is deferred to the shader. Both follow from the same thing: a
    # HueSatMap no longer touches these pixels, so it cannot key the decode.
    # Switching the toggle (or the DCP style's tables) is now a uniform change.
    correction = find_camera_match(root, renderer.dcp_selection)
    linear, dcp_info = apply_dcp_profile(camera_rgb, renderer.dcp_profile, correction, defer_tables=True)
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
    denoise_tweaks: tuple[float, float] = (50.0, 50.0),
    raw_provider: Callable[[], rawpy.RawPy] | None = None,
    store_cache: bool = True,
    denoise_strength: float = 1.0,
) -> PreparedLinear:
    """Decode RAW into linear working-space RGB (cached per variant).

    `raw_provider` lets a caller that needs several variants of the same file
    (noisy + denoised) share one opened LibRaw handle instead of re-reading and
    re-unpacking the RAW per variant. The provider owns the handle's lifetime;
    it is only invoked on a cache miss. A denoise variant mutates the shared
    handle's Bayer data in place, so decode the noisy variant first.

    `store_cache=False` decodes without populating the decode caches: an export
    is one-off, so caching it would pin hundreds of MB with no reuse and evict
    the preview entries that a DCP or denoise change is about to want.
    """
    if not is_raw(input_path):
        return prepare_rendered_image(input_path, half_size=half_size, max_size=max_size)

    # Resolve the fallback before the key, not after: `_key_tweaks` decides what
    # belongs in it from the model, and the two denoisers disagree. The daemon
    # has already done this, so there it is a no-op; direct callers (the tools
    # in sony_repro/, tests) come in unresolved.
    if denoise_model:
        denoise_model = effective_model(denoise_model, input_path)

    cache_key = _raw_cache_key(input_path, half_size, max_size, denoise_model, denoise_tweaks, denoise_strength)

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
            # A Sony RAW carries the body's own measurement of how its noise
            # grows with the signal; anything else returns None and the
            # denoiser fits that shape from the pixels as before.
            curve = sony_noise_model(input_path)
            # Sony also records how much of the fine detail it puts back after
            # denoising, and its bodies put back nearly all of it — which is
            # why Edit.exe's RAW stage costs about a tenth of the fine detail
            # where ours cost most of it. Only usable alongside the curve,
            # since the halo clamp is expressed in units of its threshold.
            restore = sony_detail_restore(input_path) if curve is not None else None
            edge, chroma = denoise_tweaks
            if restore is not None:
                restore = restore.for_edge_slider(edge)
            # Sony's own filter *is* its thresholds, so a frame without the tags
            # gives it nothing to run on. `denoise_model` is already the one that
            # can run: the fallback was applied at the top of this function, off
            # this same `sony_noise_model` call (lru_cached on file identity, so
            # the two answers cannot differ). Deliberately not re-decided here —
            # the cache entry this is about to fill was keyed on the resolved
            # model, and deciding again after the key is what would store one
            # denoiser's pixels under the other's key.
            stats = denoise_raw_inplace(
                raw, get_denoiser(denoise_model), noise=curve,
                detail=None if restore is None or curve is None
                else (restore.fraction, restore.limit_in_thresholds(curve)),
                chroma_scale=chroma / 50.0, strength=denoise_strength)
            # Which of those two happened is invisible from the result, and it
            # is the one input that varies per *file* rather than per request —
            # so a frame that denoises unlike its neighbours is explained here
            # and nowhere else. The API forwards our stderr to its console.
            kept = ("" if stats is None or stats.detail_restored is None
                    else f" detail={stats.detail_restored:.2f}")
            if stats is not None and stats.strength != 1.0:
                kept += f" strength={stats.strength:.3f}"
            note = (f"{stats.model} {stats.width}x{stats.height} "
                    f"noise={stats.noise_source}{kept}" if stats is not None
                    else "skipped, sensor CFA is not 2x2 Bayer")
            sys.stderr.write(f"denoise {input_path.name}: {note}\n")
            sys.stderr.flush()
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
                    no_auto_bright=True,
                    output_color=rawpy.ColorSpace.ProPhoto,
                    **libraw_wb_kwargs(raw, metadata.wb_scale),
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
                _remember_pixels(_FALLBACK_CACHE, cache_key, (linear, metadata, color_profile), _FALLBACK_CACHE_BYTES_MAX)
        else:
            camera_rgb = decode_camera_rgb_for_render(raw, metadata, half_size, input_path)
            camera_rgb = apply_camera_crop(camera_rgb, metadata.camera_crop)
            if camera_rgb.shape[-1] != 3:
                raise ValueError("Profiled rendering currently supports only three-channel camera RGB data")
            if max_size:
                camera_rgb = downsample_linear(camera_rgb, max_size)
            # Cache camera RGB so colour-pipeline changes skip RAW re-decode
            if store_cache:
                _remember_pixels(RAW_CAMERA_CACHE, cache_key, (camera_rgb, metadata), RAW_CAMERA_CACHE_BYTES_MAX)
            linear, color_profile = render_color(renderer, camera_rgb, metadata, root, recipe)

    return PreparedLinear(linear=linear, metadata=metadata, color_profile=color_profile)


#: Which demosaic the profiled path runs on a Sony RGGB frame. "itp" is
#: Edit.exe's own (sony/itp.py, reproduced to 1 LSB); "libraw" is the AHD that
#: stood in for it, kept for A/B measurement via LLR_DEMOSAIC=libraw.
SONY_DEMOSAIC = os.environ.get("LLR_DEMOSAIC", "itp").strip().lower()


def decode_camera_rgb_for_render(
    raw: rawpy.RawPy, metadata: RawMetadata, half_size: bool, input_path: Path,
) -> np.ndarray:
    """Camera RGB for the profiled render path.

    Sony bodies get Edit.exe's demosaic: it is the stage that decides whether
    Bayer noise comes out as colour or as luminance (measured in
    notes/measured-chroma-gap.md 2.19.1 / 2.20), and it reads the mosaic the
    RAW-domain denoiser just wrote, in the engine's own order. Everything else,
    and the fallback when the sensor is not RGGB, is LibRaw exactly as before.

    Scale: the ITP planes divided by Sony's white index (8192) land within 0.1%
    of LibRaw's use_camera_wb normalisation, so the DCP / camera-match tables
    fitted on LibRaw decodes stay valid; the fitter itself
    (fit_profile.postprocess_camera_native) is deliberately left on LibRaw.
    """
    if (SONY_DEMOSAIC == "itp" and (metadata.make or "").strip().upper() == "SONY"
            and sony_itp.supports(raw)):
        sys.stderr.write(f"demosaic {input_path.name}: sony itp\n")
        return sony_itp.demosaic_rawpy(raw, half_size=half_size)
    return postprocess_camera_native(raw, half_size=half_size, wb_scale=metadata.wb_scale)


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
    # Shipped whenever the RAW holds a curve, not only when the body used it:
    # that is what lets someone dial DRO in on a frame shot without it. Whether
    # the body used it rides on dro_active, which is what sets the default.
    dro_gain = dro_gain_table(input_path)
    # Only worth the raw pass when there is a curve for it to index. Needs the
    # camera crop because the grid is addressed in sensor coordinates while the
    # render is the cropped, flipped frame.
    grid = dro_grid_json(
        dro_grid(raw, camera_crop[0] if camera_crop else None)) if dro_gain else None
    # The RAW is already open here, so this is the cheap place to fill the cache
    # the look-profile path reads from.
    _remember(_DRO_GRID_CACHE, _file_stamp(input_path), grid, _DRO_GRID_CACHE_MAX)
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
        look=look_from_exif(exif),
        sharpen=sharpness_from_exif(exif, input_path),
        spica=spica_from_exif(exif),
        chroma_suppres=chroma_suppres_block(input_path),
        marble=marble_from_exif(exif),
        dro_active=dro_from_exif(exif, input_path),
        dro_gain=dro_gain,
        dro_grid=grid,
        # Only the YCbCr frames carry the tag, and only they lack a CFA pattern;
        # a mosaic file is not worth the read.
        wb_scale=ycc_wb_scale(input_path) if getattr(raw, "raw_pattern", None) is None else None,
    )


def resolve_as_shot_style(creative_style: str | None, input_path: Path) -> str | None:
    """The look the body rendered this shot with, as a code this path can render.

    Normally the exif CreativeStyle, normalised. When that names a look the file
    does not carry — "Off", which a body writes whenever a Picture Profile is
    set — the file's own stamp decides (sony.as_shot_look): Edit opens those
    frames as Standard, and until this fell through they took the DCP path with
    an 11 dE gap to Edit's export. The exif code is kept when the file has no
    answer either, so the DCP selection downstream still sees what the body said.
    """
    style = normalize_style(creative_style)
    if style is None or sony_can_render(style, input_path):
        return style
    return sony_as_shot_look(input_path) or style


def look_from_exif(exif: dict[str, Any]) -> LookTweaks:
    """The Creative Look tweaks the body recorded for this shot, on Edit's
    panel scale — the same numbers Edit shows when it opens the file
    (sony/profile.py stops_to_panel). The three sliders the camera does not
    have (白色/黑色/色相) start at zero.

    Through merged() rather than the constructor so a tag outside what the
    engine renders (a negative Clarity, which it clamps at zero) lands inside
    it: as-shot is the panel's reset target, and a slider has to be able to
    return to it.
    """
    return NO_TWEAKS.merged(stops_to_panel(
        highlights=_exif_int(exif.get("Highlights")),
        shadows=_exif_int(exif.get("Shadows")),
        fade=_exif_int(exif.get("Fade")),
        # exiftool prints Sony's zero for these as "Normal", not "0".
        contrast=_exif_int(exif.get("Contrast")),
        saturation=_exif_int(exif.get("Saturation")),
        clarity=_exif_int(exif.get("Clarity")),
    ))


def sharpness_from_exif(exif: dict[str, Any], input_path: Path) -> dict[str, Any]:
    """The body's sharpening for this shot, as the shader needs it.

    Two MakerNotes ladders and one scale out of the RAW itself — which is why
    this sits here beside look_from_exif and dro_from_exif rather than in
    sony/sharpness.py: reading the file is the caller's job in this package,
    and the module stays a pure description of the engine's arithmetic.
    """
    return sharpness_block(
        _exif_int(exif.get("Sharpness"), SHARPNESS_DEFAULT),
        _exif_int(exif.get("SharpnessRange"), SHARPNESS_RANGE_DEFAULT),
        sharpness_calibration(input_path),
    )


def chroma_suppres_block(input_path: Path) -> dict[str, Any] | None:
    """This shot's ChromaSuppres terms in wire form, or None if it carries none.

    No exif at all: the four anchors live in the RAW's own SR2 block, and the
    engine's ISO axis reads zero on every frame measured, so the file is the
    whole input. Here rather than in sony/chromasuppres.py for the same reason
    sharpness_from_exif is here — opening files is this module's job, and the
    stage module stays a description of the engine's arithmetic.
    """
    terms = chroma_suppres_from_file(input_path)
    return terms.to_json() if terms is not None else None


def marble_from_exif(exif: dict[str, Any]) -> dict[str, Any] | None:
    """Marble's chroma cleanup for this shot: ISO (the blend amount's only
    per-shot input) plus the body's threshold calibration (sony/marble.py).
    None without an ISO, and the stage stays off rather than guess a strength."""
    iso = _exif_int(exif.get("ISO")) or None
    if not iso:
        return None
    return marble_block(int(iso))


def camera_body_from_exif(exif: dict[str, Any]) -> str | None:
    """The exif Model as the camera-match table is keyed by it ("ILCE-7CM2"),
    or None when the tag is missing — which means no table, not a guess."""
    model = exif.get("Model")
    return str(model).strip() or None if model else None


def spica_from_exif(exif: dict[str, Any]) -> dict[str, Any]:
    """The fine half of sharpening for this shot.

    The same two ladders sharpening reads, weighted the other way, plus the
    shot's ISO — and no file read at all, because unlike sharpening this stage
    has no per-shot calibration in the SR2 block. `Sharpness` and `ISO` are
    plain exif, so this one takes only the tags.
    """
    iso = _exif_int(exif.get("ISO")) or None
    return spica_block(
        _exif_int(exif.get("Sharpness"), SHARPNESS_DEFAULT),
        _exif_int(exif.get("SharpnessRange"), SHARPNESS_RANGE_DEFAULT),
        spica_iso_gain(iso), spica_gain_scale(iso), spica_range_shift(iso),
    )


def _exif_switch_on(value: Any) -> bool:
    """An exif on/off switch, read as on unless it says otherwise.

    Absent reads as off: a body that never wrote the tag never applied the
    thing it gates. The values are not a clean enum across tags, so this can
    only test for the off states by name.
    """
    return str(value or "Off").strip().lower() not in ("off", "none")


# Below this the curve moves the darkest tones by under a percent, which is not
# something the render owes the user a warning about. The 22 Auto frames
# measured split cleanly around it: three sat at 0.001 or less, the rest at
# 0.029 and above.
DRO_VISIBLE_STOPS = 0.01


def dro_level_from_request(request: dict[str, Any]) -> int:
    """The manual DRO level a request asked for, or Auto when it asked for none.

    Auto is -1 rather than absent because that is the engine's own encoding, and
    because a level of 0 is a real, weak setting that must not read as "unset".
    """
    value = request.get("droLevel")
    if value is None:
        return DRO_LEVEL_AUTO
    try:
        return max(DRO_LEVEL_AUTO, min(DRO_LEVEL_MAX, int(value)))
    except (TypeError, ValueError):
        return DRO_LEVEL_AUTO


def dro_from_exif(exif: dict[str, Any], input_path: Path | None = None) -> bool:
    """Whether DRO actually shaped this shot, not merely whether it was armed.

    Both questions have an answer in the file and they disagree. The exif switch
    only says which mode the body was in; the curve the body then chose is
    written into the RAW, and on three of 22 Auto frames measured it came out
    flat — the stage ran and changed nothing. Reporting those as "DRO applied,
    not reproduced" would be a warning about an effect that is not there.

    Falls back to the exif switch alone when the RAW carries no curve, which is
    where the engine falls back to a built-in preset (PIPELINE.md 7.10.2).
    """
    if not _exif_switch_on(exif.get("DynamicRangeOptimizer")):
        return False
    if input_path is None:
        return True
    try:
        strength = dro_strength(input_path)
    except Exception:
        return True
    return True if strength is None else strength >= DRO_VISIBLE_STOPS


def _exif_int(value: Any, default: int = 0) -> int:
    """One signed exif integer, `default` when absent or unparseable.

    exiftool renders these as "+1" / "-6" strings, which int() handles, but a
    body that doesn't write the tag at all is the common case. For the look
    tweaks that means no tweak was applied, hence a default of 0; sharpening
    has no "off" position, so it passes the camera's own default instead.
    """
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


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


#: Sony writes 16 lens-correction knots; knot i sits at radius i / 15.2 of the
#: frame's half-diagonal, the first at the centre (measured — see
#: sony_lens_corrections). The web canonical grid (lens.ts) is the same one.
SONY_KNOT_SPAN = 15.2


def sony_lens_corrections(exif: dict[str, Any]) -> dict[str, Any] | None:
    """Map Sony's per-shot correction splines (SubIFD DistortionCorrParams /
    VignettingCorrParams / ChromaticAberrationCorrParams) to vendor-neutral
    factor tables. Each array is `nc` followed by nc int16 knot values (CA: 2*nc,
    R then B), knots evenly spaced in radius normalised to the frame's
    half-diagonal, the first at the centre: knots[i] = i / SONY_KNOT_SPAN.
    Fixed-point scales are the community-documented ones (exiftool /
    darktable's reverse engineering): distortion sampling factor p*2^-14 + 1
    (corrected position -> distorted source position), vignetting gain
    1 / 2^(0.5 - 2^(p*2^-13 - 1)), lateral CA per-channel radial factor
    p*2^-21 + 1.

    The knot spacing is measured, not documented. Neither `(i + 0.5) / nc`
    (what this read before) nor darktable's `(i + 0.5) / (nc - 1)` matches the
    camera: warp an uncorrected decode with either, block-match it against the
    in-camera JPEG, and the radial residual peaks at 1.3-2.5 px around r = 0.5
    and changes sign toward the corner — the shape of a grid that is too fine,
    not of a wrong scale (a global fill-scale fit absorbs most of it, which is
    how the earlier grid passed). Sweeping the spacing on 8 frames of the
    FE 50-150mm F2 GM at 50..150 mm, barrel and pincushion, the residual
    collapses to <= 0.3 px rms (the block-matching noise floor) at i / 15.2
    with the first knot at the centre, and grows again either side (15.0:
    0.3-0.8 px, 15.4: 0.5-0.8 px). With 16 knots the last one sits at
    r = 0.987; the corner, and for barrel the fill scale's reach past it,
    extrapolate the last segment. The 2^-14 scale is not degenerate with the
    spacing: scaling the amplitude instead makes every grid worse.

    Each correction has its own switch, and the parameters are written whether
    or not the body used them — so reading them is not evidence that anything
    was corrected. On a Tamron 28-200 with distortion correction off, the
    in-camera JPEG measures at zero radial displacement while the tags still
    describe a 1.3% correction; applying it warps a frame the camera never
    warped. A switched-off correction comes back as an identity table rather
    than being dropped, so the other two survive independently.

    The vignetting scale is the one piece here that is NOT confirmed against the
    camera, and its switch does not settle it either — the FE 50-150mm writes
    "Unknown (3)" rather than a name. Measured in scene-linear (tone curve
    inverted out, on frames at Fade 0 with DRO off), the in-camera JPEG needs no
    vignetting gain at all whatever the switch says: the ratio against an
    uncorrected decode is flat to within 2% out to the corner on five frames
    across two lenses, while applying this table overshoots by 40-47%. The RAW
    itself still falls off (one Bayer green at 27% of centre at the corner), so
    the body is not pre-correcting — it simply leaves these parameters to
    downstream software. Kept as-is because it is a user-facing slider rather
    than part of reproducing the camera.

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

    def gated(tag: str, values: list[float]) -> list[float]:
        """`values` if the body's switch for this correction is on, else identity."""
        return values if _exif_switch_on(exif.get(tag)) else [1.0] * nc

    out: dict[str, Any] = {
        "knots": [i / SONY_KNOT_SPAN for i in range(nc)],
        "distortion": gated("DistortionCorrection",
                            [dist[i + 1] * 2**-14 + 1 for i in range(nc)]),
        "vignetting": gated("VignettingCorrection",
                            [1 / 2 ** (0.5 - 2 ** (vig[i + 1] * 2**-13 - 1)) for i in range(nc)]),
    }
    if (ca and ca[0] == 2 * nc and len(ca) >= 2 * nc + 1
            and _exif_switch_on(exif.get("ChromaticAberrationCorrection"))):
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
                # Each correction's own switch. The parameters are written even
                # when the body applied nothing (see sony_lens_corrections).
                "-DistortionCorrection",
                "-VignettingCorrection",
                "-ChromaticAberrationCorrection",
                "-ChromaticAberrationCorrParams",
                # The in-camera Creative Look tweaks. Group-qualified on
                # purpose: Sony writes these alongside ExifIFD tags of the same
                # bare name, and an unqualified request can pick the wrong one.
                "-Sony:Highlights",
                "-Sony:Shadows",
                "-Sony:Fade",
                "-Sony:Contrast",
                "-Sony:Saturation",
                "-Sony:Clarity",
                # Sharpening is a camera setting rather than a Creative Look
                # tweak, but it needs the same qualifier for the same reason:
                # ExifIFD carries its own Sharpness ("Normal"), and an
                # unqualified request returns that one instead of the ladder.
                "-Sony:Sharpness",
                "-Sony:SharpnessRange",
                # Spica's detail gain rolls off with sensitivity (spica.py), and
                # without this it never saw an ISO at all — so the roll-off never
                # happened and high-ISO frames got the full fine-detail boost
                # applied straight to their noise.
                "-ISO",
                # DRO is a whole stage (ZcTaskVatr) that this pipeline does not
                # reproduce, and it runs only when the shot asked for it.
                # Knowing which shots those are is the difference between a
                # limitation and a lie — but this tag only gets us half way, so
                # dro_from_exif goes on to read the curve out of the RAW.
                #
                # Sony writes two tags under this one name, 0xb025 (the mode)
                # and 0xb04f, and both land in Sony:Camera — so unlike the
                # Highlights/Shadows pair above, a group qualifier cannot
                # separate them. Without -a exiftool returns the first, which is
                # the mode, and that is the one wanted here.
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
        "DistortionCorrection",
        "VignettingCorrection",
        "ChromaticAberrationCorrection",
        "Highlights",
        "Shadows",
        "Fade",
        "Contrast",
        "Saturation",
        "Clarity",
        "Sharpness",
        "SharpnessRange",
        "ISO",
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
