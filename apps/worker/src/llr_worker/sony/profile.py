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
angle within a quarter of a degree. What is left of the engine — ITP and the
parts of Marble that are not a round trip — comes to about 5% of chroma, plus
DRO on shots that asked for it.

Three of its spatial stages are reproduced as well, and none touches these
pixels: Clarity (clarity.py) and both halves of sharpening (sharpness.py for the
coarse pass, spica.py for the fine one) all run on the finished frame in the
browser, so they ride out on the profile as constants.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import numpy as np

from ..creative_style import normalize_style
from ..dcp import D50_TO_D65, XYZ_D50_TO_PROPHOTO, XYZ_D65_TO_SRGB
from .chroma import (
    LUMA_CONTRAST_ADVANCED,
    LUMA_LUT_WIRE,
    SATURATION_STEPS,
    blend_params,
    luma_terms,
    saturation_factor,
    unpack_params,
)
from .chroma import (
    # Renamed: SonyRenderInfo has a `luma_lut` field of its own, and the two
    # would shadow each other inside look_render_info.
    luma_lut as ygamma_lut,
)
from .clarity import (
    CLARITY_CALIBRATION_BODY,
    CLARITY_CENTER_MIX,
    CLARITY_DOWNSAMPLE,
    CLARITY_EDGE_THRESHOLD,
    CLARITY_MAX,
    CLARITY_MIN,
    CLARITY_ROLLOFF_KNEE,
    clarity_amount,
)
from .dro import (
    DRO_GRID_LUMA_WHITE,
    DRO_LUMA_WHITE,
    dro_gain_table,
    dro_level_gain_table,
    scale_dro_gain,
)
from .dro_presets import DRO_LEVEL_AUTO, DRO_LEVEL_NO_CURVE, DRO_UI_LEVELS
from .linear_matrix import SegmentedMatrix
from .sharpness import sharpness_block
from .spica import spica_off
from .sr2 import (
    DRO_LOG_CEILING,
    FADE_STEPS,
    LookCalibration,
    look_calibrations,
    luma_lut_key_for,
    unpack_param_block,
)
from .tone import LOOK_ORDER, TUNE_LIMIT, look_index, tone_curve

_DATA = Path(__file__).resolve().parent / "data"

# Sony's tone LUT is indexed in units of camera RGB x 8192 and its output is
# 1/16384 of full scale. The white point was pinned four independent ways.
TONE_INDEX_WHITE = 8192
TONE_OUT_SCALE = 16384.0

# Points shipped to the frontend. The browser resamples them onto its 2048-entry
# LUT with a monotone spline; at 1024 the round trip costs under 0.05/255.
TONE_CURVE_POINTS = 1024

# The range each tweak can be rendered over, which is also the range the
# frontend offers — it ships them (to_json's "lookRanges") rather than repeating
# them, so no stop can appear on a slider that the engine will not honour.
# tone.apply_tuning will happily extrapolate past its limit, but a value the
# body cannot write is no longer a Creative Look setting — and Edit.exe refuses
# those outright. Fade and Clarity have no negative side: for Fade the camera
# writes none, and for Clarity the engine clamps at zero instead of inverting,
# which would make every negative stop render like 0 (see clarity.py).
TWEAK_RANGES: dict[str, tuple[int, int]] = {
    "highlights": (-TUNE_LIMIT, TUNE_LIMIT),
    "shadows": (-TUNE_LIMIT, TUNE_LIMIT),
    "contrast": (-TUNE_LIMIT, TUNE_LIMIT),
    "fade": (0, FADE_STEPS - 1),
    "saturation": (-(len(SATURATION_STEPS) - 1), len(SATURATION_STEPS) - 1),
    "clarity": (CLARITY_MIN, CLARITY_MAX),
}


def _clamp_tweak(field: str, value: Any) -> int:
    lo, hi = TWEAK_RANGES[field]
    try:
        return max(lo, min(hi, int(value)))
    except (TypeError, ValueError):
        return 0


