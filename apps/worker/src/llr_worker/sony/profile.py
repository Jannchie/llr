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
from .chroma import SATURATION_STEPS, blend_params, luma_terms, saturation_factor, unpack_params
from .linear_matrix import SegmentedMatrix
from .sr2 import FADE_STEPS, LookCalibration, look_calibrations, unpack_param_block
from .tone import LOOK_ORDER, TUNE_LIMIT, look_index, tone_curve

_DATA = Path(__file__).resolve().parent / "data"

# Sony's tone LUT is indexed in units of camera RGB x 8192 and its output is
# 1/16384 of full scale. The white point was pinned four independent ways.
TONE_INDEX_WHITE = 8192
TONE_OUT_SCALE = 16384.0

# Points shipped to the frontend. The browser resamples them onto its 2048-entry
# LUT with a monotone spline; at 1024 the round trip costs under 0.05/255.
TONE_CURVE_POINTS = 1024

# The camera's own range for each tweak, which is also the range the frontend
# offers. tone.apply_tuning will happily extrapolate past its limit, but a value
# the body cannot write is no longer a Creative Look setting — and Edit.exe
# refuses those outright. Fade has no negative side; the other four are centred.
TWEAK_RANGES: dict[str, tuple[int, int]] = {
    "highlights": (-TUNE_LIMIT, TUNE_LIMIT),
    "shadows": (-TUNE_LIMIT, TUNE_LIMIT),
    "contrast": (-TUNE_LIMIT, TUNE_LIMIT),
    "fade": (0, FADE_STEPS - 1),
    "saturation": (-(len(SATURATION_STEPS) - 1), len(SATURATION_STEPS) - 1),
}


def _clamp_tweak(field: str, value: Any) -> int:
    lo, hi = TWEAK_RANGES[field]
    try:
        return max(lo, min(hi, int(value)))
    except (TypeError, ValueError):
        return 0


@dataclass(frozen=True)
class LookTweaks:
    """The five in-camera tweaks that ride on a Creative Look.

    Not one of them touches a pixel on the way through the matrix: Highlights,
    Shadows and Contrast reshape the tone curve (tone.apply_tuning), Fade sets
    YGamma's pivot and contrast, and Saturation scales the chroma either side of
    the clamp. All five leave with the profile and are applied in the browser,
    which is what lets the frontend re-request a profile for a moved slider
    instead of re-decoding the frame.
    """

    highlights: int = 0
    shadows: int = 0
    contrast: int = 0
    fade: int = 0
    saturation: int = 0

    def to_json(self) -> dict[str, int]:
        return {field: getattr(self, field) for field in TWEAK_RANGES}

    @classmethod
    def from_json(cls, data: Any) -> LookTweaks:
        """Parse a client's (or our own) JSON: missing fields mean no tweak."""
        return cls().merged(data)

    def merged(self, overrides: Any) -> LookTweaks:
        """These settings with a client's overrides on top, field by field.

        An override that is absent (or null) leaves the camera's own value
        alone, so a client can move one slider without echoing the other four.
        """
        if not isinstance(overrides, dict):
            return self
        return LookTweaks(**{
            field: getattr(self, field) if overrides.get(field) is None
            else _clamp_tweak(field, overrides[field])
            for field in TWEAK_RANGES
        })


# A shot with no tweak at all, and the default everywhere one is optional. One
# shared instance is safe because LookTweaks is frozen.
NO_TWEAKS = LookTweaks()


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
    chroma_saturation: float = 1.0
    sepia: dict[str, Any] | None = None
    # What was applied, and what the body itself recorded. They differ only when
    # the client overrode a slider; shipping both lets the panel show the
    # camera's own numbers as its starting point and its reset target.
    tweaks: LookTweaks = NO_TWEAKS
    as_shot: LookTweaks = NO_TWEAKS
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
            # The Saturation slider. The gains above are already divided by it;
            # this is the factor the shader multiplies back after the clamp,
            # which is where the setting's whole visible effect comes from.
            "profileChromaSaturation": self.chroma_saturation,
            # Sepia's toning (ZcTaskEffect), null for the nine looks without it.
            "profileSepia": self.sepia,
            # The Creative Look tweaks this profile was built with, and the ones
            # the body recorded. Everything above already has them baked in.
            "lookTweaks": self.tweaks.to_json(),
            "lookAsShot": self.as_shot.to_json(),
        }


