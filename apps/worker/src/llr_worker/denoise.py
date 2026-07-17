"""RAW-domain denoising.

We denoise on the Bayer mosaic *before* LibRaw demosaics it. The flow is:

    pack the CFA into 4 colour planes (one per 2x2 phase) -> normalise to [0,1]
    using the sensor black/white levels -> run a denoiser -> denormalise ->
    unpack back to a mosaic -> write it into ``raw.raw_image`` in place.

``rawpy.postprocess()`` then demosaics the *cleaned* mosaic, so the rest of the
pipeline (DCP, edits, export) is untouched. This matches how Lightroom AI
Denoise / DxO DeepPRIME operate: at the mosaic, where the noise is closest to
its sensor statistics and not yet correlated by demosaic interpolation.

Only sensors with a 2x2 Bayer CFA are supported; other layouts (Fuji X-Trans's
6x6, monochrome) are detected and skipped so we never write a scrambled mosaic
back into the file's data.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Protocol

import numpy as np

# ── Bayer pack / unpack ────────────────────────────────────────────────────
#
# A Bayer mosaic interleaves four "phases" in a 2x2 tile. We split those phases
# into four half-resolution planes so a network sees clean per-colour channels:
#
#     row0:  P0 P1 P0 P1 ...        plane 0 = mosaic[0::2, 0::2]  (top-left)
#     row1:  P2 P3 P2 P3 ...        plane 1 = mosaic[0::2, 1::2]  (top-right)
#     row2:  P0 P1 P0 P1 ...        plane 2 = mosaic[1::2, 0::2]  (bottom-left)
#     ...                           plane 3 = mosaic[1::2, 1::2]  (bottom-right)
#
# For raw_pattern [[0,1],[3,2]] + color_desc "RGBG" (the Sony A7C II and most
# Bayer sensors) the phase order is R, G, G, B — i.e. packed RGGB, which is what
# raw-denoise models expect.


def pack_bayer(mosaic: np.ndarray) -> np.ndarray:
    """(H, W) mosaic -> (H/2, W/2, 4) phase planes. H, W must be even."""
    return np.stack(
        (
            mosaic[0::2, 0::2],
            mosaic[0::2, 1::2],
            mosaic[1::2, 0::2],
            mosaic[1::2, 1::2],
        ),
        axis=-1,
    )


def unpack_bayer(planes: np.ndarray) -> np.ndarray:
    """Inverse of :func:`pack_bayer`. (H/2, W/2, 4) -> (H, W)."""
    h2, w2, _ = planes.shape
    out = np.empty((h2 * 2, w2 * 2), dtype=planes.dtype)
    out[0::2, 0::2] = planes[..., 0]
    out[0::2, 1::2] = planes[..., 1]
    out[1::2, 0::2] = planes[..., 2]
    out[1::2, 1::2] = planes[..., 3]
    return out


def cfa_is_bayer_2x2(raw: Any) -> bool:
    """True when the sensor uses a plain 2x2 Bayer CFA that pack/unpack handle.

    X-Trans (6x6 ``raw_pattern``) and monochrome sensors fall outside the 2x2
    assumption baked into :func:`pack_bayer`; running them through it would
    scramble the mosaic, so callers must skip denoise for them.
    """
    pattern = getattr(raw, "raw_pattern", None)
    if pattern is None:
        return False
    return np.asarray(pattern).shape == (2, 2)


def _plane_black_levels(raw: Any, row_phase: int = 0, col_phase: int = 0) -> np.ndarray:
    """Black level for each of the 4 packed phases, ordered TL, TR, BL, BR.

    ``black_level_per_channel`` is indexed by CFA colour index; ``raw_pattern``
    (row-major) maps phase position -> colour index, so we gather through it.

    ``raw_pattern`` describes the full ``raw_image`` from (0, 0), but we pack the
    *visible* crop, whose origin can sit at an odd margin. ``row_phase`` /
    ``col_phase`` are that origin's parity; rolling the pattern by them keeps each
    packed plane aligned to its true CFA colour — otherwise an odd margin would
    swap the per-phase black levels and tint the denoised result.
    """
    pattern = np.asarray(raw.raw_pattern)
    black = np.asarray(raw.black_level_per_channel, dtype=np.float32)
    if pattern.shape != (2, 2) or black.size < 4:
        # Callers guard on cfa_is_bayer_2x2; scalar fallback kept as a backstop.
        return np.full(4, float(black.reshape(-1)[0]), dtype=np.float32)
    pattern = np.roll(pattern, shift=(-(row_phase % 2), -(col_phase % 2)), axis=(0, 1))
    return black[pattern.reshape(-1)].astype(np.float32)


# ── Denoiser backends ──────────────────────────────────────────────────────


class Denoiser(Protocol):
    """A denoiser maps normalised RGGB planes -> denoised planes, same shape.

    ``planes`` is (H/2, W/2, 4) float32 in [0, 1]. ``sigma`` is an optional
    noise-level hint in the same [0, 1] scale (None = blind / self-estimated).
    """

    def __call__(self, planes: np.ndarray, sigma: float | None = None) -> np.ndarray: ...

    name: str


class PassthroughDenoiser:
    """No-op denoiser. Used to validate the pack/unpack/writeback plumbing:
    a full round-trip should reproduce the original mosaic bit-for-bit."""

    name = "passthrough"

    def __call__(self, planes: np.ndarray, sigma: float | None = None) -> np.ndarray:
        return planes


class WaveletDenoiser:
    """Classical raw denoiser: square-root VST + adaptive wavelet shrinkage.

    Sensor noise is signal-dependent (Poisson-dominated): bright pixels carry
    more noise than dark ones. We stabilise it with a square-root variance-
    stabilising transform (the Poisson VST, up to constants), so a single
    shrinkage threshold applies uniformly across the tonal range, then denoise
    each colour plane with BayesShrink soft-thresholding (per-subband adaptive
    threshold from an estimated noise level), and invert the VST.

    Calibration-free and sensor-agnostic, so it generalises to any camera — the
    reliable default while a permissively-licensed neural backend is wired in.
    Runs on CPU (scikit-image); a one-time cost per source, then cached.
    """

    name = "wavelet"

    def __init__(self, wavelet: str = "sym4", strength: float = 1.0) -> None:
        self.wavelet = wavelet
        self.strength = strength

    def __call__(self, planes: np.ndarray, sigma: float | None = None) -> np.ndarray:
        from skimage.restoration import denoise_wavelet, estimate_sigma

        out = np.empty_like(planes)
        for c in range(planes.shape[-1]):
            plane = np.clip(planes[..., c], 0.0, 1.0)
            vst = np.sqrt(plane)  # Poisson variance-stabilising transform
            est = float(estimate_sigma(vst)) if sigma is None else sigma
            den = denoise_wavelet(
                vst,
                sigma=est * self.strength,
                wavelet=self.wavelet,
                mode="soft",
                method="BayesShrink",
                rescale_sigma=False,
            )
            out[..., c] = np.clip(den, 0.0, 1.0) ** 2  # invert VST
        return out


# ── Orchestration: denoise a rawpy object in place ─────────────────────────


@dataclass
class DenoiseStats:
    model: str
    height: int
    width: int
    black_levels: list[float]
    white_level: int
    sigma: float | None


def denoise_raw_inplace(
    raw: Any,
    denoiser: Denoiser,
    *,
    sigma: float | None = None,
) -> DenoiseStats | None:
    """Denoise ``raw``'s visible Bayer mosaic in place.

    Mutates ``raw.raw_image`` so a subsequent ``raw.postprocess()`` demosaics the
    cleaned data. Returns stats for logging, or ``None`` when the sensor's CFA is
    not 2x2 Bayer (X-Trans, monochrome) and the mosaic was left untouched. Odd
    trailing row/column (rare) is left untouched so dimensions always stay valid.
    """
    if not cfa_is_bayer_2x2(raw):
        return None
    visible = raw.raw_image_visible  # view into raw.raw_image
    h, w = visible.shape
    he, we = h - (h % 2), w - (w % 2)

    mosaic = np.ascontiguousarray(visible[:he, :we])
    planes = pack_bayer(mosaic).astype(np.float32)

    # The visible crop can start at an odd margin, shifting the CFA phase of its
    # origin relative to raw_image; pass that parity so black levels stay aligned.
    sizes = raw.sizes
    black = _plane_black_levels(
        raw,
        int(getattr(sizes, "top_margin", 0) or 0),
        int(getattr(sizes, "left_margin", 0) or 0),
    )  # (4,)
    white = float(raw.white_level)
    scale = np.maximum(white - black, 1.0)

    norm = (planes - black) / scale
    np.clip(norm, 0.0, 1.0, out=norm)

    denoised = denoiser(norm, sigma)

    denoised = denoised * scale + black
    np.clip(denoised, 0.0, white, out=denoised)
    denoised = np.rint(denoised).astype(visible.dtype)

    visible[:he, :we] = unpack_bayer(denoised)

    return DenoiseStats(
        model=getattr(denoiser, "name", "unknown"),
        height=he,
        width=we,
        black_levels=[float(b) for b in black],
        white_level=int(white),
        sigma=sigma,
    )


# ── Registry ───────────────────────────────────────────────────────────────

# Lazily-constructed singletons so we only build a denoiser when a recipe
# actually asks for it.
_DENOISER_CACHE: dict[str, Denoiser] = {}

# Model id -> factory. No neural backend ships yet (deferred; must be
# non-GPL); when one lands it should call register_denoiser() from its own
# module to keep torch out of this module's import path.
_FACTORIES: dict[str, Callable[[], Denoiser]] = {
    "passthrough": PassthroughDenoiser,
    "wavelet": WaveletDenoiser,
}

# Default model used when a recipe enables denoise without naming a backend.
DEFAULT_MODEL = "wavelet"


def register_denoiser(model_id: str, factory: Callable[[], Denoiser]) -> None:
    _FACTORIES[model_id] = factory


def get_denoiser(model_id: str) -> Denoiser:
    if model_id not in _DENOISER_CACHE:
        factory = _FACTORIES.get(model_id)
        if factory is None:
            raise ValueError(
                f"unknown denoise model {model_id!r}; "
                f"available: {sorted(_FACTORIES)}"
            )
        _DENOISER_CACHE[model_id] = factory()
    return _DENOISER_CACHE[model_id]
