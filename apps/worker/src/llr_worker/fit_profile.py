"""Fit a HueSatMap-shaped correction that pulls LLR's DCP render onto the
camera's own JPEG rendering.

Why this exists: Adobe's profiles for some bodies (the Sony ILCE-7CM2 among
them) ship an *empty* ProfileHueSatMapData, leaving all colour correction to a
3x3 ForwardMatrix. A 3x3 cannot express hue-dependent adjustments, so the
camera's per-hue tuning is structurally unreachable — measured against the
in-camera JPEG the residual hue error runs 13-19 degrees.

What is fitted: a table in exactly the DCP HueSatMap shape (hue x sat x value ->
delta-hue, sat-scale, value-scale), applied *after* the full Adobe chain as a
residual. Keeping the DCP's own tables untouched means the fit can be inspected,
disabled or re-fitted without losing Adobe's calibration underneath it.

Where it is fitted: scene-linear ProPhoto, the space the table is applied in.
The camera JPEG is display-referred, so its pixels are pushed back through the
inverse profile tone curve first. That inverse is well conditioned exactly where
the fit matters and blows up near white, which is why highlights are downweighted
rather than trusted (see WEIGHT_VALUE_KNEE).

Training data is free: every RAW carries a full-size in-camera JPEG, so a folder
of ARWs is already a labelled set — no paired shooting required.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import rawpy

from .dcp import (
    DcpHueSatMap,
    DcpProfile,
    ENCODING_SRGB,
    XYZ_D50_TO_PROPHOTO,
    apply_hsv_table,
    camera_to_xyz_matrix,
    rgb_to_hsv,
    select_hue_sat_map,
    srgb_encode_float,
)

# Grid resolution. Hue gets the most cells because hue error is the dominant
# residual; value gets the fewest because the inverse tone curve makes the
# brightest cells the least trustworthy anyway.
DEFAULT_DIMS = (24, 8, 6)  # (hue, sat, value)

# A cell needs this many pixels before its correction is believed. Below it the
# cell stays identity, so colours the training set never saw are passed through
# untouched instead of extrapolated into garbage.
MIN_SAMPLES = 200

# Above this value the inverse tone curve is near-vertical (the curve maps 0.9 ->
# 0.993, so inverting amplifies noise ~15x); such pixels still contribute, but
# with rapidly decaying weight.
WEIGHT_VALUE_KNEE = 0.85

# Neutral pixels have no meaningful hue, and their sat-scale is a 0/0.
MIN_SATURATION = 0.06

# Corrections are clamped to a sane band: a fit is a refinement of Adobe's
# calibration, not a licence to invert it.
MAX_HUE_SHIFT_DEG = 30.0
SAT_SCALE_RANGE = (0.5, 2.0)
VAL_SCALE_RANGE = (0.7, 1.4)


@dataclass
class FitReport:
    cells_total: int
    cells_fitted: int
    samples: int
    hue_error_before: float
    hue_error_after: float
    sat_ratio_before: float
    sat_ratio_after: float

    def summary(self) -> str:
        pct = 100.0 * self.cells_fitted / max(self.cells_total, 1)
        return (
            f"{self.samples:,} samples, {self.cells_fitted}/{self.cells_total} cells fitted ({pct:.0f}%)\n"
            f"  hue error   {self.hue_error_before:.2f}deg -> {self.hue_error_after:.2f}deg\n"
            f"  sat ratio   {self.sat_ratio_before:.3f} -> {self.sat_ratio_after:.3f}  (1.000 = match)"
        )


def identity_table(dims: tuple[int, int, int]) -> np.ndarray:
    hue_n, sat_n, val_n = dims
    table = np.zeros((val_n, hue_n, sat_n, 3), dtype=np.float32)
    table[..., 1] = 1.0
    table[..., 2] = 1.0
    return table


def to_hsv_for_table(linear: np.ndarray, encoding: int) -> np.ndarray:
    """Match apply_hsv_table's own pre-processing exactly, or the fit lands in a
    different space than it is later applied in."""
    flat = np.clip(np.nan_to_num(linear, nan=0.0, posinf=1.0, neginf=0.0), 0, 1).reshape(-1, 3)
    if encoding == ENCODING_SRGB:
        flat = srgb_encode_float(flat)
    return rgb_to_hsv(flat)


def inverse_tone_curve(display: np.ndarray, curve: np.ndarray) -> np.ndarray:
    """Push display-referred pixels back through the profile tone curve.

    The curve is monotonic, so np.interp with the axes swapped is its inverse.
    Applied per channel because that is how the renderer applies the forward
    direction (passes.ts viewTransform).
    """
    x, y = curve[:, 0].astype(np.float64), curve[:, 1].astype(np.float64)
    order = np.argsort(y)
    ys, xs = y[order], x[order]
    out = np.empty_like(display)
    for c in range(3):
        out[..., c] = np.interp(np.clip(display[..., c], 0.0, 1.0), ys, xs)
    return out.astype(np.float32)


def dcp_base_linear(camera_rgb: np.ndarray, profile: DcpProfile) -> np.ndarray:
    """The full Adobe chain in scene-linear ProPhoto — what the residual sits on top of."""
    _, camera_to_xyz = camera_to_xyz_matrix(profile)
    linear = np.clip(camera_rgb @ camera_to_xyz.T @ XYZ_D50_TO_PROPHOTO.T, 0, None)
    hue_sat_map = select_hue_sat_map(profile)
    if hue_sat_map is not None:
        linear = apply_hsv_table(linear, hue_sat_map, profile.hue_sat_map_encoding)
    if profile.look_table is not None:
        linear = apply_hsv_table(linear, profile.look_table, profile.look_table_encoding)
    return linear


def postprocess_camera_native(raw: Any, half_size: bool = True) -> np.ndarray:
    """Camera-native linear RGB from an open rawpy handle.

    This is the exact input the DCP chain trains and renders against, so the
    fitter and the renderer must decode identically — both go through here so
    the parameter set cannot silently drift apart in one and not the other.
    """
    return np.divide(
        raw.postprocess(
            use_camera_wb=True, no_auto_bright=True, output_color=rawpy.ColorSpace.raw,
            gamma=(1, 1), output_bps=16, half_size=half_size,
        ),
        65535.0, dtype=np.float32,
    )


def decode_camera_rgb(raw_path: Path, half_size: bool = True) -> np.ndarray:
    with rawpy.imread(str(raw_path)) as raw:
        return postprocess_camera_native(raw, half_size)


CAMERA_MATCH_ROOT = Path("vendor/camera-match")


def camera_match_path(root: Path, camera: str, code: str) -> Path:
    """Where a fitted camera-match table for (camera, style code) lives.

    The fitter writes here and the decode-time loader reads here, so a freshly
    fitted table is always found where it was written — the path convention has
    one home instead of two hand-written expressions that must agree.
    """
    return root / CAMERA_MATCH_ROOT / camera / f"{code}.hsm.npz"


def accumulate(
    source_linear: np.ndarray,
    target_linear: np.ndarray,
    dims: tuple[int, int, int],
    encoding: int,
    acc: dict[str, np.ndarray] | None = None,
) -> dict[str, np.ndarray]:
    """Bin one image's pixel pairs into per-cell correction sums."""
    hue_n, sat_n, val_n = dims
    src = to_hsv_for_table(source_linear, encoding)
    tgt = to_hsv_for_table(target_linear, encoding)

    keep = (src[:, 1] > MIN_SATURATION) & (tgt[:, 2] > 1e-4) & (src[:, 2] > 1e-4)
    src, tgt = src[keep], tgt[keep]
    if src.shape[0] == 0:
        return acc if acc is not None else _empty_acc(dims)

    # Shortest-path hue delta in degrees.
    dh = (tgt[:, 0] - src[:, 0]) * 360.0
    dh = (dh + 180.0) % 360.0 - 180.0
    ds = tgt[:, 1] / np.maximum(src[:, 1], 1e-6)
    dv = tgt[:, 2] / np.maximum(src[:, 2], 1e-6)

    w = np.ones_like(dh, dtype=np.float32)
    hi = src[:, 2] > WEIGHT_VALUE_KNEE
    w[hi] *= np.maximum(0.0, 1.0 - (src[:, 2][hi] - WEIGHT_VALUE_KNEE) / (1.0 - WEIGHT_VALUE_KNEE)) ** 2
    w *= np.clip(src[:, 1], 0.0, 1.0)  # saturated pixels carry more hue information

    hi_idx = np.clip((src[:, 0] * hue_n).astype(np.int32), 0, hue_n - 1)
    si_idx = np.clip((src[:, 1] * sat_n).astype(np.int32), 0, sat_n - 1)
    vi_idx = np.clip((src[:, 2] * val_n).astype(np.int32), 0, val_n - 1)
    flat = (vi_idx * hue_n + hi_idx) * sat_n + si_idx
    size = val_n * hue_n * sat_n

    if acc is None:
        acc = _empty_acc(dims)
    acc["w"] += np.bincount(flat, weights=w, minlength=size).astype(np.float64)
    acc["n"] += np.bincount(flat, minlength=size).astype(np.float64)
    acc["dh"] += np.bincount(flat, weights=w * dh, minlength=size).astype(np.float64)
    acc["ds"] += np.bincount(flat, weights=w * np.log(np.maximum(ds, 1e-6)), minlength=size).astype(np.float64)
    acc["dv"] += np.bincount(flat, weights=w * np.log(np.maximum(dv, 1e-6)), minlength=size).astype(np.float64)
    return acc