# Linear Rec.709 (D65) -> linear ProPhoto (D50), built from the DCP path's own
# constants so both paths land in exactly the same working space.
REC709_TO_PROPHOTO_D50 = (
    XYZ_D50_TO_PROPHOTO @ np.linalg.inv(D50_TO_D65) @ np.linalg.inv(XYZ_D65_TO_SRGB)
).astype(np.float32)


# Black & White needs no special case at all — its eight chroma values are zero,
# which zeroes both gains and leaves R = G = B. Sepia's are identical to
# Standard's, so its toning is a separate stage: ZcTaskEffect, which the engine
# runs right after YCC2RGB and only for this look (found by running the stage
# census on a Sepia frame and a Film one). See sepia_toning.
UNRENDERABLE_LOOKS: frozenset[str] = frozenset()


def available_styles() -> list[str]:
    """Looks this path can render.

    Every ARW carries the calibration for all ten, so this is about what the two
    reproduced stages can express, not about what data is on hand.
    """
    return [s for s in LOOK_ORDER if s not in UNRENDERABLE_LOOKS]


def _srgb_decode(y: np.ndarray) -> np.ndarray:
    return np.where(y <= 0.04045, y / 12.92, ((y + 0.055) / 1.055) ** 2.4)


# Sepia's toning, measured off ZcTaskEffect's own in/out over three frames and
# six million pixels. The stage throws the chroma away and maps a weighted sum
# of the display-encoded RGB through one curve per channel — which is what a
# toned monochrome is. The weights are fitted rather than the engine's luma
# ones: they halve the residual, and they hold up on a frame left out of the
# fit (median 0.18% against 0.32% for the luma weights).
SEPIA_LOOK = "SE"


@lru_cache(maxsize=1)
def _sepia() -> tuple[list[float], list[list[float]]]:
    with np.load(_DATA / "sepia.npz") as z:
        return [float(w) for w in z["weights"]], [[float(v) for v in row] for row in z["lut"].T]


def sepia_toning(style: str) -> dict[str, Any] | None:
    """The toning table for a look, or None for the nine that need none."""
    if style != SEPIA_LOOK:
        return None
    weights, lut = _sepia()
    return {"weights": weights, "lut": lut}


