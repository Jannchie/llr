"""Sony's in-camera Clarity, as `ZcTaskSIMDMarble` runs it.

Clarity is the sixth Creative Look tweak (MakerNotes `0x2036`, exiftool
`Clarity`, range -9..+9). It is the only one of the six that is *spatial*, which
is why it does not ride on the tone curve like the other five: the engine builds
a heavily blurred base layer and adds back a scaled copy of what the image has
that the base layer does not.

The whole operator, read out of Edit.exe 4.0.00.10311 (see
sony_repro/PIPELINE.md 7.9.1):

    clr  = max(0, 10 * clarity)          # negative turns the stage off outright
    base = upsample8(gauss3(edge_mean(downsample8(luma))))
    d    = luma - base
    out  = luma + rolloff(luma) * AMP[clarity] * d

Every constant above lives in the *camera calibration*, not in the file, so the
tables here are baked from a live dump (`sony_repro/tools/clarity_calib.py`) of
CLARITY_CALIBRATION_BODY. Another body's tables will differ, and there is no way
to read them offline — `_limitations` puts that on the profile rather than
leaving it in this comment. `AMP` is indexed by the setting itself: `clr` is only ever a
multiple of ten, so the engine's interpolation between neighbouring table
entries never actually interpolates.

Nothing here touches pixels. Like the tone curve and DRO, Clarity is applied in
the browser — the shader owns the blur chain (rendering/passes.ts) and this
module owns the numbers it needs.
"""

from __future__ import annotations

# The body every constant below was dumped from. Kept as data, not prose: it is
# what a limitation line has to name when the shot came from something else.
CLARITY_CALIBRATION_BODY = "ILCE-7CM2"

# calib[0x1140], ten entries indexed by the setting. The engine's own units are
# 1/1024 of the detail difference, applied at the middle of the tone range.
#
# calib[0x1154] (the table for detail *below* the base layer) came back
# identical on that body, so one table serves both signs; if a body ever splits
# them this becomes two.
CLARITY_AMP = (0, 32, 108, 184, 260, 336, 412, 488, 564, 646)
CLARITY_AMP_SCALE = 1024.0

# The range offered is Edit's own 清晰 slider: 0..100, and its value *is* the
# engine's `clr` (settings +0x2c0 holds it as typed; the camera's tag lands
# there as `10 * clarity`, measured in sony_repro/notes/panel-sliders.md). The
# table above is indexed at clr/10 and interpolated between entries — the
# camera's stops never needed that, Edit's units in between do. The tag's
# negative side is not offered: `max(0, ...)` renders it exactly like 0, so it
# would be stops that cannot change a pixel. Past the table's last entry (90)
# the amount holds — the engine's own behaviour there was not measured.
CLARITY_MIN = 0
CLARITY_MAX = 100
CLARITY_PANEL_PER_STOP = 10

# calib[0x1174]: the range threshold of the edge-aware mean, on the engine's
# 16-bit plane. Samples further than this from the centre are dropped, which is
# what keeps the base layer from crossing edges.
CLARITY_EDGE_THRESHOLD = 96 / 65472.0

# calib[0x1176]: how much of the centre pixel survives the 3x3 Gaussian, in
# 1/256. Zero on this body, i.e. the Gaussian is used neat.
CLARITY_CENTER_MIX = 0 / 256.0

# The engine's downsample factor for the base layer, and the Gaussian it then
# runs on that scale. Together they put the blur radius at ~24 source pixels.
CLARITY_DOWNSAMPLE = 8

# calib[0x1168]/[0x1169] are both mode 2 on this body, which makes the rolloff
# `min(4Y, 4(1 - Y), 0.5) / 0.5` on normalised luma: full strength through the
# midtones, tapering to nothing in the last eighth at either end. That taper is
# the whole reason Clarity does not halo against a bright sky.
CLARITY_ROLLOFF_KNEE = 0.125


def clamp_clarity(value: float) -> int:
    """Into the offered range, on Edit's 0..100 scale. A negative — from an
    older file, or a request written by hand — lands on 0, which is what it
    rendered as anyway."""
    return max(CLARITY_MIN, min(CLARITY_MAX, int(value)))


def clarity_amount(clarity: float) -> float:
    """The detail gain for a 清晰 value (0..100), as the shader wants it (0 = no-op).

    Mirrors the engine's own lookup: `clr` indexes the table at clr/10 and
    blends the two entries either side. The camera's stops (multiples of ten)
    land exactly on an entry; Edit's units in between do not.
    """
    x = clamp_clarity(clarity) / CLARITY_PANEL_PER_STOP
    last = len(CLARITY_AMP) - 1
    k = min(int(x), last - 1)
    amp = CLARITY_AMP[k] + (CLARITY_AMP[k + 1] - CLARITY_AMP[k]) * min(x - k, 1.0)
    return amp / CLARITY_AMP_SCALE
