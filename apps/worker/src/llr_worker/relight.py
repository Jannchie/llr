"""Relight geometry: per-pixel camera-space normals and depth for the shader's added lights.

The lights themselves are the browser's (passes.ts, the relight block): they are
added to the scene-linear frame in the shader, so a light dragged across the
picture is a redraw, and every later edit — exposure, WB, curves, masks — treats
the light the way it treats the rest of the scene. What the shader cannot know
is the shape of the scene, which is this module's one job: a monocular geometry
estimate, made once per decode and handed over as a small texture.

Model: MoGe-2 ViT-S with the normal head (Microsoft, MIT; Ruicheng/moge-2-vits-
normal-onnx), 35M parameters. Its raw ONNX outputs are an affine-invariant point
map (depth known up to scale *and* shift), unit normals and a validity mask; the
PyTorch `infer()` that turns the point map into a camera is not part of the
graph, so the focal length and shift are recovered here (`recover_focal_shift`,
the same least-squares problem MoGe solves) and the metric scale applied.

Output, in the decode's own frame (y down): an RGBA float16 image, RGB = unit
normal in camera space (OpenCV axes: x right, y down, z forward, so a surface
facing the camera has n.z < 0), A = depth z in metres; plus `k = (kx, ky)`, the
ray slope across the frame, so a pixel at source-norm (u, v) sits at
((u - 0.5) * kx, (v - 0.5) * ky, 1) * z.
"""

from __future__ import annotations

import os
import sys
import threading
import time
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

from .analysis import LUMA_709, AnalysisCache, fit, guided_upsample, resize
from .erase import exposure_scale, to_display
from .model_fetch import Fetcher, ModelFile, cache_root
from .model_fetch import status as fetch_status

MODEL = ModelFile("model.onnx", "Ruicheng/moge-2-vits-normal-onnx", "e50ffda41565591092adea54c6ac83d6212e1e23",
                  "model.onnx", 140852051, "24eacb5dc7a2c54c7bc98f7de085ffbed79ad006ea5b664c2c2cdc02ff3a52f0")

# Long edge the model sees, and the token budget it spends on it. MoGe resizes
# to its own patch grid internally; 2500 tokens is about 700 px of ViT-S input,
# well inside its training range (1200..3600), and runs in ~0.2 s on DirectML.
INFER_LONG = 768
NUM_TOKENS = 2500
# Long edge of the texture the browser gets. The model's own output is soft at
# silhouettes, and stretched bilinearly over a full-resolution frame that
# softness lights a halo around every figure; so the normals are upsampled to
# this size with a guided filter on the frame's luminance, which moves their
# edges onto the photo's. Depth is not: the guide would print the photo's
# texture into it as tiny steps, and the shadow march reads every step as an
# occluder (streaks radiating from the light). The shader filters the rest of
# the way bilinearly.
OUT_LONG = 1536
GUIDE_RADIUS = 6       # px at OUT_LONG
GUIDE_EPS = 1e-3       # on display-encoded luma: edges stronger than ~3% steer the fit


def model_dir() -> Path:
    explicit = os.environ.get("LLR_RELIGHT_MODEL_DIR")
    return Path(explicit) if explicit else cache_root() / "models" / "moge"


FETCHER = Fetcher("relight", model_dir, (MODEL,))


# ── Model ──


class _Engine:
    def __init__(self, path: Path) -> None:
        from .gpu_host import make_session

        self.session = make_session(path)
        self.provider = self.session.get_providers()[0]

    def run(self, image: np.ndarray) -> dict[str, np.ndarray]:
        """image: H x W x 3 display RGB in [0, 1]. Returns points, normal, mask, scale."""
        x = np.ascontiguousarray(image.transpose(2, 0, 1)[None].astype(np.float32))
        names = [o.name for o in self.session.get_outputs()]
        out = self.session.run(None, {"image": x, "num_tokens": np.array(NUM_TOKENS, np.int64)})
        return dict(zip(names, out, strict=True))


# The worker's handle on the model: it runs in the GPU host (gpu_host.py), which
# gives its memory back when idle — a session here would pin it for good.
_ENGINE: Any = None
_ENGINE_LOCK = threading.Lock()


def _engine() -> Any:
    global _ENGINE
    with _ENGINE_LOCK:
        if _ENGINE is None:
            if FETCHER.missing():
                raise RuntimeError(f"relight model missing in {FETCHER.folder}")
            from .gpu_host import Remote

            _ENGINE = Remote("moge")
            sys.stderr.write(f"relight: MoGe-2 on {_ENGINE.provider} (GPU host)\n")
        return _ENGINE


def status() -> dict[str, Any]:
    return fetch_status(FETCHER, _ENGINE.provider if _ENGINE is not None else None, None)


def start_download() -> dict[str, Any]:
    FETCHER.start()
    return status()


# ── Camera recovery ──


def view_plane_span(width: int, height: int) -> tuple[float, float]:
    """MoGe's normalised image plane: half-extents along x and y, the half-diagonal being 1."""
    aspect = width / height
    d = np.sqrt(1 + aspect * aspect)
    return aspect / d, 1 / d