def tone_curve_points(
    cal: LookCalibration, style: str, highlights: int = 0, shadows: int = 0,
    contrast: int = 0, n: int = TONE_CURVE_POINTS,
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
    lut = tone_curve(cal, style, highlights, shadows, contrast)
    x = np.linspace(0.0, 1.0, n)
    y = np.interp(x, np.linspace(0.0, 1.0, lut.size), lut)
    y = _srgb_decode(np.clip(y, 0.0, 1.0))
    return [[float(a), float(b)] for a, b in zip(x, y, strict=True)]


@lru_cache(maxsize=8)
def _cached_calibrations(path: str, size: int, mtime: int) -> tuple[LookCalibration, ...]:
    return tuple(look_calibrations(path))


def can_render(style: str | None) -> bool:
    """Whether this path can reproduce a given Creative Look."""
    return style in LOOK_ORDER and style not in UNRENDERABLE_LOOKS


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


def look_render_info(
    cal: LookCalibration, style: str, tweaks: LookTweaks = NO_TWEAKS,
    as_shot: LookTweaks | None = None, dro: bool = False,
) -> SonyRenderInfo:
    """Everything about a shot's rendering that runs in the browser.

    Split out from apply_sony_profile because none of the five tweaks reaches
    the matrix: moving one changes this report and nothing else, so the frontend
    can ask for a new one over ~75 kB (mostly the 1024-point tone curve) rather
    than re-decoding tens of megabytes of pixels it already has.
    """
    cross, gain = chroma_terms(cal)
    # Named apart from `contrast` on purpose: that one is the tone-curve tweak,
    # this one is YGamma's, and the two are unrelated numbers on unrelated
    # scales. Reusing the name silently fed YGamma's 1.05 to the tone curve.
    luma_pivot, luma_contrast = luma_terms(cal, tweaks.fade)
    # The engine divides the gains before the clamp and multiplies the chroma
    # back after it. The shader can only do the multiply, so the division
    # happens here and it gets the divided gains. chroma.rgb_to_ycc, which is
    # not passing anything to a shader, does both halves itself and so takes the
    # look's own gains — do not feed it these.
    sat = saturation_factor(tweaks.saturation)

    return SonyRenderInfo(
        style=style,
        tone_curve=tone_curve_points(cal, style, tweaks.highlights, tweaks.shadows,
                                     tweaks.contrast),
        chroma_cross=[float(x) for x in cross],
        chroma_gain=[float(x) / sat for x in gain],
        chroma_saturation=sat,
        sepia=sepia_toning(style),
        luma_pivot=luma_pivot,
        luma_contrast=luma_contrast,
        tweaks=tweaks,
        as_shot=tweaks if as_shot is None else as_shot,
        # What is left of the engine is ChromaSuppres (measured identity in
        # every luma band), SSCS (touches no pixel on a whole frame), AreaComp
        # (0.9999), ITP, sharpening, Spica and Marble. On shots without DRO,
        # those come to a chroma ratio of 0.983..1.008 and under half a degree
        # of hue against the engine's own output.
        limitations=_limitations(dro),
    )


def apply_sony_profile(
    camera_rgb: np.ndarray, cal: LookCalibration, style: str,
    tweaks: LookTweaks = NO_TWEAKS, dro: bool = False,
) -> tuple[np.ndarray, SonyRenderInfo]:
    """Camera RGB -> scene-linear ProPhoto (D50), plus the matching tone curve.

    `style` selects which of the RAW's ten calibrations to use; callers gate on
    can_render first, because the tone curve is per-channel and a wrong one
    shifts hue rather than just brightness — an Instant frame rendered through
    the Film curve turns its warm tones yellow.

    No as_shot parameter: a decode always renders the settings the body itself
    recorded, so `tweaks` is the shot's own answer and look_render_info reports
    it as both. Client overrides ride on the cached profile instead, through
    apply_look_overrides.
    """
    matrix = SegmentedMatrix(unpack_param_block(cal.param_block))
    rec709 = matrix.apply(camera_rgb)
    linear_prophoto = np.clip(rec709 @ REC709_TO_PROPHOTO_D50.T, 0, None)
    return linear_prophoto, look_render_info(cal, style, tweaks, dro=dro)


def apply_look_overrides(
    profile: dict[str, Any], raw_path: Path, overrides: Any,
) -> dict[str, Any]:
    """Re-derive a finished sony profile for different Creative Look tweaks.

    The point of this existing at all is the linear cache: the tweaks change no
    pixel, so they stay out of its key and a cached decode serves any setting.
    That only works if the profile that travels with those cached pixels can be
    rebuilt for the request at hand, which is what this does.

    Anything that is not a sony profile — a DCP render, a JPEG — comes back
    untouched, as does a RAW whose calibration can no longer be read.
    """
    if profile.get("kind") != "sony" or not isinstance(overrides, dict):
        return profile
    style = str(profile.get("creativeLook") or "")
    as_shot = LookTweaks.from_json(profile.get("lookAsShot"))
    tweaks = as_shot.merged(overrides)
    if tweaks == LookTweaks.from_json(profile.get("lookTweaks")):
        return profile
    cal = calibration_for(raw_path, style)
    if cal is None:
        return profile
    rebuilt = {**profile, **look_render_info(cal, style, tweaks, as_shot).to_json()}
    # DRO is a property of the shot, not of any tweak, and _limitations cannot
    # know it from here — keep the ones the decode itself reported.
    rebuilt["limitations"] = profile.get("limitations", [])
    return rebuilt


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
