"""Edit.exe's ten built-in DRO curves, and the level scale that indexes them.

Auto DRO needs none of this: the body picks a curve at capture and writes it
into the RAW, and sr2.dro_curve rebuilds it exactly. These presets are the other
half — what the engine uses when the level is set by hand, and what it falls
back to when the RAW carries no curve at all.

The curve builder (FUN_14018ad50, preset branch at 0x14018afce) reads a table of
ten records at `param + 0x13c`, stride 0x98 = 38 floats = 10 knots + 28 control
points, and interpolates between two of them:

    n  = (level == -1) ? 50 : level
    i0 = n / 10                       # magic-number division, so integer
    i1 = (i0 < 9) ? i0 + 1 : 9
    u  = (i0 == i1) ? 0.5 : (n % 10) / 10
    out[k] = u * P[i1][k] + (1 - u) * P[i0][k]

Knots and control points are blended alike, then sampled by the same Bezier that
serves the RAW's own curve. So the level is not an index — it is a position on a
0..99 scale where every tenth value lands exactly on a stored preset.

The table lives in a runtime structure rather than .rdata: nothing in .text
references it RIP-relative, so it was read out of the live process
(sony_repro/tools/dro_presets.py) and is bit-identical across calls and frames,
which is what makes it safe to bake in here.

Shadow lift of each preset, in stops — a smooth ladder, linear at 0.175/step for
the first six and accelerating after:

    P0 0.250  P1 0.425  P2 0.600  P3 0.775  P4 0.950
    P5 1.125  P6 1.346  P7 1.624  P8 1.919  P9 2.222
"""

from __future__ import annotations

import numpy as np

from .sr2 import sample_dro_curve

# What the engine renders when the body says Auto but the RAW carries no curve
# (FUN_140175830: `level = calib[0x10f0] == 0 ? 5 : -1`). Level 5 is halfway
# between P0 and P1, a 0.337-stop lift. Distinct from the builder's own guard
# below, which this path never reaches because it has already replaced the -1.
DRO_LEVEL_NO_CURVE = 5
# The builder's guard for a level still unset by the time it runs: -1 becomes 50,
# i.e. preset 5 exactly. Recorded for completeness; nothing here selects it.
DRO_LEVEL_UNSET = 50
# The engine's "no manual level, use what the body wrote" sentinel.
DRO_LEVEL_AUTO = -1
DRO_LEVEL_MAX = 99
DRO_PRESET_COUNT = 10

# Five evenly spaced stops on the engine's level scale, used for the manual
# Lv1..Lv5 control. Each lands exactly on a stored preset, so no pair of levels
# we offer needs interpolation. Sony's own Lv labels could not be pinned to
# these values offline — the corpus holds only Off and Auto frames, and reading
# them off Imaging Edge would mean driving its UI — so treat the numbering as
# our quantisation of the engine's scale, not as a claim about Imaging Edge's.
DRO_UI_LEVELS = (10, 30, 50, 70, 90)

_PRESET_KNOTS = (
    (0.0, 0.000977, 1.000977, 5.304688, 6.304688, 6.305664, 9.646484, 10.646484, 12.0, 13.0),
    (0.0, 0.000977, 1.000977, 5.129688, 6.129688, 6.130664, 9.646484, 10.646484, 12.0, 13.0),
    (0.0, 0.000977, 1.000977, 4.954688, 5.954688, 5.955664, 9.646484, 10.646484, 12.0, 13.0),
    (0.0, 0.000977, 1.000977, 4.779688, 5.779688, 5.780664, 9.646484, 10.646484, 12.0, 13.0),
    (0.0, 0.000977, 1.000977, 4.604688, 5.604688, 5.605664, 9.646484, 10.646484, 12.0, 13.0),
    (0.0, 0.000977, 1.000977, 4.429688, 5.429688, 5.430664, 9.646484, 10.646484, 12.0, 13.0),
    (0.0, 0.000977, 1.000977, 4.229688, 5.229688, 5.230664, 9.646484, 10.646484, 12.0, 13.0),
    (0.0, 0.000977, 1.000977, 4.029688, 5.029688, 5.030664, 9.646484, 10.646484, 12.0, 13.0),
    (0.0, 0.000977, 1.000977, 3.829688, 4.829688, 4.830664, 9.646484, 10.646484, 12.0, 13.0),
    (0.0, 0.000977, 1.000977, 3.629688, 4.629688, 4.630664, 9.646484, 10.646484, 12.0, 13.0),
)

