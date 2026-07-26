"""Render camera RGB the way Sony Imaging Edge does.

The Sony chain that matters for colour is three steps, all reproduced against
the running engine's own pixels (see ../../../../sony_repro/PIPELINE.md):

    SIMDLinearMatrix16   hue-segmented 3x3, calibration read from the ARW
    MainGamma            one per-channel 1D LUT, one curve per Creative Look
    RGB2YCC              the look's saturation and hue (chroma.py)

They map onto this pipeline's two halves. The matrix replaces the DCP colour
step and runs here; the curve replaces the view transform and runs in the
browser, shipped as `profileToneCurve` exactly like a DCP's own curve. RGB2YCC
travels with the curve because the engine runs it immediately afterwards, on the
curve's own output and in the curve's own basis.

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

Measured against the engine's own frame, one stage at a time: the tone curve is
within 1/16383, RGB2YCC's luma is bit-identical and its chroma within 1, and the
matrix now lands at 0.9891 / 1.0031 on the two chroma differences with the hue
angle within a quarter of a degree. What is left of the engine — ITP,
sharpening, Spica, and the parts of Marble that are not a round trip — comes to
about 5% of chroma, plus DRO on shots that asked for it.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import numpy as np

from ..dcp import D50_TO_D65, XYZ_D50_TO_PROPHOTO, XYZ_D65_TO_SRGB
from .chroma import blend_params, luma_terms, unpack_params
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
    chroma_cross: list[float]
    chroma_gain: list[float]
    luma_pivot: float = 0.0
    luma_contrast: float = 1.0
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
            # RGB2YCC, applied by the shader right after the curve — that is
            # where the engine runs it, and it needs the curve's own encoding.
            "profileChromaCross": self.chroma_cross,
            "profileChromaGain": self.chroma_gain,
            # YGamma, which the engine runs between the two chroma halves. It is
            # the shot's Fade setting: a contrast pull on luma toward a pivot.
            "profileLumaPivot": self.luma_pivot,
            "profileLumaContrast": self.luma_contrast,
        }


# Linear Rec.709 (D65) -> linear ProPhoto (D50), built from the DCP path's own
# constants so both paths land in exactly the same working space.
REC709_TO_PROPHOTO_D50 = (
    XYZ_D50_TO_PROPHOTO @ np.linalg.inv(D50_TO_D65) @ np.linalg.inv(XYZ_D65_TO_SRGB)
).astype(np.float32)


# Sepia's chroma terms are identical to Standard's, so its toning happens in
# some stage still unaccounted for; rendering it here would give a plain colour
# image under a Sepia label. Black & White needs no special case at all — its
# eight chroma values are zero, which zeroes both gains and leaves R = G = B.
MONOCHROME_LOOKS = frozenset({"SE"})


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
    highlights: int = 0, shadows: int = 0, fade: int = 0, dro: bool = False,
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
    cross, gain = chroma_terms(cal)
    pivot, contrast = luma_terms(cal, fade)

    return linear_prophoto, SonyRenderInfo(
        style=style,
        tone_curve=tone_curve_points(cal, style, highlights, shadows),
        chroma_cross=[float(x) for x in cross],
        chroma_gain=[float(x) for x in gain],
        luma_pivot=pivot,
        luma_contrast=contrast,
        # What is left of the engine is ChromaSuppres (measured identity in
        # every luma band), SSCS (touches no pixel on a whole frame), AreaComp
        # (0.9999), ITP, sharpening, Spica and Marble. On shots without DRO,
        # those come to a chroma ratio of 0.983..1.008 and under half a degree
        # of hue against the engine's own output.
        limitations=_limitations(dro),
    )


def _limitations(dro: bool) -> list[str]:
    """What this render cannot claim to match, for *this* shot.

    DRO is the one that changes per shot: Sony runs an extra stage for it
    (ZcTaskVatr, identified by running the stage census on a DRO shot and a
    non-DRO one — it is the only difference between them), and it lifts shadows
    by up to 5%. Listing it unconditionally would be wrong on the 63 frames in
    64 that never asked for it.
    """
    out = ["Sony's ITP, sharpening and Spica stages are not reproduced."]
    if dro:
        out.append("This shot used DRO, which Sony applies as a separate stage "
                   "(ZcTaskVatr) that is not reproduced.")
    return out


def chroma_terms(cal: LookCalibration) -> tuple[np.ndarray, np.ndarray]:
    """This look's RGB2YCC cross terms and gains, blended for the shot's light.

    The eight parameters are a base plus four illuminant deltas, mixed by four
    weights that sum to 1024. The weights belong to the frame, not the look —
    they follow the white balance — so they sit at the top of the SR2SubIFD
    while the base and deltas are per look. Across 65 frames, 46 came out
    (1024, 0, 0, 0), which is why using the base alone looked right most of the
    time; the other 19 blend two illuminants and move a parameter by up to 352.

    Where the camera has already done the blend — the look the shot was taken
    on — its own answer is used, which is also the shortcut Edit.exe takes. That
    matters in 2 frames of 65: recomputing agrees to within one unit everywhere
    else, and one unit usually vanishes in the gain's `>> 3`.
    """
    if cal.chroma_final is not None:
        return unpack_params(cal.chroma_final)
    return unpack_params(blend_params(cal.chroma_base, cal.chroma_deltas, cal.chroma_weights))
