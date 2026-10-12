"""Generative erase: brushed-out regions refilled by Moebius, as a patch on the decode.

The patch lives on the scene-linear frame the worker hands the browser, so every
edit the shader runs afterwards — exposure, WB, curves, masks — reaches the
filled pixels exactly as it reaches their neighbours, and the browser's pipeline
stays the single source of truth for what a pixel looks like. The model itself
wants an ordinary display-referred picture, so each region goes out through an
invertible tone map (exposure normalised on the frame, a Reinhard-style
shoulder, the sRGB curve), is filled, and comes back through its inverse. Only
the filled pixels make that round trip; the rest of the frame is never touched.

Model: Moebius (hustvl, ECCV'26, Apache-2.0), a 0.22B latent inpainting UNet,
exported to ONNX as three graphs (`scripts/export_moebius_onnx.py`): the UNet
with classifier-free guidance and its fixed conditioning baked in, and the SD
VAE's encoder and decoder. Sampling is DDIM in numpy, started from pure noise:
the reference pipeline's default strength of 0.99 noises the *original* latent,
which leaks the object being removed back into the fill as a ghost.

Strokes arrive in source-normalised coordinates (y down, the decode's own
orientation; the browser maps its view through crop, orientation and lens
correction before sending them), each a polyline with a radius in units of the
frame's long edge; a stroke may carry further polylines (`more`) that paint the
same region, so one fill can be brushed in several goes. Each stroke is one
fill, applied in order on the frame the
previous ones produced, and cached by everything it depends on: the base decode
(minus its size — preview and export share a fill) and the strokes up to it.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from .analysis import resize as _resize
from .dcp import PROPHOTO_TO_XYZ_D50, srgb_decode_float, srgb_encode_float
from .imported import SRGB_TO_XYZ_D50
from .model_fetch import Fetcher, ModelFile, cache_root
from .model_fetch import status as fetch_status

MODEL_SIZE = 512
LATENT = MODEL_SIZE // 8
MODEL_FILES = ("moebius_unet.onnx", "vae_encoder.onnx", "vae_decoder.onnx")
# Where a machine without the graphs fetches them: the export of
# scripts/export_moebius_onnx.py, pinned to one revision and checked against its
# sizes and LFS hashes, so a download is exactly the files this code was run on.
MODEL_REPO = "Jannchie/moebius-inpaint-onnx"
MODEL_REVISION = "17fe1ea303f476c39a754f51416aaac717e4c501"
MODEL_SOURCES = (
    ModelFile("moebius_unet.onnx", MODEL_REPO, MODEL_REVISION, "moebius_unet.onnx", 905039644,
              "f411ebd5c3f678ff25a09b0d4575adc670e24706b261967537bcf93467c4da21"),
    ModelFile("vae_encoder.onnx", MODEL_REPO, MODEL_REVISION, "vae_encoder.onnx", 136757861,
              "83d1cee820edced626a659603cb76a5fca4fbf7bd6cd8616651925c3088fffc5"),
    ModelFile("vae_decoder.onnx", MODEL_REPO, MODEL_REVISION, "vae_decoder.onnx", 198069190,
              "114b632791dbf44f4c408a200ff40fb97da2c4d3d7a76011b7083888604d9721"),
)
STEPS = 20
GUIDANCE = 2.0

# Limits on what a request may ask for; the API clamps to the same numbers.
MAX_STROKES = 64
MAX_POINTS = 4096
MAX_LINES = 32  # polylines in one stroke: the first plus `more`

# How much of the frame around a stroke the model gets to see, as a fraction of
# the stroke's own extent on each side. Less starves it of context to continue;
# more spends the 512 px on scenery and blurs the fill.
CONTEXT = 0.6
# The smallest region worth a fill, as a fraction of the frame's long edge: a
# tiny dab still needs enough surroundings to read the texture it sits in.
MIN_REGION = 0.05

_PROPHOTO_TO_SRGB = (np.linalg.inv(SRGB_TO_XYZ_D50.astype(np.float64)) @ PROPHOTO_TO_XYZ_D50.astype(np.float64))
_SRGB_TO_PROPHOTO = np.linalg.inv(_PROPHOTO_TO_SRGB)
_PROPHOTO_Y = PROPHOTO_TO_XYZ_D50[1].astype(np.float64)
# Reinhard shoulder white point: scene values up to this many times mid-grey's
# exposure stay distinguishable instead of clipping, and the curve stays
# invertible everywhere below it.
_SHOULDER_WHITE = 16.0


def model_dir() -> Path:
    """Where the three ONNX graphs live: $LLR_ERASE_MODEL_DIR, else beside the catalog."""
    explicit = os.environ.get("LLR_ERASE_MODEL_DIR")
    return Path(explicit) if explicit else cache_root() / "models" / "moebius"


FETCHER = Fetcher("erase", model_dir, MODEL_SOURCES)


Line = tuple[tuple[tuple[float, float], ...], float]  # polyline, radius


@dataclass(frozen=True)
class Stroke:
    points: tuple[tuple[float, float], ...]
    radius: float  # in units of the frame's long edge
    # "fill": the generative model; "heal": copied from a matching patch nearby
    # and blended to its surroundings (make_heal_patch) — no model involved.
    mode: str = "fill"
    # Further polylines painting the same region, each with its own radius.
    more: tuple[Line, ...] = ()

    def lines(self) -> tuple[Line, ...]:
        return ((self.points, self.radius), *self.more)

    def extent(self, width: int, height: int) -> tuple[float, float, float, float]:
        """The px box (x0, y0, x1, y1) every line covers, brush included."""
        long_edge = max(width, height)
        x0 = y0 = np.inf
        x1 = y1 = -np.inf
        for pts, radius in self.lines():
            r = radius * long_edge
            x0 = min(x0, min(u for u, _ in pts) * width - r)
            x1 = max(x1, max(u for u, _ in pts) * width + r)
            y0 = min(y0, min(v for _, v in pts) * height - r)
            y1 = max(y1, max(v for _, v in pts) * height + r)
        return x0, y0, x1, y1

    def key(self) -> str:
        # A fill's key is what it always was, so existing fills stay cached.
        pts = [[round(x, 5), round(y, 5)] for x, y in self.points] + [round(self.radius, 5)]
        if self.more:
            pts.append([[[round(x, 5), round(y, 5)] for x, y in line] + [round(r, 5)] for line, r in self.more])
        return json.dumps([*pts, "heal"] if self.mode == "heal" else pts)


def _parse_line(points: Any, radius: Any) -> Line | None:
    if not isinstance(radius, (int, float)) or not isinstance(points, list) or not points:
        return None
    pts: list[tuple[float, float]] = []
    for p in points[:MAX_POINTS]:
        if (isinstance(p, (list, tuple)) and len(p) == 2
                and all(isinstance(v, (int, float)) and np.isfinite(v) for v in p)):
            pts.append((float(p[0]), float(p[1])))
    r = float(radius)
    return (tuple(pts), r) if pts and np.isfinite(r) and 0.0 < r <= 0.5 else None


def parse_strokes(raw: Any) -> list[Stroke]:
    """The request's `erase` field, validated; anything malformed is dropped, not guessed."""
    if not isinstance(raw, list):
        return []
    out: list[Stroke] = []
    for item in raw[:MAX_STROKES]:
        if not isinstance(item, dict):
            continue
        first = _parse_line(item.get("points"), item.get("radius"))
        if first is None:
            continue
        raw_more = item.get("more")
        more: list[Line] = []
        for extra in (raw_more if isinstance(raw_more, list) else [])[:MAX_LINES - 1]:
            line = _parse_line(extra.get("points"), extra.get("radius")) if isinstance(extra, dict) else None
            if line is not None:
                more.append(line)
        out.append(Stroke(first[0], first[1], "heal" if item.get("mode") == "heal" else "fill", tuple(more)))
    return out