def _empty_acc(dims: tuple[int, int, int]) -> dict[str, np.ndarray]:
    size = dims[0] * dims[1] * dims[2]
    return {k: np.zeros(size, dtype=np.float64) for k in ("w", "n", "dh", "ds", "dv")}


def solve(
    acc: dict[str, np.ndarray],
    dims: tuple[int, int, int],
    min_samples: int = MIN_SAMPLES,
    fit_value: bool = True,
) -> tuple[np.ndarray, int]:
    """Turn accumulated sums into a table, leaving under-sampled cells at identity.

    `fit_value=False` pins the value-scale channel to 1. Sony's DRO is on by
    default and is spatially adaptive: it brightens shadows by an amount that
    depends on the neighbourhood, which no per-colour table can reproduce. Hue
    and saturation survive it well enough to fit, but the value axis would just
    be absorbing DRO's local decisions as if they were a property of the colour.
    """
    hue_n, sat_n, val_n = dims
    shape = (val_n, hue_n, sat_n)
    w = acc["w"].reshape(shape)
    n = acc["n"].reshape(shape)
    ok = (n >= min_samples) & (w > 1e-6)

    table = identity_table(dims)
    safe = np.maximum(w, 1e-12)
    table[..., 0] = np.where(ok, np.clip(acc["dh"].reshape(shape) / safe, -MAX_HUE_SHIFT_DEG, MAX_HUE_SHIFT_DEG), 0.0)
    # Ratios are averaged in log space so that 2x and 0.5x are symmetric.
    table[..., 1] = np.where(ok, np.clip(np.exp(acc["ds"].reshape(shape) / safe), *SAT_SCALE_RANGE), 1.0)
    if fit_value:
        table[..., 2] = np.where(ok, np.clip(np.exp(acc["dv"].reshape(shape) / safe), *VAL_SCALE_RANGE), 1.0)

    # Smooth across hue (circular) so neighbouring cells cannot produce a visible
    # seam where one was fitted and the next fell back to identity.
    for c in range(3):
        table[..., c] = _smooth_hue(table[..., c])
    return table.astype(np.float32), int(ok.sum())


