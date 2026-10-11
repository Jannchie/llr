"""AI masks: subject, sky and person mattes for the masking panel.

Like the relight geometry (relight.py), these are a per-decode analysis the
browser reads as a texture — the selection itself, combining and feathering,
stays in the shader's mask machinery (masks.ts types "ai"). Three mattes in
one RGBA8 image in the decode's own frame (y down): R the salient subject, G
sky, B people.

Models (downloaded on first use, pinned and checked like erase's):
- ISNet general-use (DIS, Apache-2.0; rembg's release, MIT): the subject, at
  1024x1024 in ~0.5 s on the CPU.
- BiRefNet-lite (ZhengPeng7, MIT; onnx-community export): the subject again,
  only when ISNet is unsure (see SUBJECT_UNSURE) — ~6 s on the CPU, but decisive
  where ISNet leaves a crowd, or the subject among clutter, half-selected.
- SegFormer-B3 on ADE20K (NVIDIA, its own non-commercial research licence;
  onnx-community export): 150-class semantic segmentation at 512x512, read for
  "sky" (2) and "person" (12).

Both see the frame tone-mapped the way erase shows its model one; their
low-resolution outputs are upsampled with a guided filter on the frame's
luminance so the matte edges land on the photo's edges.
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
from .model_fetch import Fetcher, ModelFile, cache_root
from .model_fetch import status as fetch_status
from .relight import display_at

ISNET = ModelFile("isnet_general_use.onnx", "", "", "", 178648008,
                  "60920e99c45464f2ba57bee2ad08c919a52bbf852739e96947fbb4358c0d964a",
                  url="https://github.com/danielgatis/rembg/releases/download/v0.0.0/isnet-general-use.onnx")
SUBJECT = ModelFile("birefnet_lite.onnx", "onnx-community/BiRefNet_lite-ONNX",
                    "de15b22ba131738a16dff04aab8bdf8dc32e3ac1", "onnx/model.onnx",
                    224005088, "5600024376f572a557870a5eb0afb1e5961636bef4e1e22132025467d0f03333")
SEMANTIC = ModelFile("segformer_b3_ade.onnx", "onnx-community/segformer-b3-finetuned-ade-512-512-ONNX",
                     "748163478112c6185cd52530d7b133d96c8236be", "onnx/model.onnx",
                     190133539, "cc489c2a484e2932b30a6ec25e39efca281437a14973ab0959b0f1e54eb3bb82")
ADE_SKY, ADE_PERSON = 2, 12
MEAN = np.array([0.485, 0.456, 0.406], np.float32)
STD = np.array([0.229, 0.224, 0.225], np.float32)
# ISNet's own normalisation (rembg's): centred on 0.5, unscaled.
ISNET_MEAN = np.array([0.5, 0.5, 0.5], np.float32)
ISNET_STD = np.array([1.0, 1.0, 1.0], np.float32)
# How unsure an ISNet matte may be before BiRefNet is asked instead: the share of
# its selection (> 0.15) that is neither in nor out (0.15..0.85). Set on 88
# photos of the library this was built against: portraits, flowers and staged
# subjects sit at 0.01-0.3 and ISNet matches BiRefNet there; crowds, a subject
# among clutter and half-lit faces rise past 0.4, where ISNet goes grey and
# BiRefNet still commits. About one photo in five takes the second model.
SUBJECT_UNSURE = 0.35
OUT_LONG = 1024
GUIDE_RADIUS = 4
GUIDE_EPS = 2e-3


def model_dir() -> Path:
    explicit = os.environ.get("LLR_AI_MODEL_DIR")
    return Path(explicit) if explicit else cache_root() / "models" / "aimask"


FETCHER = Fetcher("ai-masks", model_dir, (ISNET, SUBJECT, SEMANTIC))


class _Session:
    """One graph, on the CPU — deliberately (gpu_host.py has the measurements).

    ISNet takes ~0.5 s here, so the GPU would buy little. BiRefNet at 1024 px
    peaks at 11 GB (fp16) to 21 GB (fp32) of VRAM on DirectML — more than the
    browser can spare beside it, and DirectML never gives it back while the
    process lives — for a matte made once per photo and cached; on the CPU it
    takes ~6 s and no VRAM. SegFormer's resize is rejected by DirectML at run
    time anyway.
    """

    def __init__(self, path: Path) -> None:
        from .gpu_host import make_session

        self.session = make_session(path, gpu=False)
        self.input = self.session.get_inputs()[0].name
        self.provider = self.session.get_providers()[0]

    def run(self, x: np.ndarray) -> np.ndarray:
        return self.session.run(None, {self.input: x})[0]


_ENGINE: dict[str, _Session] | None = None
_ENGINE_ERROR: str | None = None
_LOCK = threading.Lock()


def _engine() -> dict[str, _Session]:
    global _ENGINE, _ENGINE_ERROR
    with _LOCK:
        if _ENGINE is None:
            missing = FETCHER.missing()
            if missing:
                raise RuntimeError(f"AI mask models missing in {FETCHER.folder}: {', '.join(f.name for f in missing)}")
            try:
                # BiRefNet ("subject") is loaded on first need (subject_matte):
                # most photos never ask for it.
                _ENGINE = {"isnet": _Session(FETCHER.folder / ISNET.name),
                           "semantic": _Session(FETCHER.folder / SEMANTIC.name)}
            except Exception as error:
                _ENGINE_ERROR = f"could not load the AI mask models: {error}"
                raise RuntimeError(_ENGINE_ERROR) from error
            sys.stderr.write(f"ai-masks: loaded on {_ENGINE['isnet'].provider}\n")
        return _ENGINE


def _birefnet(eng: dict[str, Any]) -> Any:
    with _LOCK:
        if "subject" not in eng:
            eng["subject"] = _Session(FETCHER.folder / SUBJECT.name)
        return eng["subject"]


def status() -> dict[str, Any]:
    return fetch_status(FETCHER, _ENGINE["isnet"].provider if _ENGINE else None, _ENGINE_ERROR)


def start_download() -> dict[str, Any]:
    FETCHER.start()
    return status()


def _model_input(display: np.ndarray, side: int, mean: np.ndarray = MEAN, std: np.ndarray = STD) -> np.ndarray:
    x = (resize(display, (side, side), Image.Resampling.BICUBIC) - mean) / std
    return np.ascontiguousarray(x.transpose(2, 0, 1)[None].astype(np.float32))


def ambiguity(matte: np.ndarray) -> float:
    """Share of a matte's selection that is neither clearly in nor clearly out."""
    selected = matte > 0.15
    n = int(selected.sum())
    return float((selected & (matte < 0.85)).sum() / n) if n else 0.0


