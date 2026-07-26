"""Sony's MainGamma curve, rebuilt from the RAW rather than baked in.

Every ARW carries the factory tone curve for all ten Creative Looks (see
sr2.look_calibrations), as 128 control points per look. Two scale factors turn
those into the engine's own 32768-entry LUT, and both were pinned by measurement
rather than guessed:

    x / 128   -> LUT index, where 8192 is Sony's white point
    y / 16    -> LUT output on a 16384 full scale

The anchor for the x scale is where the curve saturates: at x = 2^20, and
2^20 / 128 = 8192 lands exactly on the white point that four independent
measurements had already given. Rebuilt this way, the curve matches what Frida
dumps out of the running engine to within 8/16384 (0.05%) on all ten looks.

On top of that baseline the engine applies the in-camera tweaks that ride on the
curve: Highlights, Shadows and Contrast, each -9..+9. Highlights, Shadows and
negative Contrast are strictly linear in the setting — feeding a unit shape
measured at +-9 reproduces every intermediate step to within 3/16384 — but the
two directions have different shapes, and each look has its own pair. Positive
Contrast is the exception and needs every step measured; see apply_tuning. All
of those shapes are measured, not derived, and live in data/look_tuning.npz;
how they were captured is in ../../../../sony_repro.

Fade is deliberately absent, and that was the clue that found it: it measures as
exactly zero on this curve because it acts on YGamma instead, as a contrast pull
toward a pivot on luma alone (chroma.luma_gamma).
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import numpy as np

from .sr2 import LookCalibration

_DATA = Path(__file__).resolve().parent / "data"

# Sony's own order for the ten SR2DataIFDs. The RAW stores no look codes, only
# names like "Standard" — the code is this pipeline's own shorthand, matching
# what the body writes into the CreativeStyle exif tag.
LOOK_ORDER = ("ST", "VV", "NT", "PT", "FL", "VV2", "IN", "SH", "BW", "SE")

TONE_INDEX_WHITE = 8192       # LUT index of Sony's white
CURVE_X_SCALE = 128.0         # tag 0x7805 units per LUT index
CURVE_Y_FULL = 16.0 * 16384.0  # tag 0x7806 units at full scale

# The camera's own range. Edit.exe treats anything past it as no tweak at all —
# +-10 renders identically to 0 — but that is input validation on a value the
# body can never write, not a statement about the curve, so this pipeline
# extrapolates instead (see apply_tuning).
TUNE_LIMIT = 9
TUNE_EXTRAPOLATION_LIMIT = 30


def look_index(style: str) -> int | None:
    return LOOK_ORDER.index(style) if style in LOOK_ORDER else None


@lru_cache(maxsize=1)
def _tuning() -> dict[str, np.ndarray]:
    """Measured unit shapes, keyed "<look>_<field>_<side>" -> (8193,) fractions."""
    with np.load(_DATA / "look_tuning.npz") as z:
        return {k: z[k].astype(np.float64) for k in z.files}


def base_curve(cal: LookCalibration, n: int = TONE_INDEX_WHITE + 1) -> np.ndarray:
    """One look's factory curve as `n` samples over [0, white point].

    Output is display-*encoded*, exactly as the engine's LUT holds it — the sRGB
    transfer function is already baked in. The control points are not evenly
    spaced (dense in the shadows), hence the explicit x array.
    """
    return np.interp(
        np.linspace(0.0, TONE_INDEX_WHITE, n),
        cal.curve_x.astype(np.float64) / CURVE_X_SCALE,
        cal.curve_y.astype(np.float64) / CURVE_Y_FULL,
    )


def _resample(shape: np.ndarray, n: int) -> np.ndarray:
    if shape.size == n:
        return shape
    return np.interp(np.linspace(0, 1, n), np.linspace(0, 1, shape.size), shape)


def _positive_contrast(shapes: dict[str, np.ndarray], style: str, value: float,
                       n: int) -> np.ndarray | None:
    """Positive Contrast, which is the one tweak that is *not* linear.

    Highlights, Shadows and negative Contrast all scale a single unit shape.
    Positive Contrast changes shape as well as size — rescaling the +9 shape to
    +3 leaves a residual of 44/16384 against a total amplitude of 110 — so each
    step is measured separately and this interpolates between the measured ones.
    Past +9 it keeps going along the last step's difference, on the same
    reasoning as the linear tweaks.
    """
    steps = shapes.get(f"{style}_contrast_steps")
    if steps is None:
        return None
    i = min(int(value), steps.shape[0] - 1)          # 1-based settings, 0-based rows
    lo = _resample(steps[i - 1], n) if i >= 1 else np.zeros(n)
    hi = _resample(steps[i], n)
    return lo + (value - i) * (hi - lo)


def apply_tuning(curve: np.ndarray, style: str, highlights: int = 0, shadows: int = 0,
                 contrast: int = 0) -> np.ndarray:
    """Add the in-camera tone tweaks to a factory curve.

    Inside the camera's own -9..+9 this is the engine's behaviour. Highlights,
    Shadows and negative Contrast are strictly linear in the setting, so a unit
    shape times the value reproduces every intermediate step to within 3/16384.
    Positive Contrast is not linear and is handled by _positive_contrast.

    Past that range the two diverge deliberately. Edit.exe ignores out-of-range
    values outright — +-10 renders identically to 0 — which is validation on a
    number the body can never write, not a claim that the curve stops there.
    Since the response is linear, the same unit shape keeps extrapolating, so
    this carries on out to TUNE_EXTRAPOLATION_LIMIT and clamps beyond it. The
    curve is clipped to [0, 1] at the end either way, which is what bounds a
    large setting rather than the setting itself being refused.

    A look with no measured shapes on file keeps its baseline: the tweak refines
    an already-correct curve rather than being a prerequisite for one.
    """
    out = curve
    shapes = _tuning()
    for field, value in (("highlights", highlights), ("shadows", shadows),
                         ("contrast", contrast)):
        if not value:
            continue
        amount = float(np.clip(value, -TUNE_EXTRAPOLATION_LIMIT, TUNE_EXTRAPOLATION_LIMIT))
        if field == "contrast" and amount > 0:
            delta = _positive_contrast(shapes, style, amount, out.size)
            if delta is not None:
                out = out + delta
            continue
        shape = shapes.get(f"{style}_{field}_{'neg' if value < 0 else 'pos'}")
        if shape is None:
            continue
        out = out + amount * _resample(shape, out.size)
    return np.clip(out, 0.0, 1.0)


def tone_curve(
    cal: LookCalibration, style: str, highlights: int = 0, shadows: int = 0,
    contrast: int = 0, n: int = TONE_INDEX_WHITE + 1,
) -> np.ndarray:
    """A look's complete display-encoded curve for one shot's settings."""
    return apply_tuning(base_curve(cal, n), style, highlights, shadows, contrast)