def _smooth_hue(plane: np.ndarray) -> np.ndarray:
    left = np.roll(plane, 1, axis=1)
    right = np.roll(plane, -1, axis=1)
    return 0.25 * left + 0.5 * plane + 0.25 * right


def apply_correction(linear: np.ndarray, table: np.ndarray, dims: tuple[int, int, int], encoding: int) -> np.ndarray:
    return apply_hsv_table(linear, DcpHueSatMap(dimensions=dims, data=table), encoding)


def _forward_tone(linear: np.ndarray, curve: np.ndarray) -> np.ndarray:
    x, y = curve[:, 0], curve[:, 1]
    return np.stack([np.interp(np.clip(linear[..., c], 0, 1), x, y) for c in range(3)], axis=-1).astype(np.float32)


def _to_lab(linear_display: np.ndarray) -> np.ndarray:
    from .dcp import D50_WHITE_XYZ, PROPHOTO_TO_XYZ_D50

    xyz = np.clip(linear_display, 0, None).reshape(-1, 3) @ PROPHOTO_TO_XYZ_D50.T
    t = xyz / D50_WHITE_XYZ
    f = np.where(t > 216 / 24389, np.cbrt(np.maximum(t, 1e-9)), (24389 / 27 * t + 16) / 116)
    return np.stack([116 * f[:, 1] - 16, 500 * (f[:, 0] - f[:, 1]), 200 * (f[:, 1] - f[:, 2])], axis=-1)


