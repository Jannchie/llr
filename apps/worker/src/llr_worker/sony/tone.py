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

On top of that baseline the engine applies the in-camera tweaks (Highlights and
Shadows, each -9..+9). Their effect is strictly linear in the setting — feeding
a unit shape measured at +-9 reproduces every intermediate step to within
3/16384 — but the two directions have different shapes, and each look has its
own pair. Those 40 shapes are measured, not derived, and live in
data/look_tuning.npz; how they were captured is in ../../../../sony_repro.

Fade is deliberately absent: it measures as exactly zero on this curve, so it
must act on one of the later YCC stages instead.
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

TUNE_LIMIT = 9  # the engine treats anything beyond this as no tweak at all


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


def apply_tuning(curve: np.ndarray, style: str, highlights: int = 0, shadows: int = 0) -> np.ndarray:
    """Add the in-camera Highlights/Shadows tweaks to a factory curve.

    Settings outside +-9 are ignored rather than clamped, which is what the
    engine itself does: +-10 renders identically to 0. A look with no measured
    shapes on file keeps its baseline, since the tweak is a refinement of an
    already-correct curve rather than a prerequisite for one.
    """
    out = curve
    shapes = _tuning()
    for field, value in (("highlights", highlights), ("shadows", shadows)):
        if not value or abs(value) > TUNE_LIMIT:
            continue
        shape = shapes.get(f"{style}_{field}_{'neg' if value < 0 else 'pos'}")
        if shape is None:
            continue
        if shape.size != out.size:
            shape = np.interp(np.linspace(0, 1, out.size), np.linspace(0, 1, shape.size), shape)
        out = out + value * shape
    return np.clip(out, 0.0, 1.0)


def tone_curve(
    cal: LookCalibration, style: str, highlights: int = 0, shadows: int = 0,
    n: int = TONE_INDEX_WHITE + 1,
) -> np.ndarray:
    """A look's complete display-encoded curve for one shot's settings."""
    return apply_tuning(base_curve(cal, n), style, highlights, shadows)
