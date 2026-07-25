"""Render camera RGB the way Sony Imaging Edge does.

The Sony chain that matters for colour is two steps, and both are now reproduced
bit-exactly (see ../../../../sony_repro/PIPELINE.md):

    SIMDLinearMatrix16   hue-segmented 3x3, calibration read from the ARW
    MainGamma            one per-channel 1D LUT, one curve per Creative Look

They map cleanly onto this pipeline's two halves. The matrix replaces the DCP
colour step and runs here; the curve replaces the view transform and runs in the
browser, shipped as `profileToneCurve` exactly like a DCP's own curve.

Both are read out of the shot itself: the body writes calibration for all ten
Creative Looks into every frame, so switching looks needs no extra data and no
baked-in tables. See sr2.look_calibrations and tone.py.

Two facts make the swap safe (both measured against the running engine):

* Sony feeds its matrix the *same* pixels as `postprocess_camera_native()` —
  median relative difference 0.20%, per-channel gains within 1%. So `camera_rgb`
  goes straight in with no conversion.
* The matrix output sits on near-Rec.709 primaries: all 16 knot matrices hug the
  identity (diagonal 0.96..1.03, off-diagonal within +-0.17). Sony has no
  separate camera-to-standard-space matrix at all, which is the whole reason
  this path beats routing through XYZ.

Against a correctly-matched DCP profile the two trade places depending on the
frame — this path wins on RMSE and on shadow rendering, the DCP sometimes wins
on hue angle. What it does not yet reproduce is the saturation the engine adds
in its later YCC stages, which reads as an overall flatness next to the
in-camera JPEG.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import numpy as np

from ..dcp import D50_TO_D65, XYZ_D50_TO_PROPHOTO, XYZ_D65_TO_SRGB
from .linear_matrix import SegmentedMatrix
from .sr2 import LookCalibration, look_calibrations, unpack_param_block
from .tone import LOOK_ORDER, look_index, tone_curve

# Sony's tone LUT is indexed in units of camera RGB x 8192 and its output is
# 1/16384 of full scale. The white point was pinned four independent ways.
TONE_INDEX_WHITE = 8192
TONE_OUT_SCALE = 16384.0

# Points shipped to the frontend. The browser resamples them onto its 2048-entry
# LUT with a monotone spline; at 1024 the round trip costs under 0.05/255.
TONE_CURVE_POINTS = 1024

@dataclass
class SonyRenderInfo:
    """Mirrors DcpRenderInfo's JSON shape so the frontend consumes both alike."""

    style: str
    tone_curve: list[list[float]]
    limitations: list[str]
    working_space: str = "linear-prophoto-d50"

    def to_json(self) -> dict[str, Any]:
        return {
            "kind": "sony",
            "name": f"Sony Imaging Edge ({self.style})",
            "matrix": "LinearMatrix16 (hue-segmented, from the RAW's own calibration)",
            "creativeLook": self.style,
            "toneCurveSamples": len(self.tone_curve),
            "limitations": self.limitations,
            "workingSpace": self.working_space,
            "profileToneCurve": self.tone_curve,
        }


# Linear Rec.709 (D65) -> linear ProPhoto (D50), built from the DCP path's own
# constants so both paths land in exactly the same working space.
REC709_TO_PROPHOTO_D50 = (
    XYZ_D50_TO_PROPHOTO @ np.linalg.inv(D50_TO_D65) @ np.linalg.inv(XYZ_D65_TO_SRGB)
).astype(np.float32)


# Black & White and Sepia carry an *identity* colour matrix — their desaturation
# happens entirely in the YCC stages this pipeline does not reproduce, so
# rendering them here would produce a colour image with the wrong curve.
MONOCHROME_LOOKS = frozenset({"BW", "SE"})


def available_styles() -> list[str]:
    """Looks this path can render.

    Every ARW carries the calibration for all ten, so this is about what the two
    reproduced stages can express, not about what data is on hand.
    """
    return [s for s in LOOK_ORDER if s not in MONOCHROME_LOOKS]


def _srgb_decode(y: np.ndarray) -> np.ndarray:
    return np.where(y <= 0.04045, y / 12.92, ((y + 0.055) / 1.055) ** 2.4)


def tone_curve_points(
    cal: LookCalibration, style: str, highlights: int = 0, shadows: int = 0,
    n: int = TONE_CURVE_POINTS,
) -> list[list[float]]:
    """Sony's MainGamma LUT as (x, y) points in the frontend's contract.

    x is scene-linear with Sony's white at 1.0; y is *display-linear*, not the
    engine's own output. The LUT bakes in the sRGB transfer function (its slope
    at the origin is ~12, the sRGB toe), and the browser applies that encode
    itself at the very end — so the encode is undone here to avoid gamma twice.
    Everything between (tone curve, grading, gamut mapping) then operates on
    display-linear values, which is where those steps belong anyway.

    The curve saturates just under x = 1.0, so [0, 1] is its whole domain and no
    highlight rolloff is lost by clamping there.
    """
    lut = tone_curve(cal, style, highlights, shadows)
    x = np.linspace(0.0, 1.0, n)
    y = np.interp(x, np.linspace(0.0, 1.0, lut.size), lut)
    y = _srgb_decode(np.clip(y, 0.0, 1.0))
    return [[float(a), float(b)] for a, b in zip(x, y, strict=True)]


@lru_cache(maxsize=8)
def _cached_calibrations(path: str, size: int, mtime: int) -> tuple[LookCalibration, ...]:
    return tuple(look_calibrations(path))


def can_render(style: str | None) -> bool:
    """Whether this path can reproduce a given Creative Look."""
    return style in LOOK_ORDER and style not in MONOCHROME_LOOKS


def calibration_for(raw_path: Path, style: str) -> LookCalibration | None:
    """This shot's calibration for one look, or None if the RAW carries none.

    None means any non-Sony body, and Sony files predating the SR2DataIFD block.
    Callers use it as the availability probe *before* decoding, so the fallback
    to DCP is decided once rather than discovered halfway through. Cached
    because reaching the data means reading and decrypting the file.
    """
    index = look_index(style)
    if index is None:
        return None
    try:
        st = raw_path.stat()
        looks = _cached_calibrations(str(raw_path), st.st_size, int(st.st_mtime_ns))
    except (KeyError, ValueError, OSError, struct.error):
        return None
    return looks[index] if index < len(looks) else None


def apply_sony_profile(
    camera_rgb: np.ndarray, cal: LookCalibration, style: str,
    highlights: int = 0, shadows: int = 0,
) -> tuple[np.ndarray, SonyRenderInfo]:
    """Camera RGB -> scene-linear ProPhoto (D50), plus the matching tone curve.

    `style` selects which of the RAW's ten calibrations to use; callers gate on
    can_render first, because the tone curve is per-channel and a wrong one
    shifts hue rather than just brightness — an Instant frame rendered through
    the Film curve turns its warm tones yellow.
    """
    matrix = SegmentedMatrix(unpack_param_block(cal.param_block))
    rec709 = matrix.apply(camera_rgb)
    linear_prophoto = np.clip(rec709 @ REC709_TO_PROPHOTO_D50.T, 0, None)

    return linear_prophoto, SonyRenderInfo(
        style=style,
        tone_curve=tone_curve_points(cal, style, highlights, shadows),
        # The remaining gap to the engine is ZcTask3DLut (which the default
        # preview path never executes) plus the SSCS saturation and AreaComp
        # stages, and DRO, which lifts shadows on shots that requested it.
        limitations=["Sony's YCC stages (SSCS saturation, AreaComp, sharpening) and DRO are not reproduced."],
    )