@dataclass(frozen=True)
class LookTweaks:
    """The six in-camera tweaks that ride on a Creative Look.

    Not one of them touches a pixel on the way through the matrix: Highlights,
    Shadows and Contrast reshape the tone curve (tone.apply_tuning), Fade sets
    YGamma's pivot and contrast, and Saturation scales the chroma either side of
    the clamp. All six leave with the profile and are applied in the browser,
    which is what lets the frontend re-request a profile for a moved slider
    instead of re-decoding the frame.

    Clarity is the odd one out only in *how* the browser applies it: it is
    spatial, so it rides as a gain for the shader's own blur chain rather than
    as a reshaped curve. See clarity.py.
    """

    highlights: int = 0
    shadows: int = 0
    contrast: int = 0
    fade: int = 0
    saturation: int = 0
    clarity: int = 0

    def to_json(self) -> dict[str, int]:
        return {field: getattr(self, field) for field in TWEAK_RANGES}

    @classmethod
    def from_json(cls, data: Any) -> LookTweaks:
        """Parse a client's (or our own) JSON: missing fields mean no tweak."""
        return cls().merged(data)

    def merged(self, overrides: Any) -> LookTweaks:
        """These settings with a client's overrides on top, field by field.

        An override that is absent (or null) leaves the camera's own value
        alone, so a client can move one slider without echoing the other five.
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
    # YGamma's table, the first LUMA_LUT_WIRE entries of the look's own (the
    # rest cannot be reached from a shader whose luma is clamped to 1.0). It is
    # per-look, so it has to be re-derived whenever the look changes — which is
    # why it lives here beside the two terms rather than riding forward the way
    # dro_grid does. About 90 kB of JSON; the alternative is shipping both
    # tables and an index, which is bigger.
    luma_lut: list[int] | None = None
    # ...and the pair Edit's 色彩复制 = 高级 puts in their place. That setting is
    # a switch in the browser, so both answers have to travel together or
    # flipping it would cost a round trip for a stage that changes no pixel of
    # the decode. The contrast is 高级's one constant rather than a per-look
    # entry (chroma.LUMA_CONTRAST_ADVANCED); the pivot is shared with the
    # standard path, which is why there is no advanced one here.
    luma_lut_advanced: list[int] | None = None
    luma_contrast_advanced: float = LUMA_CONTRAST_ADVANCED
    chroma_saturation: float = 1.0
    sepia: dict[str, Any] | None = None
    # What was applied, and what the body itself recorded. They differ only when
    # the client overrode a slider; shipping both lets the panel show the
    # camera's own numbers as its starting point and its reset target.
    tweaks: LookTweaks = NO_TWEAKS
    as_shot: LookTweaks = NO_TWEAKS
    working_space: str = "linear-prophoto-d50"
    # True when the look's curve and chroma came from the donor table rather than
    # from this RAW — the body predates the look. See BORROWED_DATA.
    borrowed: bool = False
    # DRO's gain against log luminance, for the shader to apply before anything
    # else — that is where the engine's own stage sits. None when the shot has no
    # DRO curve at all, which is a different thing from a strength of zero.
    dro_gain: list[float] | None = None
    dro_strength: float = 0.0
    dro_as_shot: float = 0.0
    # Whether DRO can be reached at all on this frame. Always true for a Sony
    # RAW now that manual levels come from the engine's own presets; it stays a
    # field so the frontend keeps one thing to branch on.
    dro_available: bool = False
    # The engine's own level encoding: -1 means Auto, i.e. the curve the body
    # wrote, scaled by dro_strength. 0..99 selects Edit.exe's built-in presets
    # instead, ten of them with interpolation between (sony/dro_presets.py).
    dro_level: int = DRO_LEVEL_AUTO
    # The engine's own 8x6x14 bilateral grid, already in wire form (sony/dro.py
    # dro_grid_json). With it the shader indexes the tone curve by the *local*
    # log mean, which is what DRO actually is; without it the pixel's own log
    # luminance stands in and the operator goes global. Costs a raw read to
    # build, so it rides along with the decode and is carried forward untouched
    # when only the look or the strength changes.
    dro_grid: dict[str, Any] | None = None
    # The body's sharpening, which is a camera setting rather than one of the
    # Creative Look tweaks and so does not ride on `tweaks`. Already in wire
    # form (sharpness.sharpness_block), and carried forward untouched by
    # apply_look_overrides the way dro_grid is — no slider can change it. None
    # for a file whose settings could not be read, which renders as no
    # sharpening rather than as an invented default.
    sharpen: dict[str, Any] | None = None
    # The other half of the same control (sony/spica.py). Travels beside
    # `sharpen` and for all the same reasons; kept a separate block because the
    # two stages are weighted against each other and a consumer that wants to
    # show the split needs to see both numbers.
    spica: dict[str, Any] | None = None
    # ChromaSuppres' four terms, in wire form (sony/chromasuppres.py). Derived
    # from four SR2 tags of the shot's own calibration, so like `sharpen` it is
    # the body's answer rather than a slider's and rides through a rebuild
    # untouched. None for a file whose tags could not be read, which renders
    # with the stage off — the engine's rolloff would otherwise be invented.
    chroma_suppres: dict[str, Any] | None = None
    # Marble's chroma cleanup, the engine's last stage (sony/marble.py): the
    # shot's ISO, which sets how much of the cleaned chroma is blended back
    # (0.5 at ISO 100, 1.0 from ISO 1600), and the body's threshold calibration.
    # None for a profile built without exif, which renders with the stage off.
    marble: dict[str, Any] | None = None

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
            # ...and the table it indexes Y through first, on the engine's
            # 0..16383 scale in and out. Standard and Neutral carry a highlight
            # knee here; the other eight are near identity. Null only for a
            # profile built without a calibration to read the selector from.
            "profileLumaLut": self.luma_lut,
            # The same stage under 色彩复制 = 高级, which swaps this table and
            # this contrast as well as adding the 3-D LUT after it. Both ride
            # along unconditionally so the browser's switch stays a redraw.
            "profileLumaLutAdvanced": self.luma_lut_advanced,
            "profileLumaContrastAdvanced": self.luma_contrast_advanced,
            # ChromaSuppres, which runs just *before* YGamma and reads the luma
            # from before it: the mid-tones lose 1/256 of their chroma and the
            # highlights fade out above hiY. Null means the shot's four SR2 tags
            # could not be read, and the shader leaves the chroma alone.
            "profileChromaSuppres": self.chroma_suppres,
            # Marble's chroma cleanup (sony/marble.py): ISO for the blend
            # amount and the threshold calibration. Null renders it off.
            "profileMarble": self.marble,
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
            # What each of them can be set to. Sent for the same reason as
            # droLevels: the ranges are the engine's, so a slider built from
            # anything else could offer a stop that renders like its neighbour.
            "lookRanges": {field: list(r) for field, r in TWEAK_RANGES.items()},
            # This look is not in the RAW; its curve and chroma were borrowed.
            "lookBorrowed": self.borrowed,
            # DRO. The table is indexed by log luminance over [0, DRO_LOG_CEILING]
            # — see sony/dro.py for why that scale and not normalised luma.
            "profileDroGain": self.dro_gain,
            # num/den for the bilateral grid, plus the affine map from image
            # coordinates onto it. Present = the shader can be local; absent =
            # it falls back to the global approximation on droLumaWhite's scale.
            "profileDroGrid": self.dro_grid,
            "droStrength": self.dro_strength,
            "droAsShot": self.dro_as_shot,
            "droAvailable": self.dro_available,
            "droLevel": self.dro_level,
            "droLevels": list(DRO_UI_LEVELS),
            "droLogCeiling": DRO_LOG_CEILING,
            "droLumaWhite": DRO_LUMA_WHITE,
            # Clarity. The setting itself is in lookTweaks; these are what the
            # shader's blur chain needs, all from the body's calibration rather
            # than from the file (sony/clarity.py). Gain 0 = the stage is off.
            "profileClarity": {
                "gain": clarity_amount(self.tweaks.clarity),
                "downsample": CLARITY_DOWNSAMPLE,
                "edgeThreshold": CLARITY_EDGE_THRESHOLD,
                "centerMix": CLARITY_CENTER_MIX,
                "rolloffKnee": CLARITY_ROLLOFF_KNEE,
            },
            # Sharpening, the stage right before Clarity. `amount` folds both
            # camera settings and this shot's own calibration into the one
            # number the shader multiplies its high-pass by; the kernel and the
            # dead zone are the operator's own and live in the shader.
            "profileSharpness": self.sharpen or sharpness_block(0, -1),
            # Spica, the fine half, which runs between sharpening and Clarity.
            # `amount` is how much of the filtered buffer survives its blend and
            # `isoGain` scales the detail before it; the classifier, the weight
            # tables and the three gain curves are the operator's own and live
            # in the shader. Off is spelled by spica_off, not by running the
            # formula on absent tags — see there for why they differ.
            "profileSpica": self.spica or spica_off(),
            # What the engine's own luma is worth on normalised RGB: it forms
            # BT.601 over a 14-bit plane, so white enters the grid's luma axis
            # at log2(16383) and clamps. Only the grid path uses this — the
            # global fallback deliberately puts white on droLumaWhite instead,
            # so that it leaves white alone rather than dimming it.
            "droGridLumaWhite": DRO_GRID_LUMA_WHITE,
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


# Looks a body newer than this shot's ships, carried as data so a camera that
# never had them can still render them. Sony added FL2/FL3 with the a7 V, FX2 and
# RX1R III and back-ported them to the a1 II / a9 III by firmware; bodies before
# that write ten SR2DataIFDs and simply have no calibration for the other two.
#
# What makes borrowing defensible is a measured split, checked by diffing an
# ILCE-7M5's twelve looks against an ILCE-7CM2's ten:
#
#   * the tone curves (0x7805/0x7806) are byte-identical across the two bodies
#     for all ten shared looks — the curve is Sony's definition of the look, not
#     a per-body calibration, so it transplants exactly;
#   * the chroma parameters are per-body (RMS 52 against a magnitude of ~700,
#     about 7%), so the borrowed ones are the donor's, not what this body would
#     have written. A "relative transfer" via Standard as an anchor was tried and
#     is *worse* (RMS 62): the per-body difference has no look-independent part.
#
# The hue-segmented matrix is never borrowed — it is per-shot calibration from
# the file's own top level, so a borrowed look still renders through this body's
# own colour. Reported in limitations rather than passed off as the real thing.
BORROWED_DATA = _DATA / "donor_looks.npz"


@lru_cache(maxsize=1)
def _borrowed() -> dict[str, dict[str, np.ndarray]]:
    if not BORROWED_DATA.exists():
        return {}
    with np.load(BORROWED_DATA) as z:
        names = [str(n) for n in z["names"]]
        return {
            code: {
                "curve_x": z[f"{code}_curve_x"],
                "curve_y": z[f"{code}_curve_y"],
                "chroma_base": z[f"{code}_chroma_base"],
                "chroma_deltas": z[f"{code}_chroma_deltas"],
            }
            for code in names
        }


def borrowed_looks() -> list[str]:
    """Look codes this build can supply to a body that lacks them."""
    return list(_borrowed())


def _borrowed_calibration(raw_path: Path, style: str) -> LookCalibration | None:
    """A borrowed look, wearing this shot's own body-level calibration.

    Everything that describes the *frame* — the matrix, the illuminant weights,
    the Fade tables — still comes from the file being rendered. Only the look's
    own curve and chroma come from the donor.
    """
    table = _borrowed().get(style)
    if table is None:
        return None
    own = _calibrations_or_none(raw_path)
    if not own:
        return None
    host = own[0]
    return LookCalibration(
        name=style,
        param_block=host.param_block,
        curve_x=table["curve_x"],
        curve_y=table["curve_y"],
        chroma_base=table["chroma_base"],
        chroma_deltas=table["chroma_deltas"],
        chroma_weights=host.chroma_weights,
        luma_pivot=host.luma_pivot,
        luma_contrast=host.luma_contrast,
        # The camera's own blend is for the look it shot, which this is not.
        chroma_final=None,
        # ...and neither is the host's YGamma table: that pair is per-look, so
        # borrowing the host's would give a borrowed FL2 the *host look's* knee.
        # The donor table predates these two tags and does not carry them, so
        # the family comes from the look's name — Standard and Neutral against
        # the other eight, which is the split every file measured shows.
        luma_lut_key=luma_lut_key_for(style),
    )


def _code_of(cal: LookCalibration) -> str:
    """This calibration's Creative Look code.

    Through the same table the exif CreativeStyle tag goes through, so a name
    this build has never seen arrives as its own code rather than being dropped.
    """
    return normalize_style(cal.name) or cal.name


def looks_in_file(raw_path: Path) -> list[str]:
    """The Creative Look codes this RAW actually carries, in the file's order.

    Read from each SR2DataIFD's own name (tag 0x7770) rather than taken from
    LOOK_ORDER's positions. The position list is only true for bodies that write
    exactly the ten looks measured so far; newer ones ship more (FL2, FL3), and
    a positional lookup would either truncate them away or hand back the wrong
    calibration. The names normalise to codes through the same table the exif
    CreativeStyle tag goes through, so an unknown name arrives as its own code
    rather than being dropped.

    Empty for any file with no readable calibration — non-Sony bodies included.
    Borrowed looks are appended after the file's own, so a body that already
    ships FL2/FL3 uses its own calibration for them and never the donor's.
    """
    looks = _calibrations_or_none(raw_path)
    if looks is None:
        return []
    present = [_code_of(cal) for cal in looks]
    return present + [c for c in _borrowed() if c not in present]


def is_borrowed(raw_path: Path, style: str) -> bool:
    """Whether this look would come from the donor table rather than the file."""
    if style not in _borrowed():
        return False
    looks = _calibrations_or_none(raw_path)
    return bool(looks) and all(_code_of(c) != style for c in looks)


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
    cal: LookCalibration, highlights: int = 0, shadows: int = 0,
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
    lut = tone_curve(cal, highlights, shadows, contrast)
    x = np.linspace(0.0, 1.0, n)
    y = np.interp(x, np.linspace(0.0, 1.0, lut.size), lut)
    y = _srgb_decode(np.clip(y, 0.0, 1.0))
    return [[float(a), float(b)] for a, b in zip(x, y, strict=True)]


@lru_cache(maxsize=8)
def _cached_calibrations(path: str, size: int, mtime: int) -> tuple[LookCalibration, ...]:
    return tuple(look_calibrations(path))


def can_render(style: str | None, raw_path: Path | None = None) -> bool:
    """Whether this path can reproduce a given Creative Look.

    Without a file this is the static answer: one of the ten codes this pipeline
    knows. Given one, the file's own list wins, so a look the body ships but this
    module has never heard of (FL2, FL3) is renderable — nothing in the two
    reproduced stages is specific to which look the calibration describes.
    """
    if style is None or style in UNRENDERABLE_LOOKS:
        return False
    if raw_path is not None and (present := looks_in_file(raw_path)):
        return style in present
    return style in LOOK_ORDER


def _calibrations_or_none(raw_path: Path) -> tuple[LookCalibration, ...] | None:
    try:
        st = raw_path.stat()
        return _cached_calibrations(str(raw_path), st.st_size, int(st.st_mtime_ns))
    except (KeyError, ValueError, OSError, struct.error):
        return None


def calibration_for(raw_path: Path, style: str) -> LookCalibration | None:
    """This shot's calibration for one look, or None if the RAW carries none.

    None means any non-Sony body, and Sony files predating the SR2DataIFD block.
    Callers use it as the availability probe *before* decoding, so the fallback
    to DCP is decided once rather than discovered halfway through. Cached
    because reaching the data means reading and decrypting the file.

    Matched on the calibration's own name first so that bodies writing more than
    ten looks resolve correctly; LOOK_ORDER's position is the fallback for files
    whose names do not normalise to a code we recognise.
    """
    looks = _calibrations_or_none(raw_path)
    if looks is None:
        return None
    for cal in looks:
        if _code_of(cal) == style:
            return cal
    if (borrowed := _borrowed_calibration(raw_path, style)) is not None:
        return borrowed
    index = look_index(style)
    return looks[index] if index is not None and index < len(looks) else None


def look_render_info(
    cal: LookCalibration, style: str, tweaks: LookTweaks = NO_TWEAKS,
    as_shot: LookTweaks | None = None, dro: bool = False, borrowed: bool = False,
    dro_gain: list[float] | None = None, dro_strength: float | None = None,
    dro_grid: dict[str, Any] | None = None, dro_level: int = DRO_LEVEL_AUTO,
    sharpen: dict[str, Any] | None = None, spica: dict[str, Any] | None = None,
    chroma_suppres: dict[str, Any] | None = None, marble: dict[str, Any] | None = None,
) -> SonyRenderInfo:
    """Everything about a shot's rendering that runs in the browser.

    Split out from apply_sony_profile because none of the six tweaks reaches
    the matrix: moving one changes this report and nothing else, so the frontend
    can ask for a new one over ~75 kB (mostly the 1024-point tone curve) rather
    than re-decoding tens of megabytes of pixels it already has.
    """
    cross, gain = chroma_terms(cal)
    # Named apart from `contrast` on purpose: that one is the tone-curve tweak,
    # this one is YGamma's, and the two are unrelated numbers on unrelated
    # scales. Reusing the name silently fed YGamma's 1.05 to the tone curve.
    luma_pivot, luma_contrast = luma_terms(cal, tweaks.fade)
    # 高级's own contrast, beside the shot's. Both go on the wire because the
    # setting is a switch in the browser rather than something the decode knows.
    _, luma_contrast_advanced = luma_terms(cal, tweaks.fade, advanced=True)
    # The engine divides the gains before the clamp and multiplies the chroma
    # back after it. The shader can only do the multiply, so the division
    # happens here and it gets the divided gains. chroma.rgb_to_ycc, which is
    # not passing anything to a shader, does both halves itself and so takes the
    # look's own gains — do not feed it these.
    sat = saturation_factor(tweaks.saturation)
    # As-shot is 1.0 exactly when the body applied DRO. A frame it rendered
    # without DRO still ships the table, so the control has something to scale,
    # but it starts at zero and the render is unchanged until someone moves it.
    # What the *body* did, whether or not a curve survived for us to apply. A
    # shot that used DRO with no curve in the file has to keep saying so, and
    # collapsing the two into one number loses that on every profile rebuild.
    as_shot_dro = 1.0 if dro else 0.0
    strength = as_shot_dro if dro_strength is None else max(0.0, float(dro_strength))
    # Three states, in the engine's own terms. Off wins over everything, so a
    # level left set from a previous choice cannot resurrect the effect. A level
    # then supplies its own curve from Edit.exe's preset ladder; only Auto scales
    # the curve the body wrote, because only there is there one to scale.
    if strength <= 0.0:
        table = None
    elif dro_level >= 0:
        table = dro_level_gain_table(dro_level)
    elif dro_gain is not None:
        # None rather than 512 ones when there is nothing to do: the consumer
        # branches on it either way, and this keeps the common case off the wire.
        table = scale_dro_gain(dro_gain, strength)
    elif dro:
        # Auto, but the body wrote no curve. The engine does not give up here —
        # it renders its level-5 preset — so neither do we, and the "DRO applied,
        # not reproduced" warning below stops being true.
        table = dro_level_gain_table(DRO_LEVEL_NO_CURVE, strength)
    else:
        table = None

    return SonyRenderInfo(
        style=style,
        tone_curve=tone_curve_points(cal, tweaks.highlights, tweaks.shadows,
                                     tweaks.contrast),
        chroma_cross=[float(x) for x in cross],
        chroma_gain=[float(x) / sat for x in gain],
        chroma_saturation=sat,
        sepia=sepia_toning(style),
        luma_pivot=luma_pivot,
        luma_contrast=luma_contrast,
        luma_lut=[int(v) for v in ygamma_lut(cal)[:LUMA_LUT_WIRE]],
        luma_lut_advanced=[int(v) for v in ygamma_lut(cal, advanced=True)[:LUMA_LUT_WIRE]],
        luma_contrast_advanced=luma_contrast_advanced,
        tweaks=tweaks,
        as_shot=tweaks if as_shot is None else as_shot,
        # ChromaSuppres is reproduced now (sony/chromasuppres.py), bit-exactly
        # against the engine's own tiles — it was never the identity the note
        # here used to claim, it just hides in the mid-tones as a flat 255/256.
        # What is left of the engine is SSCS (touches no pixel on a whole
        # frame), AreaComp (0.9999) and ITP. On shots without DRO, those come to
        # a chroma ratio of 0.983..1.008 and under half a degree of hue against
        # the engine's own output.
        limitations=_limitations(table is not None, borrowed,
                                 dro_global=dro_grid is None,
                                 clarity=clarity_amount(tweaks.clarity) > 0,
                                 sharpen=float((sharpen or {}).get("amount") or 0) > 0,
                                 spica=float((spica or {}).get("amount") or 0) > 0),
        borrowed=borrowed,
        dro_gain=table,
        dro_level=dro_level,
        # Unlike the gain table, this stays on the profile at strength zero.
        # It is what the file says, not what is being applied, and dropping it
        # would mean turning DRO off and back on silently downgraded the render
        # to the global approximation — apply_look_overrides has nowhere to
        # recover it from. The consumer already ignores it when nothing is on.
        dro_grid=dro_grid,
        dro_strength=strength,
        dro_as_shot=as_shot_dro,
        sharpen=sharpen,
        spica=spica,
        chroma_suppres=chroma_suppres,
        marble=marble,
        # Every Sony RAW can take a manual level, because the preset curves are
        # the engine's rather than the file's — and this function only ever runs
        # on a Sony RAW, since it needs one for its calibration. Auto is the part
        # that still depends on the file, and dro_gain is what reports on that.
        dro_available=True,
    )


def apply_sony_profile(
    camera_rgb: np.ndarray, cal: LookCalibration, style: str,
    tweaks: LookTweaks = NO_TWEAKS, dro: bool = False,
    dro_gain: list[float] | None = None, dro_grid: dict[str, Any] | None = None,
    sharpen: dict[str, Any] | None = None, spica: dict[str, Any] | None = None,
    chroma_suppres: dict[str, Any] | None = None, marble: dict[str, Any] | None = None,
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

    DRO does not touch these pixels either, and for a reason worth stating: it
    is one scalar gain per pixel, and a scalar commutes with the matrix, so
    applying it in the shader afterwards gives the same answer as applying it
    here beforehand. That is what keeps the strength control off the decode path.
    """
    matrix = SegmentedMatrix(unpack_param_block(cal.param_block))
    rec709 = matrix.apply(camera_rgb)
    linear_prophoto = np.clip(rec709 @ REC709_TO_PROPHOTO_D50.T, 0, None)
    return linear_prophoto, look_render_info(cal, style, tweaks, dro=dro, dro_gain=dro_gain,
                                             dro_grid=dro_grid, sharpen=sharpen,
                                             spica=spica, chroma_suppres=chroma_suppres,
                                             marble=marble)