# ── Tone map: scene-linear ProPhoto <-> display sRGB the model can read ──


def exposure_scale(linear: np.ndarray) -> float:
    """Gain putting the frame's median luminance on mid-grey, from a sparse sample."""
    step = max(1, int(np.sqrt(linear.shape[0] * linear.shape[1] / 65536)))
    y = linear[::step, ::step] @ _PROPHOTO_Y
    med = float(np.median(y))
    return float(np.clip(0.18 / med, 1 / 64, 64)) if med > 0 else 1.0


def to_display(linear: np.ndarray, k: float) -> np.ndarray:
    v = np.clip((linear.astype(np.float64) @ _PROPHOTO_TO_SRGB.T) * k, 0, None)
    w = _SHOULDER_WHITE
    t = v / (1 + v) * (1 + w) / w
    return srgb_encode_float(np.clip(t, 0, 1)).astype(np.float32)


def from_display(display: np.ndarray, k: float) -> np.ndarray:
    w = _SHOULDER_WHITE
    t = np.clip(srgb_decode_float(display.astype(np.float64)) * w / (1 + w), 0, 0.9999)
    v = t / (1 - t) / k
    return np.clip(v @ _SRGB_TO_PROPHOTO.T, 0, None).astype(np.float32)


# ── Model ──