def delta_e(source_linear: np.ndarray, target_linear: np.ndarray, curve: np.ndarray) -> dict[str, float]:
    """Perceptual CIEDE76 between a render and the camera JPEG, in the display
    space both actually reach — the only figure that reflects what the eye sees.

    Both inputs are scene-linear; the target is the inverse-toned camera JPEG, so
    forwarding both through the profile tone curve lands them in display-referred
    ProPhoto together.
    """
    src = _to_lab(_forward_tone(source_linear, curve))
    tgt = _to_lab(_forward_tone(target_linear, curve))
    dE = np.sqrt(((src - tgt) ** 2).sum(axis=1))
    dC = np.sqrt(((src[:, 1:] - tgt[:, 1:]) ** 2).sum(axis=1))
    return {
        "mean": float(dE.mean()), "p50": float(np.percentile(dE, 50)),
        "p95": float(np.percentile(dE, 95)), "chroma": float(dC.mean()),
        "lightness": float(np.abs(src[:, 0] - tgt[:, 0]).mean()),
    }


def measure(source_linear: np.ndarray, target_linear: np.ndarray, encoding: int) -> tuple[float, float]:
    src = to_hsv_for_table(source_linear, encoding)
    tgt = to_hsv_for_table(target_linear, encoding)
    m = (src[:, 1] > MIN_SATURATION) & (src[:, 2] > 1e-3)
    if m.sum() == 0:
        return float("nan"), float("nan")
    dh = np.abs((tgt[m, 0] - src[m, 0]) * 360.0)
    dh = np.minimum(dh, 360.0 - dh)
    return float(dh.mean()), float(tgt[m, 1].mean() / max(src[m, 1].mean(), 1e-6))


def save(path: Path, table: np.ndarray, dims: tuple[int, int, int], encoding: int, meta: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(
        path, table=table, dims=np.asarray(dims, dtype=np.int32),
        encoding=np.int32(encoding), meta=np.asarray([repr(meta)], dtype=object),
    )


def load(path: Path) -> tuple[np.ndarray, tuple[int, int, int], int] | None:
    if not path.exists():
        return None
    with np.load(path, allow_pickle=True) as data:
        return (
            data["table"].astype(np.float32),
            tuple(int(v) for v in data["dims"]),  # type: ignore[return-value]
            int(data["encoding"]),
        )