def apply_look_overrides(
    profile: dict[str, Any], raw_path: Path, overrides: Any, style: str | None = None,
    dro_strength: float | None = None, dro_level: int | None = None,
) -> dict[str, Any]:
    """Re-derive a finished sony profile for a different Creative Look setting.

    The point of this existing at all is the linear cache: neither the six
    tweaks nor the choice of look changes a pixel, so both stay out of its key
    and one cached decode serves every setting. That only works if the profile
    travelling with those cached pixels can be rebuilt for the request at hand,
    which is what this does.

    `style` switches which of the RAW's parallel calibrations is used. It is
    pixel-free for the same reason the tweaks are: the hue-segmented matrix is
    the *body's*, stored once at the SR2SubIFD's top level and shared by every
    look, so only the tone curve and the chroma terms differ — and both of those
    travel in this profile for the shader to apply. Verified rather than assumed:
    all ten looks put the same 276-byte param_block through SegmentedMatrix and
    return bit-identical linear output.

    Anything that is not a sony profile — a DCP render, a JPEG — comes back
    untouched, as does a RAW whose calibration can no longer be read.
    """
    if profile.get("kind") != "sony":
        return profile
    current = str(profile.get("creativeLook") or "")
    style = style or current
    as_shot_dro = float(profile.get("droAsShot") or 0.0)
    strength = as_shot_dro if dro_strength is None else max(0.0, float(dro_strength))
    # None means the caller said nothing about the level, so keep the one this
    # profile was built with — the same "unspecified, not Auto" rule the strength
    # above follows. Auto has its own value (-1) and does travel.
    current_level = int(profile.get("droLevel", DRO_LEVEL_AUTO) or DRO_LEVEL_AUTO)
    level = current_level if dro_level is None else int(dro_level)
    dro_changed = (strength != float(profile.get("droStrength") or 0.0)
                   or level != current_level)
    if not isinstance(overrides, dict) and style == current and not dro_changed:
        return profile
    as_shot = LookTweaks.from_json(profile.get("lookAsShot"))
    tweaks = as_shot.merged(overrides) if isinstance(overrides, dict) else as_shot
    if (style == current and not dro_changed
            and tweaks == LookTweaks.from_json(profile.get("lookTweaks"))):
        return profile
    cal = calibration_for(raw_path, style)
    if cal is None:
        return profile
    # Re-read rather than rescale what the profile carries: that table is already
    # at the applied strength, and recovering the as-shot one from it means a
    # root that is undefined at strength zero. Reading is a decrypt and a Bezier.
    # The grid is carried forward rather than rebuilt: it depends only on the
    # RAW's pixels, so neither the look nor the strength can change it, and
    # rebuilding would put a full raw read behind a slider. This is also why
    # to_json has to emit the key unconditionally — a None here would clobber it.
    #
    # Sharpening rides forward for the same reason and by the same means: it is
    # a camera setting rather than a Creative Look tweak, so nothing a slider
    # can send here changes it, and carrying the block beats re-reading the RAW
    # for a number that cannot have moved. ChromaSuppres is the same story with
    # a stronger claim — its terms come from the body's own calibration tags,
    # which no camera setting reaches at all.
    rebuilt = {**profile, **look_render_info(
        cal, style, tweaks, as_shot, dro=as_shot_dro > 0.0,
        borrowed=is_borrowed(raw_path, style),
        dro_gain=dro_gain_table(raw_path), dro_strength=strength,
        dro_grid=profile.get("profileDroGrid"), dro_level=level,
        sharpen=profile.get("profileSharpness"),
        spica=profile.get("profileSpica"),
        chroma_suppres=profile.get("profileChromaSuppres"),
        marble=profile.get("profileMarble")).to_json()}
    # The notes follow the request, not the decode: _limitations was handed this
    # rebuild's own borrowed/DRO state, so rebuilt already carries the right list
    # in the right order. It is the only producer of them for a sony profile.
    return rebuilt