def recover_focal_shift(points: np.ndarray, mask: np.ndarray) -> tuple[float, float]:
    """Focal (in view-plane units) and z shift making `points` a pinhole projection of the frame.

    For a shift s the projection of each point is xy / (z + s), and the best
    focal for it is closed-form (least squares against the pixel's view-plane
    position); s itself is a 1-D search. MoGe solves the same problem with an
    L1 objective in `recover_focal_shift`; L2 on a subsample is close enough
    for lighting, which only ever reads the geometry smoothly.
    """
    h, w = points.shape[:2]
    sx, sy = view_plane_span(w, h)
    uu = (np.arange(w) + 0.5) / w * 2 - 1
    vv = (np.arange(h) + 0.5) / h * 2 - 1
    uv = np.stack(np.meshgrid(uu * sx, vv * sy), -1)
    step = max(1, int(np.sqrt(h * w / 8192)))
    sel = mask[::step, ::step]
    p = points[::step, ::step][sel]
    q = uv[::step, ::step][sel]
    if p.shape[0] < 16:
        return 1.0, 0.0
    z = p[:, 2]

    def solve(s: float) -> tuple[float, float]:
        proj = p[:, :2] / (z + s)[:, None]
        f = float(np.sum(proj * q) / max(np.sum(proj * proj), 1e-12))
        return float(np.mean(np.sum((proj * f - q) ** 2, -1))), f

    lo = -float(z.min()) + 1e-3 * max(float(np.abs(z).max()), 1e-6)
    span = max(float(z.max() - z.min()), 1e-3) * 20
    grid = lo + span * (np.linspace(0, 1, 64) ** 2)
    errs = [solve(s)[0] for s in grid]
    i = int(np.argmin(errs))
    a, b = grid[max(0, i - 1)], grid[min(len(grid) - 1, i + 1)]
    for _ in range(40):  # golden-section refine in the bracket
        m1, m2 = a + (b - a) * 0.382, a + (b - a) * 0.618
        if solve(m1)[0] < solve(m2)[0]:
            b = m2
        else:
            a = m1
    shift = (a + b) / 2
    return solve(shift)[1], shift


def display_at(frame: np.ndarray, size: tuple[int, int]) -> tuple[np.ndarray, np.ndarray]:
    """The frame box-averaged to `size` in scene light, and that tone-mapped the
    way erase shows its model a frame: exposure on mid-grey, a soft shoulder, sRGB.
    Returns (linear, display)."""
    small = resize(frame, size, Image.Resampling.BOX)
    return small, np.clip(to_display(small, exposure_scale(small)), 0, 1).astype(np.float32)


def estimate(frame: np.ndarray, engine: Any = None) -> tuple[np.ndarray, dict[str, Any]]:
    """Geometry for a scene-linear ProPhoto frame (H x W x 3). Returns (RGBA f16 texture, meta)."""
    t0 = time.perf_counter()
    height, width = frame.shape[:2]
    iw, ih = fit(width, height, INFER_LONG)
    ow, oh = fit(width, height, OUT_LONG)
    # The guide is wanted at OUT_LONG anyway: the model's input is reduced from
    # it rather than from a second pass over the full frame.
    guide_lin, guide_display = display_at(frame, (ow, oh)) if ow > iw else (frame, None)
    _, display = display_at(guide_lin, (iw, ih))
    out = (engine or _engine()).run(display)
    points, normal = out["points"][0], out["normal"][0]
    mask = out["mask"][0] > 0.5
    scale = float(np.asarray(out["scale"]).reshape(-1)[0])
    focal, shift = recover_focal_shift(points, mask)
    depth = (points[..., 2] + shift) * scale
    # Sky and other invalid pixels: push them far away and face them at the camera,
    # so a light near them neither lights nor shadows anything there.
    far = float(np.percentile(depth[mask], 99)) * 4 if mask.any() else 100.0
    depth = np.where(mask & (depth > 0), depth, far)
    normal = np.where(mask[..., None], normal, np.array([0, 0, -1], np.float32))
    if (ow, oh) != (iw, ih):
        normal = resize(normal, (ow, oh))
        # Depth in log so a figure and the wall behind it are one step, not a ramp.
        depth = np.exp(resize(np.log(np.maximum(depth, 1e-4)), (ow, oh)))
        if guide_display is not None:
            normal = guided_upsample(guide_display @ LUMA_709, normal, GUIDE_RADIUS, GUIDE_EPS)
    normal = normal / np.maximum(np.linalg.norm(normal, axis=-1, keepdims=True), 1e-6)
    tex = np.concatenate([normal, depth[..., None]], -1).astype(np.float16)
    sx, sy = view_plane_span(iw, ih)
    meta = {"width": ow, "height": oh, "k": [2 * sx / focal, 2 * sy / focal]}
    sys.stderr.write(f"relight: geometry {ow}x{oh} in {time.perf_counter() - t0:.2f}s "
                     f"(fovX {np.degrees(2 * np.arctan(sx / focal)):.0f}°, "
                     f"median depth {float(np.median(depth)):.2f} m)\n")
    return tex, meta


# Geometry per decode, keyed by the caller (the frame's cache key and erase strokes).
_CACHE: AnalysisCache[tuple[np.ndarray, dict[str, Any]]] = AnalysisCache(
    "relight",
    lambda hit: {"tex": hit[0], "k": np.array(hit[1]["k"])},
    lambda z: (z["tex"], {"width": int(z["tex"].shape[1]), "height": int(z["tex"].shape[0]),
                          "k": [float(v) for v in z["k"]]}))


def cached_estimate(key: str, frame_fn: Any, cache_dir: Path | None) -> tuple[np.ndarray, dict[str, Any]]:
    """`estimate` on the frame `frame_fn()` returns, cached in memory and beside the source."""
    return _CACHE.get(key, lambda: estimate(frame_fn()), cache_dir)