class _Engine:
    """The three ONNX sessions, loaded once and run one fill at a time."""

    def __init__(self, folder: Path) -> None:
        from .gpu_host import make_session  # onnxruntime is optional at import time

        self.sessions = {name: make_session(folder / f"{name}.onnx")
                         for name in ("moebius_unet", "vae_encoder", "vae_decoder")}
        self.provider = self.sessions["moebius_unet"].get_providers()[0]
        betas = np.linspace(0.00085 ** 0.5, 0.012 ** 0.5, 1000, dtype=np.float64) ** 2
        self.alphas_cumprod = np.cumprod(1 - betas)

    def fill(self, image: np.ndarray, mask: np.ndarray, seed: int) -> np.ndarray:
        """image: 512x512x3 display RGB in [0, 1]; mask: 512x512 bool, True = fill. Returns the decode, [0, 1]."""
        s = self.sessions
        x = image.astype(np.float32) * 2 - 1
        masked = x * (1 - mask[..., None].astype(np.float32))
        lat_masked = s["vae_encoder"].run(None, {"image": masked.transpose(2, 0, 1)[None]})[0]
        # Nearest-neighbour to the latent grid, as the reference pipeline's F.interpolate does.
        m_lat = mask.reshape(LATENT, 8, LATENT, 8)[:, 0, :, 0][None, None].astype(np.float32)
        z = np.random.default_rng(seed).standard_normal((1, 4, LATENT, LATENT)).astype(np.float32)
        stride = 1000 // STEPS
        ac = self.alphas_cumprod
        for t in range(1000 - stride, -1, -stride):  # DDIM "leading" spacing, eta 0
            eps = s["moebius_unet"].run(None, {
                "x": np.concatenate([z, m_lat, lat_masked], 1),
                "t": np.array([t], np.int64),
                "guidance": np.array(GUIDANCE, np.float32),
            })[0]
            a, prev = ac[t], t - stride
            ap = ac[prev] if prev >= 0 else 1.0
            x0 = (z - np.sqrt(1 - a) * eps) / np.sqrt(a)
            z = (np.sqrt(ap) * x0 + np.sqrt(1 - ap) * eps).astype(np.float32)
        return s["vae_decoder"].run(None, {"latent": z})[0][0].transpose(1, 2, 0)


# The worker's handle on the model: it runs in the GPU host (gpu_host.py), which
# gives its memory back when idle — sessions here would pin it for good.
_ENGINE: Any = None
_ENGINE_ERROR: str | None = None
_ENGINE_LOCK = threading.Lock()


def _engine() -> Any:
    global _ENGINE, _ENGINE_ERROR
    with _ENGINE_LOCK:
        if _ENGINE is None:
            missing = FETCHER.missing()
            if missing:
                _ENGINE_ERROR = f"model files missing in {FETCHER.folder}: {', '.join(f.name for f in missing)}"
                raise RuntimeError(_ENGINE_ERROR)
            try:
                from .gpu_host import Remote

                _ENGINE = Remote("moebius")
            except Exception as error:
                _ENGINE_ERROR = f"could not load the erase model: {error}"
                raise RuntimeError(_ENGINE_ERROR) from error
            _ENGINE_ERROR = None
            sys.stderr.write(f"erase: Moebius loaded on {_ENGINE.provider}\n")
        return _ENGINE