BORROWED_NOTE = (
    "This Creative Look is not in the RAW: the body predates it. Its tone curve "
    "is Sony's own (byte-identical across bodies), but its chroma parameters are "
    "another body's — around 7% from what this one would have shipped."
)


DRO_NOTE = (
    "DRO's curve is exact, but it is applied to each pixel's own brightness "
    "rather than to the local average Sony's stage measures. Shadows and "
    "highlights land close; midtones are the weakest part."
)


CLARITY_NOTE = (
    f"Clarity's amplitudes are {CLARITY_CALIBRATION_BODY}'s. They live in the "
    "camera's calibration rather than in the RAW, so there is no way to read "
    "this body's own — the shape of the effect is right, its strength may not be."
)


SHARPEN_NOTE = (
    "Sharpening's kernel is keyed to sensor pixels, so it is the engine's "
    "operator only at full resolution; a downscaled render sharpens at its own "
    "scale."
)


SPICA_NOTE = (
    "Spica, the fine half of sharpening, reproduces the engine's classifier and "
    "weight tables exactly, but runs them on the display-encoded frame rather "
    "than on the engine's 14-bit plane — so its thresholds land on coarser "
    "steps. Like sharpening it is keyed to sensor pixels and only matches at "
    "full resolution, and its gain curves are one body's calibration."
)


