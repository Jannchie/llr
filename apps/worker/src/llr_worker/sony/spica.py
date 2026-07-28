"""Sony's fine sharpening half, as `ZcTaskSIMDSpica` runs it.

Sharpness (sharpness.py) and this stage are the two ends of one control. The
body's SharpnessRange splits the effect between them with complementary
weights: range up sends it to the coarse 7x7 binomial, range down sends it
here. At the camera's default of +3 both run at full strength, so a render
without this stage is missing half of Sony's sharpening — which is what
SHARPEN_NOTE used to say, and no longer has to.

The operator, read out of Edit.exe 4.0.00.10311 (PIPELINE.md 7.11.4.9) and
reproduced bit-for-bit against 8.1M of the engine's own pixels:

    lo, hi = min/max over a 9-point cross, +-2 pixels
    M      = 9 bits, one per cross point: (p - lo) >= (hi - lo) / 2
             bit 8 set -> M = ~M & 0xff        (fold, so the 9 bits index 256)
    idx, dx, dy = LUT[M]                       (100 shapes x 4 mirrorings)
    S      = sum of 25 weights over a radius-3 diamond, sampled through (dx, dy)
    detail = S - 512 * centre
    G      = min(T_detail, T_range, T_mid) * 2
    tmp    = centre - trunc(G * detail / 262144)
    out    = tmp * w + centre * (1 - w)

`M` is a local binary pattern: which of the nine cross points sit in the upper
half of the local range. Two patterns that are mirror images of each other get
the same weight table and differ only in how the diamond is sampled, which is
why 512 codes collapse to 100 tables. That is the whole classifier — there is
no edge angle estimated anywhere, only a bit pattern looked up.

`T_*` are trapezoids: flat outside `[a, d]`, ramping to a plateau over `[b, c]`.
Three of them, on the detail magnitude, on the local range, and on the local
midpoint, and the gain is the *minimum* of the three. That is what makes this
stage back off on flat areas, on very high contrast, and in the deep shadows,
without any of the three knowing about the others.

**The blend at the end is the whole difference between this stage and
sharpening.** The filter writes into a scratch buffer; a second pass mixes that
buffer back over the original by `w`. Modelling it as `centre - trunc(w * ...)`
instead of `round(w * tmp + (1 - w) * centre)` gets the amplitude right and
still misses a quarter of the pixels by one level.

What this port does not carry, and why it is still the same operator:

* The trapezoids and the weight tables are read from the engine's config rather
  than from the RAW, so they are one body's (ILCE-7CM2). Same standing as
  Clarity's amplitudes — the shape is right, another body may scale it.
* `trunc` on a 14-bit plane, and the integer classifier thresholds, are dropped
  in the shader's float port, which works on the display-encoded frame. See
  SPICA_NOTE for the part of that a consumer should be told about.
"""

from __future__ import annotations

from typing import Any

from .sharpness import (
    SHARPNESS_DEFAULT,
    SHARPNESS_RANGE_BASE,
    SHARPNESS_RANGE_STEP,
    SHARPNESS_RANGE_UNITY,
    SHARPNESS_STEP,
    clamp_sharpness,
    clamp_sharpness_range,
)

# `cfg[0x0c]`, the constant the two setting ladders are multiplied by. Half,
# which is why a default frame blends the filtered buffer and the original
# 50/50 — and why leaving the blend out doubles the effect.
SPICA_BLEND_SCALE = 0.5
# `st[0x208] + 100` over this. The same strength term sharpening uses, so the
# two stages track each other as the Sharpness setting moves.
SPICA_LEVEL_UNITY = 100.0

# `cfg[0x08]`: below this local range the engine skips the classifier outright
# and takes table 0 — the one isotropic shape in the family. On the engine's
# own 14-bit plane, so eight levels out of 16383.
SPICA_RANGE_THRESHOLD = 8
# `cfg[0xc4] / 2048`. The trapezoids produce a number in [48, 512]; this turns
# it into the gain the detail is multiplied by.
SPICA_GAIN_SCALE = 2.0
# The two right shifts between the weighted sum and the output: the weights sum
# to 512, and the gain above is on a 512 scale as well.
SPICA_DETAIL_SHIFT = 512.0