_PRESET_CTRL = (
    (0.0, 0.0, 0.0, 0.000977, 0.333984, 0.666992, 1.016602, 2.518555, 4.036133, 5.538086,
     5.887695, 6.220703, 6.554688, 6.554688, 6.554688, 6.554688, 7.585938, 8.616211,
     9.665039, 9.979492, 10.3125, 10.646484, 11.097656, 11.548828, 12.0, 12.333008,
     12.666016, 13.0),
    (0.0, 0.0, 0.0, 0.000977, 0.333984, 0.666992, 1.030274, 2.518555, 4.036133, 5.524414,
     5.887695, 6.220703, 6.554688, 6.554688, 6.554688, 6.554688, 7.585938, 8.616211,
     9.675586, 9.979492, 10.3125, 10.646484, 11.097656, 11.548828, 12.0, 12.333008,
     12.666016, 13.0),
    (0.0, 0.0, 0.0, 0.000977, 0.333984, 0.666992, 1.043946, 2.518555, 4.036133, 5.510742,
     5.887694, 6.220703, 6.554688, 6.554688, 6.554688, 6.554688, 7.585938, 8.616211,
     9.686132, 9.979492, 10.3125, 10.646484, 11.097656, 11.548828, 12.0, 12.333008,
     12.666016, 13.0),
    (0.0, 0.0, 0.0, 0.000977, 0.333984, 0.666992, 1.057617, 2.518555, 4.036133, 5.497071,
     5.887694, 6.220703, 6.554688, 6.554688, 6.554688, 6.554688, 7.585938, 8.616211,
     9.696680, 9.979492, 10.3125, 10.646484, 11.097656, 11.548828, 12.0, 12.333008,
     12.666016, 13.0),
    (0.0, 0.0, 0.0, 0.000977, 0.333984, 0.666992, 1.071289, 2.518555, 4.036133, 5.483399,
     5.887695, 6.220703, 6.554688, 6.554688, 6.554688, 6.554688, 7.585938, 8.616211,
     9.707227, 9.979492, 10.3125, 10.646484, 11.097656, 11.548828, 12.0, 12.333008,
     12.666016, 13.0),
    (0.0, 0.0, 0.0, 0.000977, 0.333984, 0.666992, 1.084961, 2.518555, 4.036133, 5.469727,
     5.887695, 6.220703, 6.554688, 6.554688, 6.554688, 6.554688, 7.585938, 8.616211,
     9.717773, 9.979492, 10.3125, 10.646484, 11.097656, 11.548828, 12.0, 12.333008,
     12.666016, 13.0),
    (0.0, 0.0, 0.0, 0.000977, 0.333984, 0.666992, 1.071289, 2.518555, 4.036133, 5.566544,
     5.927695, 6.220703, 6.534688, 6.554688, 6.554688, 6.554688, 7.585938, 8.616211,
     9.707227, 9.979492, 10.3125, 10.646484, 11.097656, 11.548828, 12.0, 12.333008,
     12.666016, 13.0),
    (0.0, 0.0, 0.0, 0.000977, 0.333984, 0.666992, 1.057617, 2.518555, 4.036133, 5.663360,
     5.967695, 6.220703, 6.514688, 6.554688, 6.554688, 6.554688, 7.585938, 8.616211,
     9.696680, 9.979492, 10.3125, 10.646484, 11.097656, 11.548828, 12.0, 12.333008,
     12.666016, 13.0),
    (0.0, 0.0, 0.0, 0.000977, 0.333984, 0.666992, 1.043946, 2.518555, 4.036133, 5.760176,
     6.007695, 6.220703, 6.494688, 6.554688, 6.554688, 6.554688, 7.585938, 8.616211,
     9.686132, 9.979492, 10.3125, 10.646484, 11.097656, 11.548828, 12.0, 12.333008,
     12.666016, 13.0),
    (0.0, 0.0, 0.0, 0.000977, 0.333984, 0.666992, 1.030274, 2.518555, 4.036133, 5.856992,
     6.047695, 6.220703, 6.474688, 6.554688, 6.554688, 6.554688, 7.585938, 8.616211,
     9.675586, 9.979492, 10.3125, 10.646484, 11.097656, 11.548828, 12.0, 12.333008,
     12.666016, 13.0),
)

_KNOTS = np.array(_PRESET_KNOTS, dtype=np.float64)
_CTRL = np.array(_PRESET_CTRL, dtype=np.float64)


def dro_preset_curve(level: int) -> np.ndarray:
    """The tone curve the engine builds for a manual DRO level.

    Same 104 samples of output against input log2 luma that sr2.dro_curve
    returns for an Auto shot, so the two are interchangeable downstream.
    """
    n = int(max(0, min(DRO_LEVEL_MAX, level)))
    i0 = n // 10
    i1 = i0 + 1 if i0 < DRO_PRESET_COUNT - 1 else DRO_PRESET_COUNT - 1
    # The engine's u for the pinned-at-the-top case is 0.5, which is harmless
    # there because it blends P9 with itself. Copied rather than simplified so
    # the two implementations read alike.
    u = 0.5 if i0 == i1 else (n % 10) / 10.0
    return sample_dro_curve(_KNOTS[i1] * u + _KNOTS[i0] * (1 - u),
                            _CTRL[i1] * u + _CTRL[i0] * (1 - u))