def _limitations(dro: bool, borrowed: bool = False, dro_global: bool = False,
                 clarity: bool = False, sharpen: bool = False,
                 spica: bool = False) -> list[str]:
    """What this render cannot claim to match, for *this* shot.

    DRO has three states now. Applied without a grid: the curve is exact but the
    local mean it should be indexed by is not, so the note says which half is
    approximated. Applied with the grid: both halves are the engine's own and
    there is nothing to warn about. Off: nothing to say.

    There used to be a fourth — the body used DRO but wrote no curve, leaving the
    stage unreproduced. Manual levels retired it: the engine answers that case
    with a built-in preset, and so does look_render_info.

    Clarity only earns a line when it is actually doing something: its tables
    are one body's, and unlike the tone curve there is nothing in the file to
    check them against.

    The two sharpening stages are the reverse — each earns a line precisely
    *because* it is reproduced. Off, there is nothing to qualify; on, what they
    cannot match only exists because the stage is running.
    """
    out = ["Sony's ITP stage is not reproduced."]
    if dro and dro_global:
        out.append(DRO_NOTE)
    if clarity:
        out.append(CLARITY_NOTE)
    if sharpen:
        out.append(SHARPEN_NOTE)
    if spica:
        out.append(SPICA_NOTE)
    if borrowed:
        out.insert(0, BORROWED_NOTE)
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
