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
curve: Highlights, Shadows and Contrast, each -9..+9. This is not a fit to
measurements — it is the engine's own construction, read out of Edit.exe and
checked against it (see apply_tuning). All three tweaks collapse into a single
lookup over the curve's *output*, built from a family of 37 static curves that
ships as data/tone_family.npz:

    gain_lo = 18 + contrast - shadows        over table indices [0, 471)
    gain_hi = 18 + contrast + highlights     over table indices [471, 1025)

family[18] is the identity, so all three at zero leaves the curve alone.

Two things follow from the family's spacing, which is far from uniform — a step
above 18 moves the shadow end by about 3/65536, a step below it by about 12.
Positive Contrast is nonlinear in the setting, and the same +1 of Contrast lands
differently depending on where the shot's own Highlights and Shadows have already
put the gain. That second effect looked for a while like a per-body difference,
because the two bodies on hand happened to ship files with different tweaks.

Because the three tweaks are summed into a gain rather than applied one after
another, a look has nothing to do with it, and neither does the order they are
set in. That is what makes FL2 and FL3 work: those looks exist only on newer
bodies and llr borrows their curve (see donor_looks), which under an earlier
per-look table left the three sliders doing nothing at all.

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
TONE_OUTPUT_FULL = 16384.0    # the engine's LUT holds this as full scale
CURVE_Y_FULL = 16.0 * TONE_OUTPUT_FULL  # tag 0x7806 units at full scale

# The camera's own range. Edit.exe treats anything past it as no tweak at all —
# +-10 renders identically to 0 — but that is input validation on a value the
# body can never write, not a statement about the curve, so this pipeline
# carries on past it (see apply_tuning).
TUNE_LIMIT = 9
TUNE_EXTRAPOLATION_LIMIT = 30

# The engine's own tone-operator construction, read out of Edit.exe.
TUNE_NEUTRAL_GAIN = 18.0      # family[18] is the identity
TUNE_GAIN_MAX = 35.0          # the engine clamps here, and the family ends at 36
TUNE_SPLIT = 471              # where the shadow gain hands over to the highlight one
TUNE_TABLE_LEN = 1025
TUNE_TABLE_MAX = 65535.0      # the table's full scale is 65536, which a u16 cannot hold
TUNE_TABLE_STEP = 64          # table entries per unit of index


def look_index(style: str) -> int | None:
    return LOOK_ORDER.index(style) if style in LOOK_ORDER else None


@lru_cache(maxsize=1)
def _family() -> np.ndarray:
    """The 37 static operator curves, (37, 1025) over curve output in [0, 65536)."""
    with np.load(_DATA / "tone_family.npz") as z:
        return z["family"].astype(np.float64)


def _operator_table(highlights: float, shadows: float, contrast: float) -> np.ndarray:
    """One operator table for a whole set of tweaks, exactly as the engine builds it.

    The two halves take different gains but share the family, so a tweak never
    acts on what another tweak produced — they meet as a sum inside the gain.
    """
    family = _family()
    table = np.empty(TUNE_TABLE_LEN)
    for gain, lo, hi in ((TUNE_NEUTRAL_GAIN + contrast - shadows, 0, TUNE_SPLIT),
                         (TUNE_NEUTRAL_GAIN + contrast + highlights,
                          TUNE_SPLIT, TUNE_TABLE_LEN)):
        g = float(np.clip(gain, 0.0, TUNE_GAIN_MAX))
        k = int(g)
        blend = np.float32(g - k)
        lower, upper = family[k, lo:hi], family[k + 1, lo:hi]
        mixed = lower + (upper - lower) * blend
        table[lo:hi] = np.clip(np.trunc(mixed.astype(np.float32) + np.float32(0.5)),
                               0.0, TUNE_TABLE_MAX)
    return table


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


def apply_tuning(curve: np.ndarray, style: str, highlights: int = 0, shadows: int = 0,
                 contrast: int = 0) -> np.ndarray:
    """Run the in-camera tone tweaks over a factory curve.

    This is the engine's own construction rather than a fit to it: one table
    over the curve's output, built by _operator_table, then looked up with the
    engine's own integer steps. Checked against Edit.exe on two bodies over 51
    renders — every setting of Highlights, Shadows and Contrast, singly and in
    pairs, on twelve looks — the resulting 32768-entry LUT is bit-identical.

    Reaching that took replacing two earlier models, and both failures are worth
    remembering. Adding each tweak's measured delta independently misses a
    Highlights-with-Contrast pair by 152/16384; applying them one after another
    misses it by 111. Neither is what the engine does, because the tweaks are
    summed into a gain *before* a single table is built, so there is no order to
    get right and nothing for a second tweak to act on.

    Out-of-range settings are the one deliberate divergence. Edit.exe refuses
    them outright — +-10 renders identically to 0 — which is validation on a
    number the body can never write, not a claim that the curve stops there. So
    this passes them through, and what bounds them is the engine's own clamp on
    the gain rather than a rule of ours.

    `style` selects nothing — a tweak has no idea which look it is riding on —
    and is kept because callers name the look anyway.
    """
    limit = TUNE_EXTRAPOLATION_LIMIT
    table = _operator_table(*(float(np.clip(v, -limit, limit))
                              for v in (highlights, shadows, contrast)))

    scaled = np.trunc(np.clip(curve, 0.0, 1.0) * CURVE_Y_FULL).astype(np.int64) >> 2
    index = scaled >> 6
    # At the top the engine reads the last entry for both ends rather than
    # stepping off it, which pins the result flat there instead of interpolating.
    top = index >= TUNE_TABLE_LEN - 2
    lower = table[np.where(top, TUNE_TABLE_LEN - 2, index)].astype(np.float32)
    upper = table[np.where(top, TUNE_TABLE_LEN - 2, index + 1)].astype(np.float32)
    blend = (scaled - (index << 6)).astype(np.float32) / np.float32(TUNE_TABLE_STEP)
    mixed = lower * (np.float32(1.0) - blend) + upper * blend
    return (np.trunc(mixed).astype(np.int64) >> 2) / TONE_OUTPUT_FULL


def tone_curve(
    cal: LookCalibration, style: str, highlights: int = 0, shadows: int = 0,
    contrast: int = 0, n: int = TONE_INDEX_WHITE + 1,
) -> np.ndarray:
    """A look's complete display-encoded curve for one shot's settings."""
    return apply_tuning(base_curve(cal, n), style, highlights, shadows, contrast)