def status() -> dict[str, Any]:
    """Whether erase can run here, without loading anything heavy."""
    return fetch_status(FETCHER, _ENGINE.provider if _ENGINE is not None else None, _ENGINE_ERROR)


def start_download() -> dict[str, Any]:
    """Fetch whichever graphs are missing, in the background; status() reports progress."""
    FETCHER.start()
    return status()


# ── Geometry ──


def stroke_mask(stroke: Stroke, width: int, height: int, box: tuple[int, int, int, int]) -> np.ndarray:
    """The stroke rasterised over `box` (x0, y0, x1, y1 in px of a width x height frame)."""
    x0, y0, x1, y1 = box
    img = Image.new("L", (x1 - x0, y1 - y0), 0)
    draw = ImageDraw.Draw(img)
    for line, radius in stroke.lines():
        r = radius * max(width, height)
        pts = [(u * width - x0, v * height - y0) for u, v in line]
        if len(pts) > 1:
            draw.line(pts, fill=255, width=max(1, round(2 * r)), joint="curve")
        for x, y in pts:
            draw.ellipse((x - r, y - r, x + r, y + r), fill=255)
    return np.asarray(img) > 127


def region_for(stroke: Stroke, width: int, height: int) -> tuple[int, int, int, int]:
    """A square-as-possible box around the stroke with context, inside the frame."""
    bx0, by0, bx1, by1 = stroke.extent(width, height)
    side = max(bx1 - bx0, by1 - by0) * (1 + 2 * CONTEXT)
    side = max(side, MIN_REGION * max(width, height))
    cx, cy = (bx0 + bx1) / 2, (by0 + by1) / 2
    w = min(side, width)
    h = min(side, height)
    x0 = int(np.clip(round(cx - w / 2), 0, width - round(w)))
    y0 = int(np.clip(round(cy - h / 2), 0, height - round(h)))
    return x0, y0, x0 + round(w), y0 + round(h)


def _square(arr: np.ndarray) -> tuple[np.ndarray, tuple[int, int]]:
    """Pad to square by reflection (Moebius only takes square input); returns the offsets."""
    h, w = arr.shape[:2]
    n = max(h, w)
    py, px = (n - h) // 2, (n - w) // 2
    pad = ((py, n - h - py), (px, n - w - px)) + ((0, 0),) * (arr.ndim - 2)
    return np.pad(arr, pad, mode="reflect" if min(h, w) > 1 else "edge"), (py, px)


# ── Fills, cached ──


@dataclass
class Patch:
    """One stroke's fill, resolution-free: a box in normalised frame coordinates and its pixels."""
    box: tuple[float, float, float, float]  # x0, y0, x1, y1 in [0, 1]
    linear: np.ndarray   # h x w x 3 scene-linear ProPhoto, at the resolution it was made
    alpha: np.ndarray    # h x w, feathered coverage
    source_side: int     # long edge of the frame it was made on, when that limited it; else 0


_PATCHES: OrderedDict[str, Patch] = OrderedDict()
_PATCHES_MAX = 48
_PATCH_LOCK = threading.Lock()


def _patch_path(folder: Path, digest: str) -> Path:
    return folder / f"erase-{digest}.npz"


def _load_patch(path: Path) -> Patch | None:
    try:
        with np.load(path) as z:
            b = [float(v) for v in z["box"]]
            return Patch((b[0], b[1], b[2], b[3]), z["linear"], z["alpha"], int(z["side"]))
    except (OSError, ValueError, KeyError):
        return None


def _save_patch(path: Path, p: Patch) -> None:
    tmp = path.with_suffix(".tmp.npz")
    try:
        np.savez(tmp, box=np.array(p.box), linear=p.linear.astype(np.float16), alpha=p.alpha.astype(np.float16),
                 side=np.array(p.source_side))
        os.replace(tmp, path)
    except OSError:
        tmp.unlink(missing_ok=True)


