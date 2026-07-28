"""Sony's in-camera sharpening, as `ZcTaskSIMDSharpness` runs it.

Unlike the six Creative Look tweaks, Sharpness is a camera setting of its own
(MakerNotes `0x2006`, with `0x2035` SharpnessRange beside it). It is the last
thing but one the engine does to a frame — `Sharpness -> Spica -> Marble` — so
in the browser it belongs with Clarity, as a post-pass on the finished image
rather than as anything the tone curve could carry.

The operator, read out of Edit.exe 4.0.00.10311 (PIPELINE.md 7.11):

    BIN = [1, 6, 15, 20, 15, 6, 1]                     # separable, sums to 4096
    hp  = 25.6*y - 10.24*blur(y) - 3.84*sum(4-neighbours)
    d   = |hp| < deadzone ? 0 : floor(amp * hp)        # a hard threshold, not coring
    out = clamp(y + d, 0, 32767)

`hp` is a plain linear high-pass whose taps sum to zero; the only non-linearity
is the dead zone, and it is compared against `hp` itself rather than against
`amp * hp`, so turning the strength up never lets a new pixel through. That is
what keeps Sony's sharpening off flat areas however hard it is pushed.

Reproducing that form bit-for-bit against the engine's own tiles gives 99.987%
identical pixels. The shader ships a float port of it that drops the `floor`
and clamps at 16383 rather than 32767 — both differences are below one 8-bit
level, measured in `sony_repro/tools/sharp_shader_check.py`. **The kernel itself
lives in the shader**, not here: it is the operator's own shape rather than
anything per-shot, so `rendering/passes.ts` owns those numbers and this module
owns only what varies per frame.

What varies is one amplitude, and computing it offline is what sets this stage
apart from Clarity: the single calibration term comes from the RAW's own tag
`0x78cd` rather than from the camera body. Of 22 frames checked, the 20 whose
tag exceeds the base reproduce the engine's value bit-for-bit; the other two sit
at or below it, where the parser's branch does not run and the engine keeps its
initialised 1.0 — so those two confirm the fallback rather than the formula.

Two things this reproduction does *not* get right. Both go onto the profile's
`limitations` list (profile.SHARPEN_NOTE) rather than staying in this comment,
so a consumer can surface them — though note that nothing in the frontend
renders that list today, so for now they travel without being seen:

* Spica, the other half. `SharpnessRange` splits the effect between this stage
  (coarse, the 7x7 binomial above) and `ZcTaskSpica` (fine, directional
  filtering with a texture classifier), with complementary weights. At the
  camera's default range of +3 both run at full strength, and Spica's two core
  filters have not been read, so the fine end is missing.
* Resolution. The kernel reaches three *sensor* pixels, so it is only the
  engine's operator at full resolution; a downscaled render sharpens at its own
  scale instead. Clarity does not have this problem — its radius is a fraction
  of the frame, so it survives being rendered small.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

from .sr2 import read_sr2_tag

# The engine's own range for each setting. Both ladders start at zero; exiftool
# prints them with a leading plus ("+4", "+3"), which is a display convention
# and not a sign — there is no negative half to either.
SHARPNESS_MIN = 0
SHARPNESS_MAX = 9
# The engine's own default, and where it sends any value outside the range: the
# same input validation as the Creative Look tweaks (PIPELINE.md 6.2).
SHARPNESS_DEFAULT = 4
SHARPNESS_RANGE_MIN = 0
SHARPNESS_RANGE_MAX = 5
# Unity on the amplitude, and what the body writes unless told otherwise — so a
# shot whose range cannot be read renders at the strength Sony intended.
SHARPNESS_RANGE_DEFAULT = 3

# `lvl = 10 * (sharpness - SHARPNESS_DEFAULT)` and `rng = 10 * range + 20`, the
# two ladders measured by sweeping the MakerNotes tag a step at a time
# (sony_repro/tools/sharp_sweep.py). Both are exact, not fitted.
SHARPNESS_STEP = 10
SHARPNESS_RANGE_STEP = 10
SHARPNESS_RANGE_BASE = 20
# The engine divides by this, so a range of +3 (rng = 50) is unity — which is
# why the camera's default neither boosts nor cuts the strength.
SHARPNESS_RANGE_UNITY = 50.0
SHARPNESS_AMP_SCALE = 1024.0

# SR2SubIFD tag holding this shot's sharpening calibration, and the affine map
# the calibration parser applies to it. Values at or below the base are left
# alone: that branch does not run and the engine keeps its initialised 1.0.
SHARPNESS_CALIB_TAG = 0x78CD
SHARPNESS_CALIB_BASE = 0x100
SHARPNESS_CALIB_QUANTUM = 0.003125       # 1/320
SHARPNESS_CALIB_DEFAULT = 1.0


def clamp_sharpness(value: int) -> int:
    """The engine's own input validation: out of range renders as the default.

    Note this is *not* a clamp to the nearest end — a value of 30 renders like
    +4, not like +9. Sony treats anything off the ladder as a corrupt tag.
    """
    value = int(value)
    return value if SHARPNESS_MIN <= value <= SHARPNESS_MAX else SHARPNESS_DEFAULT


def clamp_sharpness_range(value: int) -> int:
    """Out of range turns the stage off outright, rather than falling back.

    The two settings differ here and it is not an oversight: an invalid range
    leaves `opts[0x2c4]` at zero, and zero flows into the amplitude as a factor,
    so the whole stage multiplies out. An invalid Sharpness lands on a table
    index instead, which has a default to fall back to.
    """
    value = int(value)
    return value if SHARPNESS_RANGE_MIN <= value <= SHARPNESS_RANGE_MAX else -1


def sharpness_amount(sharpness: int, sharpness_range: int,
                     calibration: float = SHARPNESS_CALIB_DEFAULT) -> float:
    """The detail gain for a setting, as the shader wants it.

        amp = (lvl + 100) * 0.5 * calib[0x1060] * (rng / 50) / 1024

    At the camera's defaults (+4 / +3) with a calibration of 1.7 that is
    85/1024 = 0.0830078125, the constant Edit.exe's own tiles were reproduced
    against.

    Zero means the stage multiplies out, which happens only when the range tag
    is unreadable or off its ladder — the camera has no "off" position, and
    even Sharpness +0 still sharpens.
    """
    rng = clamp_sharpness_range(sharpness_range)
    if rng < 0:
        return 0.0
    lvl = SHARPNESS_STEP * (clamp_sharpness(sharpness) - SHARPNESS_DEFAULT)
    steps = SHARPNESS_RANGE_STEP * rng + SHARPNESS_RANGE_BASE
    return ((lvl + 100) * 0.5 * float(calibration)
            * (steps / SHARPNESS_RANGE_UNITY) / SHARPNESS_AMP_SCALE)


def sharpness_block(sharpness: int, sharpness_range: int,
                    calibration: float = SHARPNESS_CALIB_DEFAULT) -> dict[str, Any]:
    """What the shader needs, plus the two settings behind it.

    `amount` is the only number the shader wants — the kernel and its dead zone
    are the operator's own and live there. The two ladder positions ride along
    for the panel rather than for the render: they are what the body's menu
    showed, and `amount` alone cannot be shown to anyone, being a high-pass
    scale on a 14-bit plane. They are the *rendered* values, so a tag off its
    ladder reports the default this actually used and not the corrupt number.

    The block rides through a profile rebuild untouched — no Creative Look
    slider can change a camera setting — which is why apply_look_overrides
    carries this dict forward rather than rebuilding it, exactly as it does for
    the DRO grid.
    """
    return {
        "amount": sharpness_amount(sharpness, sharpness_range, calibration),
        "level": clamp_sharpness(sharpness),
        "range": clamp_sharpness_range(sharpness_range),
    }


def sharpness_calibration(path: str | Path) -> float:
    """`calib[0x1060]` for this shot, from the RAW's own tag `0x78cd`.

    Falls back to the engine's initialised 1.0 for anything this cannot read —
    a non-Sony file, or a frame whose tag sits at or below the base, which is
    the branch the calibration parser skips. Both mean "no adjustment", so
    there is nothing to report to the caller.
    """
    try:
        st = Path(path).stat()
    except OSError:
        return SHARPNESS_CALIB_DEFAULT
    return _cached_calibration(str(path), st.st_size, int(st.st_mtime_ns))


@lru_cache(maxsize=8)
def _cached_calibration(path: str, size: int, mtime: int) -> float:
    # Keyed on the file's identity like sr2._cached_dro_curve: reaching the tag
    # means decrypting the SR2 block, and every profile rebuild asks for it.
    try:
        raw = read_sr2_tag(path, SHARPNESS_CALIB_TAG)
    except Exception:
        # The SR2 walk can fail anywhere on a file that is not a Sony RAW, not
        # only at the missing-tag check. The answer for such a file is the
        # default, not an exception.
        return SHARPNESS_CALIB_DEFAULT
    if len(raw) < 2:
        return SHARPNESS_CALIB_DEFAULT
    # read_sr2_tag hands back the tag's bytes without its type or the file's
    # byte order, both of which _decrypted_sr2 knew and dropped. Every ARW seen
    # is little-endian ("II") with this tag as a SHORT, which is the assumption
    # sharp_verify.py made across 22 frames; a big-endian container would need
    # the endianness threaded out of sr2 rather than assumed here.
    value = int.from_bytes(raw[:2], "little")
    if value <= SHARPNESS_CALIB_BASE:
        return SHARPNESS_CALIB_DEFAULT
    return (value - SHARPNESS_CALIB_BASE) * SHARPNESS_CALIB_QUANTUM + 1.0