# The three trapezoids, `(a, b, c, d, outside, inside)`, evaluated as in
# Edit.exe's 0x35a1d0: `x < a` or `x >= d` gives `outside`, `[b, c]` gives
# `inside`, and the two flanks interpolate.
#
# Note `b > c` on the last two. That is not a transcription error: it makes the
# plateau unreachable, so those two are a single ramp from `outside` at `a` to
# `inside` at `b` and back to `outside` past `d`. Evaluate them by the engine's
# branch order rather than by testing intervals, or the unreachable plateau
# starts answering.
SPICA_CURVE_DETAIL = (512.0, 2048.0, 2304.0, 3840.0, 128.0, 512.0)
SPICA_CURVE_RANGE = (128.0, 2602.666748046875, 2048.0, 3904.0, 48.0, 512.0)
SPICA_CURVE_MID = (0.0, 10752.0, 3072.0, 5760.0, 176.0, 512.0)
# The detail curve is indexed by `|detail| / 128`, the other two by raw 14-bit
# range and midpoint.
SPICA_DETAIL_DIVISOR = 128.0

# The one thing here that is neither a camera setting nor an engine constant:
# `0x35a450(ISO, preset)` scales the detail before any of it, so high ISO gets
# less fine sharpening. Three breakpoints and three values, read straight out
# of the preset's config block; the first segment is flat at 1.0 because its
# two endpoints are equal, so only the second one bends.
#
# Confirmed both ways: reading the config gives 400/1600/25600 -> 1/1/0.75, and
# solving for the factor from the engine's own pixels across twelve ISOs gives
# 1.0 from ISO 100 to 1600 and 0.975 at ISO 4000 — which is what this returns.
SPICA_ISO_KNEE = 1600.0
SPICA_ISO_FLOOR_AT = 25600.0
SPICA_ISO_FLOOR = 0.75


def spica_iso_gain(iso: float | None) -> float:
    """The detail scale for a shot's ISO. 1.0 below the knee, 0.75 past the top.

    An unreadable ISO renders at 1.0 — the value every frame below the knee
    gets, so it is the engine's answer for most shots rather than an invented
    neutral.
    """
    if iso is None or iso <= SPICA_ISO_KNEE:
        return 1.0
    if iso >= SPICA_ISO_FLOOR_AT:
        return SPICA_ISO_FLOOR
    t = (float(iso) - SPICA_ISO_KNEE) / (SPICA_ISO_FLOOR_AT - SPICA_ISO_KNEE)
    return 1.0 * (1.0 - t) + SPICA_ISO_FLOOR * t


def spica_amount(sharpness: int, sharpness_range: int) -> float:
    """How much of the filtered buffer survives the blend, 0 = the stage is off.

        w = (100 - st[0x2c4]) / 50 * (st[0x208] + 100) / 100 * 0.5

    with the same two ladders sharpening uses: `st[0x2c4] = 10 * range + 20` and
    `st[0x208] = 10 * (sharpness - 4)`. At the camera's defaults (+4 / +3) that
    is exactly 0.5, the value Edit.exe's own tiles were reproduced against.

    Complementary to sharpness_amount by construction, so range +5 leaves this
    stage at 0.6 of the strength term rather than at zero — the split moves the
    effect between the two ends, it never turns either off.
    """
    rng = clamp_sharpness_range(sharpness_range)
    # An out-of-range tag leaves `opts[0x2c4]` at zero. For sharpening that
    # zeroes the amplitude; here the term is `100 - opts[0x2c4]`, so the same
    # corrupt tag sends this stage to double weight instead. Faithful to the
    # engine, and the reason this does not share sharpening's early return.
    steps = SHARPNESS_RANGE_STEP * rng + SHARPNESS_RANGE_BASE if rng >= 0 else 0
    lvl = SHARPNESS_STEP * (clamp_sharpness(sharpness) - SHARPNESS_DEFAULT)
    return ((100.0 - steps) / SHARPNESS_RANGE_UNITY
            * (lvl + 100.0) / SPICA_LEVEL_UNITY * SPICA_BLEND_SCALE)


def spica_block(sharpness: int, sharpness_range: int,
                iso_gain: float = 1.0) -> dict[str, Any]:
    """What the shader needs, as it travels on the profile.

    Two numbers. Everything else — the weight tables, the classifier LUT, the
    trapezoids — is the operator's own shape rather than anything per-shot, so
    it lives in the shader beside sharpening's kernel for the same reason.

    Rides through a profile rebuild untouched, like sharpness_block: no Creative
    Look slider can move a camera setting.
    """
    return {
        "amount": spica_amount(sharpness, sharpness_range),
        "isoGain": float(iso_gain),
    }


def spica_off() -> dict[str, Any]:
    """The block for a file whose settings could not be read.

    Not `spica_block(0, -1)`: that is sharpening's way of spelling "off", and
    it does not transfer. An unreadable range zeroes sharpening's amplitude but
    *doubles* this stage's weight (see spica_amount), so running the formula on
    a missing tag would sharpen a file harder than any real setting can.
    """
    return {"amount": 0.0, "isoGain": 1.0}