def make_patch(frame: np.ndarray, stroke: Stroke, k: float, seed: int, engine: Any) -> Patch:
    """Fill one stroke on `frame` (H x W x 3 linear). `engine.fill` is the model call."""
    height, width = frame.shape[:2]
    box = region_for(stroke, width, height)
    x0, y0, x1, y1 = box
    rw, rh = x1 - x0, y1 - y0
    # Nothing below needs more than the model's resolution, so the region is
    # area-averaged down first — in scene light, which is where averaging is
    # right — and the tone map, padding and resampling touch 512 px a side
    # rather than megapixels. Upsampling the fill back past 512 later would add
    # bytes, not detail, so the patch stays at this size too.
    f = min(1.0, MODEL_SIZE / max(rw, rh))
    sw, sh = max(1, round(rw * f)), max(1, round(rh * f))
    mask = stroke_mask(stroke, width, height, box).astype(np.float32)
    region = frame[y0:y1, x0:x1]
    if f < 1:
        region = _resize(region, (sw, sh), Image.Resampling.BOX)
        mask = _resize(mask, (sw, sh), Image.Resampling.BOX)
    sq_img, (py, px) = _square(to_display(region, k))
    sq_mask, _ = _square(mask)
    n = sq_img.shape[0]
    img512 = sq_img if n == MODEL_SIZE else _resize(sq_img, (MODEL_SIZE, MODEL_SIZE), Image.Resampling.LANCZOS)
    img512 = np.clip(img512, 0, 1)
    # Grown a little at model scale: the brush edge sits on the object's own
    # anti-aliased rim, which would otherwise survive as a halo the model copies.
    m512 = _resize(sq_mask, (MODEL_SIZE, MODEL_SIZE), Image.Resampling.BILINEAR) > 0.25
    m512 = np.asarray(Image.fromarray(m512.astype(np.uint8) * 255).filter(ImageFilter.MaxFilter(5))) > 127
    out512 = engine.fill(img512, m512, seed)
    filled = out512 if n == MODEL_SIZE else _resize(out512, (n, n), Image.Resampling.LANCZOS)
    alpha = _resize(m512.astype(np.float32), (n, n), Image.Resampling.BILINEAR)
    alpha = np.asarray(Image.fromarray((np.clip(alpha, 0, 1) * 255).astype(np.uint8)).filter(
        ImageFilter.GaussianBlur(max(1.0, n / 256)))).astype(np.float32) / 255
    filled = filled[py:py + sh, px:px + sw]
    alpha = alpha[py:py + sh, px:px + sw]
    # Keep only the part the fill actually covers: the context around it was
    # for the model to read, and blending it back would cost time for nothing.
    ys, xs = np.nonzero(alpha > 1 / 255)
    if ys.size:
        ty0, ty1, tx0, tx1 = int(ys.min()), int(ys.max()) + 1, int(xs.min()), int(xs.max()) + 1
    else:
        ty0, ty1, tx0, tx1 = 0, sh, 0, sw
    filled, alpha = filled[ty0:ty1, tx0:tx1], alpha[ty0:ty1, tx0:tx1]
    sx, sy = rw / sw, rh / sh
    norm_box = ((x0 + tx0 * sx) / width, (y0 + ty0 * sy) / height, (x0 + tx1 * sx) / width, (y0 + ty1 * sy) / height)
    # A region smaller than the model's input was filled at the frame's own
    # resolution, so a larger frame later could give it more pixels: remember
    # how large this one was. Otherwise 0 — 512 px a side is all the fill has.
    limited = max(width, height) if f == 1 and max(rw, rh) < MODEL_SIZE else 0
    return Patch(norm_box, from_display(np.clip(filled, 0, 1), k), alpha, limited)


# ── Heal: a nearby patch, matched and blended — no model ──

# Largest working size of a heal region; a bigger one is healed at this size
# and scaled back (the result is smooth where it differs from the source).
HEAL_MAX = 1024