def subject_matte(display: np.ndarray, eng: dict[str, Any]) -> tuple[np.ndarray, str, float]:
    """The subject at 1024x1024 in [0, 1]: ISNet's, or BiRefNet's when ISNet is unsure."""
    y = eng["isnet"].run(_model_input(display, 1024, ISNET_MEAN, ISNET_STD)).reshape(1024, 1024).astype(np.float32)
    y = (y - y.min()) / max(float(y.max() - y.min()), 1e-6)
    amb = ambiguity(y)
    if amb <= SUBJECT_UNSURE:
        return y, "isnet", amb
    logit = _birefnet(eng).run(_model_input(display, 1024))[0, 0]
    return 1 / (1 + np.exp(-logit.astype(np.float32))), "birefnet", amb


def estimate(frame: np.ndarray, engine: dict[str, Any] | None = None) -> tuple[np.ndarray, dict[str, Any]]:
    """The three mattes for a scene-linear ProPhoto frame. Returns (RGBA8 image, meta)."""
    t0 = time.perf_counter()
    eng = engine or _engine()
    height, width = frame.shape[:2]
    ow, oh = fit(width, height, OUT_LONG)
    _, display = display_at(frame, (ow, oh))

    subject, by, amb = subject_matte(display, eng)
    logits = eng["semantic"].run(_model_input(display, 512))[0].astype(np.float32)
    logits -= logits.max(0, keepdims=True)
    prob = np.exp(logits)
    prob /= prob.sum(0, keepdims=True)
    mattes = np.stack([resize(m, (ow, oh)) for m in (subject, prob[ADE_SKY], prob[ADE_PERSON])], -1)
    mattes = np.clip(guided_upsample(display @ LUMA_709, mattes, GUIDE_RADIUS, GUIDE_EPS), 0, 1)
    rgba = np.concatenate([mattes, np.ones((oh, ow, 1), np.float32)], -1)
    out = np.round(rgba * 255).astype(np.uint8)
    sys.stderr.write(f"ai-masks: {ow}x{oh} in {time.perf_counter() - t0:.2f}s "
                     f"(subject by {by}, ISNet ambiguity {amb:.2f}; "
                     f"subject {mattes[..., 0].mean():.2f}, sky {mattes[..., 1].mean():.2f}, "
                     f"person {mattes[..., 2].mean():.2f})\n")
    return out, {"width": ow, "height": oh}


_CACHE: AnalysisCache[tuple[np.ndarray, dict[str, Any]]] = AnalysisCache(
    "aimask",
    lambda hit: {"img": hit[0]},
    lambda z: (z["img"], {"width": int(z["img"].shape[1]), "height": int(z["img"].shape[0])}))


def cached_estimate(key: str, frame_fn: Any, cache_dir: Path | None) -> tuple[np.ndarray, dict[str, Any]]:
    return _CACHE.get(key, lambda: estimate(frame_fn()), cache_dir)