def _push_pull(values: np.ndarray, known: np.ndarray) -> np.ndarray:
    """Fill where `known` is 0 from where it is 1: a pyramid of weighted means (membrane-like)."""
    h, w = known.shape
    if min(h, w) <= 2 or known.sum() == 0:
        m = (values * known[..., None]).sum((0, 1)) / max(known.sum(), 1e-6)
        return np.where(known[..., None] > 0, values, m)
    ph, pw = h + (h & 1), w + (w & 1)
    v = np.pad(values * known[..., None], ((0, ph - h), (0, pw - w), (0, 0)))
    k = np.pad(known, ((0, ph - h), (0, pw - w)))
    vs = v.reshape(ph // 2, 2, pw // 2, 2, -1).sum((1, 3))
    ks = k.reshape(ph // 2, 2, pw // 2, 2).sum((1, 3))
    coarse = vs / np.maximum(ks, 1e-6)[..., None]
    filled = _push_pull(coarse, (ks > 0).astype(np.float32))
    up = np.asarray(_resize(filled, (pw, ph), Image.Resampling.BILINEAR))[:h, :w]
    return np.where(known[..., None] > 0, values, up)


def make_heal_patch(frame: np.ndarray, stroke: Stroke) -> Patch:
    """Heal one stroke on `frame` (H x W x 3 linear) from the best-matching nearby patch.

    The healing brush, done the classic way: candidate offsets around the
    stroke are scored on the ring just outside it (log-space SSD, so a
    match holds across brightness), the winner is copied in, and the
    difference between the copy and its new surroundings is spread across
    the hole as a smooth membrane (push-pull) — the copy keeps its texture,
    the surroundings set its level.
    """
    height, width = frame.shape[:2]
    r = max(radius for _, radius in stroke.lines()) * max(width, height)
    ex0, ey0, ex1, ey1 = stroke.extent(width, height)   # brush included, so the margin is the rest
    margin = 0.5 * r + 4
    bx0, by0 = max(0, int(ex0 - margin)), max(0, int(ey0 - margin))
    bx1, by1 = min(width, int(ex1 + margin) + 1), min(height, int(ey1 + margin) + 1)
    box = (bx0, by0, bx1, by1)
    rw, rh = bx1 - bx0, by1 - by0
    f = min(1.0, HEAL_MAX / max(rw, rh))

    def region(x0: int, y0: int) -> np.ndarray:
        reg = frame[y0:y0 + rh, x0:x0 + rw].astype(np.float32)
        return reg if f == 1 else _resize(reg, (max(1, round(rw * f)), max(1, round(rh * f))), Image.Resampling.BOX)

    mask = stroke_mask(stroke, width, height, box).astype(np.float32)
    target = region(bx0, by0)
    sh, sw = target.shape[:2]
    if f < 1:
        mask = _resize(mask, (sw, sh), Image.Resampling.BOX)
    hole = mask > 0.05
    grow = max(2, round(0.6 * r * f))
    ring = (np.asarray(Image.fromarray(hole.astype(np.uint8) * 255).filter(ImageFilter.MaxFilter(2 * min(grow, 25) + 1))) > 127) & ~hole
    log_t = np.log(np.maximum(target, 1e-5))

    best, best_cost = None, np.inf
    radii = [2.2 * r, 3.0 * r, 4.2 * r, 6.0 * r]
    for dist in radii:
        for a in np.linspace(0, 2 * np.pi, 16, endpoint=False):
            dx, dy = round(dist * np.cos(a)), round(dist * np.sin(a))
            x0, y0 = bx0 + dx, by0 + dy
            if x0 < 0 or y0 < 0 or x0 + rw > width or y0 + rh > height:
                continue
            # The source must not itself be the stroke: its own hole, shifted, is excluded.
            sdx, sdy = round(dx * f), round(dy * f)
            shifted = np.zeros_like(hole)
            ys0, ys1 = max(0, -sdy), min(sh, sh - sdy)
            xs0, xs1 = max(0, -sdx), min(sw, sw - sdx)
            if ys1 > ys0 and xs1 > xs0:
                shifted[ys0:ys1, xs0:xs1] = hole[ys0 + sdy:ys1 + sdy, xs0 + sdx:xs1 + sdx]
            if (shifted & hole).any():
                continue
            src = region(x0, y0)
            cost = float(np.mean((np.log(np.maximum(src[ring], 1e-5)) - log_t[ring]) ** 2)) if ring.any() else 0.0
            if cost < best_cost:
                best, best_cost = src, cost
    if best is None:   # nowhere to copy from (a stroke filling the frame): diffuse instead
        best = _push_pull(target, (~hole).astype(np.float32))
    # The membrane: log(target / source) on the known pixels, filled smoothly into the hole.
    diff = log_t - np.log(np.maximum(best, 1e-5))
    membrane = _push_pull(diff, (~hole).astype(np.float32))
    for _ in range(2):   # smooth the coarse levels' blockiness inside the hole
        membrane = np.where(hole[..., None], _blur3(membrane), membrane)
    healed = best * np.exp(np.clip(membrane, -6, 6))
    alpha = np.asarray(Image.fromarray((np.clip(mask, 0, 1) * 255).astype(np.uint8)).filter(
        ImageFilter.GaussianBlur(max(1.0, 0.15 * r * f)))).astype(np.float32) / 255
    alpha = np.maximum(alpha, mask)
    norm_box = (bx0 / width, by0 / height, bx1 / width, by1 / height)
    return Patch(norm_box, np.clip(healed, 0, None).astype(np.float32), alpha, 0)


def _blur3(a: np.ndarray) -> np.ndarray:
    """A 3x3 box blur per channel, edges clamped."""
    p = np.pad(a, ((1, 1), (1, 1), (0, 0)), mode="edge")
    h, w = a.shape[:2]
    return sum(p[y:y + h, x:x + w] for y in range(3) for x in range(3)) / 9.0


def apply_patch(frame: np.ndarray, p: Patch) -> None:
    """Blend a patch into `frame` in place, at the frame's resolution."""
    height, width = frame.shape[:2]
    x0, y0 = round(p.box[0] * width), round(p.box[1] * height)
    x1, y1 = max(x0 + 1, round(p.box[2] * width)), max(y0 + 1, round(p.box[3] * height))
    size = (x1 - x0, y1 - y0)
    lin = _resize(p.linear.astype(np.float32), size, Image.Resampling.BICUBIC)
    a = np.clip(_resize(p.alpha.astype(np.float32), size, Image.Resampling.BILINEAR), 0, 1)[..., None]
    region = frame[y0:y1, x0:x1]
    region[...] = region * (1 - a) + np.clip(lin, 0, None) * a


def apply_erase(frame: np.ndarray, strokes: list[Stroke], base_key: Any, cache_dir: Path | None,
                engine: Any = None, inplace: bool = False) -> np.ndarray:
    """`frame` with every stroke filled in order. Returns a new array and leaves
    the input alone, unless `inplace` (a float32 frame the caller owns).

    `base_key` names the decode the fills are made on, minus its size: a fill
    is the same picture whether it lands on a preview or an export.
    """
    if not strokes:
        return frame
    t0 = time.perf_counter()
    made = 0
    out = frame if inplace and frame.dtype == np.float32 else np.array(frame, dtype=np.float32, copy=True)
    k = exposure_scale(out)
    chain = hashlib.sha1(repr(base_key).encode())
    for stroke in strokes:
        chain.update(stroke.key().encode())
        digest = chain.hexdigest()[:24]
        with _PATCH_LOCK:
            patch = _PATCHES.get(digest)
            if patch is not None:
                _PATCHES.move_to_end(digest)
        if patch is None and cache_dir is not None:
            patch = _load_patch(_patch_path(cache_dir, digest))
        # A fill made on a small preview is upscaled onto a bigger frame; once a
        # frame offers clearly more pixels than it was made from (and the model
        # could have used them), make it again rather than keep the soft one.
        long_edge = max(out.shape[:2])
        if patch is None or (patch.source_side and long_edge > patch.source_side * 1.25):
            seed = int(digest[:8], 16)
            patch = (make_heal_patch(out, stroke) if stroke.mode == "heal"
                     else make_patch(out, stroke, k, seed, engine or _engine()))
            made += 1
            if cache_dir is not None:
                _save_patch(_patch_path(cache_dir, digest), patch)
        with _PATCH_LOCK:
            _PATCHES[digest] = patch
            _PATCHES.move_to_end(digest)
            while len(_PATCHES) > _PATCHES_MAX:
                _PATCHES.popitem(last=False)
        apply_patch(out, patch)
    sys.stderr.write(f"erase: {len(strokes)} strokes ({made} filled) in {time.perf_counter() - t0:.2f}s\n")
    return out
