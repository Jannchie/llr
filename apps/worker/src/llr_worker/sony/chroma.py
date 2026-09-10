"""Sony's RGB2YCC stage — where the saturation and hue of a look actually live.

After MainGamma the engine converts to YCbCr and straight back, and the pair is
deliberately *not* an identity. The forward transform is the whole of a Creative
Look's colour character; the return trip is plain BT.601.

    Y  = (R*2432 + G*4864 + B*896) / 8192      luma weights, near but not BT.601
    u  = R - G;  v = B - G                     green differences, not luma ones
    v2 = cross[u >= 0 ? 1 : 3] * u + v         cross-coupled, each on the other's
    u2 = cross[v >= 0 ? 0 : 2] * v + u         sign, both on the *unmodified* u/v
    Cr = gain[u2 >= 0 ? 1 : 3] * u2            gain also branches on sign
    Cb = gain[v2 >= 0 ? 0 : 2] * v2

Gains that branch on sign give a hue-dependent gain; the cross terms give a hue
rotation. Those are exactly the two effects measured against the in-camera JPEG
long before this stage was found: gain 0.64..1.45 with a few degrees of rotation.

Eight signed shorts drive it, and they are in the RAW: 0x7842 is the base and
0x7843..0x7846 four illuminant deltas blended by weight (1024 = 1.0). Verified
against the engine's own interpolated values, bit for bit.

Black & White and Sepia need nothing extra: their eight values are all zero, so
both gains are zero, Cb = Cr = 0, and R = G = B = Y falls out on its own.

Verified pixel by pixel against the running engine on a whole frame: Y is
bit-identical, Cb and Cr differ by at most 1 in 14 bits (rounding).
"""

from __future__ import annotations

import sys
from functools import lru_cache
from pathlib import Path

import numpy as np

from .chromasuppres import ChromaSuppresTerms, chroma_suppres_gain
from .lut3d import apply_lut3d_float
from .sr2 import LUMA_LUT_FLAT, LUMA_LUT_KNEE, LookCalibration

CHROMA_BASE_TAG = 0x7842
CHROMA_ILLUMINANT_TAGS = (0x7843, 0x7844, 0x7845, 0x7846)

LUMA_WEIGHTS = (2432, 4864, 896)   # at lv+0x218f6, right after the tone LUT
LUMA_SHIFT = 8192
ILLUMINANT_UNIT = 1024             # a weight of 1024 means 1.0
CHROMA_LIMIT = 0.5                 # the engine clamps Cb/Cr to +-8192 of 16383

# BT.601, in the engine's own fixed point (/10000).
_R_CR, _G_CR, _G_CB, _B_CB = 1.4020, 0.7141, 0.3441, 1.7720

# YGamma (RVA 0x36f030) sits between the two halves and touches Y alone — its
# two chroma planes come out bit-identical. Its full form is
#
#     black = -((bl + (bl>>31 & 3)) >> 2) * (1/512) * 32767
#     scale = 512 / ((0x200 - wl) - black_int)
#     Y' = trunc(clamp(((max(0, lut[Y]) - black) * scale - pivot) * contrast
#                      + pivot, 0, 16383))
#
# bl and wl read 0 on every frame measured, which drops black and scale. Pivot
# and contrast do not: they are the *Fade* setting, read out of the RAW's two
# ten-entry tables (sr2.LUMA_PIVOT_TAG / LUMA_CONTRAST_TAG). At Fade 0 the pivot
# is 0 and the whole thing is a plain gain, which is why Fade looked absent for
# so long — every frame in the sample corpus was shot at Fade 0.
#
# Checked against the engine's own in/out on whole frames: 100.0000% bit-exact
# over 2M pixels at Fade 0 *and* at Fade 5, where the pivot is 10624 and the
# contrast 0.789 (sony_repro/tools/ygamma_verify.py). Rounding must be
# truncation; round-to-nearest matches only half the pixels.
#
# The LUT is *not* droppable, which cost a while to find out. On the FL and VV2
# frames the whole sample corpus was measured on it really is near identity — it
# departs by at most 16 in 16383, in deep shadow — so it was left out. Standard
# and Neutral get a different table: identity to Y=8192 and then slope 0.90625,
# a highlight knee, so lut[12288] = 11904 and lut[16383] = 15614. Without it a
# Standard frame's highlights came out 2-3% bright and clipped (measured at
# export on DSC03036: the stage's transfer is x1.0545 up to 8192 but only
# x1.009 by 15360, against the flat x1.0547 the pivot/contrast line alone gives).
#
# Which table applies is the shot's own choice, read from two per-look SR2 tags
# — sr2.LUMA_LUT_C_TAG / LUMA_LUT_D_TAG and the note there.
LUMA_FULL_SCALE = 16383.0          # the engine's luma range, and the pivot's units
LUMA_CONTRAST_UNIT = 16384.0       # tag 0x780e's units: 16384 means x1.0

# Edit's 色彩复制 = 高级 does not only add the 3-D LUT (lut3d.py) after this
# stage: it swaps YGamma's table *and* its contrast as well, so the one switch
# moves both. The contrast it swaps in is the same for both families — the eight
# looks whose own 0x780e entry is 16384 get 17280 too, which is why an FL frame
# rendered at its own 1.0 came out dark against Edit's own 高级 export. Verified
# to 100.0000% bit-exactness on six export tiles per family (tests/test_ygamma.py
# and the fixtures beside it).
LUMA_CONTRAST_ADVANCED_UNITS = 17280
LUMA_CONTRAST_ADVANCED = LUMA_CONTRAST_ADVANCED_UNITS / LUMA_CONTRAST_UNIT

#: The engine's own tables, dumped from calib+0x318fc at export.
LUMA_LUT_DATA = Path(__file__).resolve().parent / "data" / "ygamma_luts.npz"
#: Why 32768 and not 16384: the engine's Y plane is int16 and overshoots at this
#: point — a captured tile reaches 16531 — so the table has to be indexable past
#: full scale. Only the first 16384 entries can ever be reached from a float
#: pipeline whose y is clamped to 1.0, which is what ships to the browser.
LUMA_LUT_SIZE = 32768
LUMA_LUT_WIRE = 16384
_LUMA_LUT_NAMES = {LUMA_LUT_KNEE: "c4096_d14848", LUMA_LUT_FLAT: "c1024_d16384"}
#: 高级 has its own pair, dumped from the same address with the setting on. Same
#: two families, same selector — only the entries differ, and they differ from
#: Y=1 upward rather than only past a knee (adv[8192] is 8176, not 8192).
_LUMA_LUT_ADVANCED = "adv_"
#: Selectors already reported. One line per unknown pair, not one per frame.
_LUMA_LUT_SEEN: set[tuple[int, int]] = set()


@lru_cache(maxsize=1)
def _luma_luts() -> dict[str, np.ndarray]:
    """All four tables, loaded once and handed out read-only (they are shared)."""
    names = [n for name in _LUMA_LUT_NAMES.values()
             for n in (name, _LUMA_LUT_ADVANCED + name)]
    with np.load(LUMA_LUT_DATA) as z:
        tables = {name: z[name].astype(np.uint16) for name in names}
    for table in tables.values():
        table.flags.writeable = False
    return tables


def luma_lut(cal: LookCalibration, advanced: bool = False) -> np.ndarray:
    """The table YGamma indexes Y through for this look. uint16[32768].

    `advanced` is Edit's 色彩复制 = 高级: the same family, the other dump. It is
    the same switch that adds the 3-D LUT, so a caller that turns one on turns
    both on — see apply_chroma.

    An unseen selector falls back to the near-identity family rather than
    raising: it is the eight-look majority, so a body writing a pair nobody has
    dumped renders at worst like the looks that share its shape. The one stderr
    line is how such a body gets noticed.
    """
    key = (int(cal.luma_lut_key[0]), int(cal.luma_lut_key[1]))
    name = _LUMA_LUT_NAMES.get(key)
    if name is None:
        if key not in _LUMA_LUT_SEEN:
            _LUMA_LUT_SEEN.add(key)
            print(f"llr: unknown YGamma LUT selector (0x780c, 0x780d) = {key} for "
                  f"look {cal.name!r}; falling back to {_LUMA_LUT_NAMES[LUMA_LUT_FLAT]}",
                  file=sys.stderr)
        name = _LUMA_LUT_NAMES[LUMA_LUT_FLAT]
    return _luma_luts()[_LUMA_LUT_ADVANCED + name if advanced else name]


def blend_params(base: np.ndarray, deltas: np.ndarray, weights: np.ndarray) -> np.ndarray:
    """Base plus illuminant deltas: p = base + (sum_k delta[k] * w[k]) >> 10."""
    acc = (np.asarray(deltas, np.int64) * np.asarray(weights, np.int64)[:, None]).sum(0)
    return np.asarray(base, np.int64) + (acc >> 10)


def unpack_params(params: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Eight shorts -> four cross terms and four gains, as floats.

    The bit layout is not the obvious one: the cross terms are 9-bit signed at
    bit 2, and the gains 8-bit unsigned at bit 3 over 128 (the engine divides by
    64 and then halves). Reading them as plain /1024 fixed point gives gains
    below 1.0, which cannot produce the measured saturation — that mismatch is
    what led to the right unpacking.
    """
    p = np.asarray(params, np.int64)
    cross = (p[:4] >> 2) / 256.0
    gain = ((p[4:] >> 3) & 0xFF) / 128.0
    return cross.astype(np.float32), gain.astype(np.float32)


# The in-camera Saturation slider, -9..+9, as the engine sees it: the settings
# object holds 10 per step up to 2 and 5 per step after that, and both stages
# that use it read `1 + value/100`. Measured by dumping the settings struct at
# every setting (sony_repro/tools/settings_probe.py).
SATURATION_STEPS = (0, 10, 20, 25, 30, 35, 40, 45, 50, 55)


def saturation_factor(setting: int) -> float:
    """One Saturation setting -> the factor both halves of the stage use.

    Sony applies this twice in opposite directions: RGB2YCC divides its gains by
    it, and ZcTaskSIMDHueSaturation multiplies both chroma planes back by it
    afterwards. The two nearly cancel, so the slider's whole visible effect is
    what the clamp in between does — about 1% at +9, and about 5% at -9, where
    the intermediate chroma is 2.3x larger and clips.
    """
    i = min(abs(int(setting)), len(SATURATION_STEPS) - 1)
    v = SATURATION_STEPS[i] * (1 if setting >= 0 else -1)
    return 1.0 + v / 100.0


def rgb_to_ycc(rgb: np.ndarray, cross: np.ndarray, gain: np.ndarray,
               saturation: float = 1.0,
               ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Display-encoded RGB in [0, 1] -> Y, Cb, Cr (chroma centred on zero).

    `saturation` is applied the way the engine applies it: the gains are divided
    by it before the clamp and the result multiplied back after, which is why
    the setting is nearly a no-op except where the clamp bites.
    """
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    y = (r * LUMA_WEIGHTS[0] + g * LUMA_WEIGHTS[1] + b * LUMA_WEIGHTS[2]) / LUMA_SHIFT
    u, v = r - g, b - g
    v2 = np.where(u >= 0, cross[1], cross[3]) * u + v
    u2 = np.where(v >= 0, cross[0], cross[2]) * v + u
    cr = np.clip(np.where(u2 >= 0, gain[1], gain[3]) / saturation * u2,
                 -CHROMA_LIMIT, CHROMA_LIMIT) * saturation
    cb = np.clip(np.where(v2 >= 0, gain[0], gain[2]) / saturation * v2,
                 -CHROMA_LIMIT, CHROMA_LIMIT) * saturation
    return y, cb, cr


def luma_terms(cal: LookCalibration, fade: int = 0,
               advanced: bool = False) -> tuple[float, float]:
    """This shot's YGamma pivot and contrast, for its Fade setting.

    The engine indexes both tables at Fade*10 and interpolates in tenths, so a
    whole-number Fade — all the camera can write — lands exactly on an entry.
    Out-of-range values clamp rather than extrapolate, matching the engine: past
    90 it holds the last entry, and Fade has no negative side to begin with.

    `advanced` replaces the contrast with 高级's own, which is the one constant
    for every look rather than a per-look table entry. The *pivot* is left as the
    shot's: both frames the setting was measured on were at Fade 0, where the
    pivot is 0 either way, so there is no evidence that 高级 touches it and
    inventing one would silently move every faded frame.
    """
    i = int(np.clip(fade, 0, cal.luma_pivot.size - 1))
    contrast = (LUMA_CONTRAST_ADVANCED if advanced
                else float(cal.luma_contrast[i]) / LUMA_CONTRAST_UNIT)
    return float(cal.luma_pivot[i]) / LUMA_FULL_SCALE, contrast


def luma_gamma(y: np.ndarray, pivot: float, contrast: float,
               lut: np.ndarray | None = None) -> np.ndarray:
    """YGamma: table, then pull luma toward `pivot` by `contrast`, and clip.

    Chroma is untouched — the engine's own two chroma planes come out of this
    stage bit-identical.

    At Fade 0 the pivot is zero and the second half is the plain gain it was
    first measured as; with Fade on, the pivot rises to about 0.65 and the
    contrast drops below one, which lifts the shadows while pulling the
    highlights down — the faded look, done in one stage on luma alone.

    `lut` is this look's table (luma_lut). The index is the engine's own — Y
    truncated onto 0..16383 — so the float path steps exactly where the integer
    stage does instead of interpolating across a knee the engine has as a step.
    None keeps the old identity behaviour, which is right only for a caller that
    has no calibration to read the selector from.
    """
    y = np.asarray(y)
    if lut is not None:
        idx = np.clip(np.trunc(y * LUMA_FULL_SCALE), 0, len(lut) - 1).astype(np.intp)
        y = (np.asarray(lut)[idx] / LUMA_FULL_SCALE).astype(y.dtype, copy=False)
    return np.clip((y - pivot) * contrast + pivot, 0.0, 1.0)


def ygamma_planes(y16: np.ndarray, lut: np.ndarray,
                  pivot16: int = 0, contrast: float = 1.0) -> np.ndarray:
    """The exact integer stage on an engine plane: int16 Y in, int16 Y out.

    Kept beside the float path for the same reason chromasuppres keeps one: it
    is what the bit-exactness claim is checked against, on tiles captured out of
    the running engine. `pivot16` is on the 0..16383 scale, not normalised.

    The negatives are clamped at the *index* rather than after: the engine's own
    `max(0, Y)` sits inside the table lookup (RVA 0x36f030), and its Y plane does
    go negative. Rounding is truncation — round-to-nearest matches half the
    pixels and no more.
    """
    y = np.asarray(y16, np.int16).astype(np.int64)
    v = np.asarray(lut, np.int64)[np.maximum(y, 0)]
    return np.trunc(np.clip((v - pivot16) * contrast + pivot16,
                            0.0, LUMA_FULL_SCALE)).astype(np.int16)


def ycc_to_rgb(y: np.ndarray, cb: np.ndarray, cr: np.ndarray) -> np.ndarray:
    """Plain BT.601. All of the styling is in the forward direction."""
    return np.clip(np.stack([
        y + _R_CR * cr,
        y - _G_CR * cr - _G_CB * cb,
        y + _B_CB * cb,
    ], -1), 0.0, 1.0)


def apply_chroma(rgb: np.ndarray, cross: np.ndarray, gain: np.ndarray,
                 pivot: float = 0.0, contrast: float = 1.0,
                 saturation: float = 1.0,
                 suppress: ChromaSuppresTerms | None = None,
                 lut: np.ndarray | None = None,
                 lut3d: bool = False,
                 lut_advanced: np.ndarray | None = None,
                 contrast_advanced: float | None = None) -> np.ndarray:
    """The whole YCC section: display-encoded RGB in, the same out.

    Grey does not survive this unchanged — YGamma moves it. That is the engine's
    behaviour, not a bug in the chroma maths: YGamma itself leaves both chroma
    planes bit-identical, and only Y moves.

    `suppress` adds ChromaSuppres, which the engine runs between RGB2YCC and
    YGamma (chromasuppres.py). Without it the two chroma planes really do come
    out of this section untouched; with it they lose 1/256 in the mid-tones and
    fade out above the shot's `hiY`. Its gain reads the luma *before* YGamma,
    because that is the plane the engine's own stage sees — before the table as
    well as before the pivot/contrast line.

    `lut` is YGamma's table for this look (luma_lut); without it the stage is
    the pivot/contrast line alone, which is right only for the eight looks whose
    table is near identity.

    `lut3d` is Edit's 色彩复制 = 高级, "advanced colour reproduction", and it is
    the one setting here the user chooses: Edit's own default is 标准. It is not
    only the extra stage it was first taken for. 高级 changes two things at once,
    and the flag drives both:

      * YGamma runs off a different table and a different contrast —
        `lut_advanced` and `contrast_advanced`, which a caller with a calibration
        gets from `luma_lut(cal, advanced=True)` and `luma_terms(cal, fade,
        advanced=True)`. Leave either None and that half is skipped, which is
        right only for a caller that has no calibration to read them from;
      * ZcTask3DLut then runs where the engine runs it (lut3d.py), after YGamma
        and before the return trip. It works on the engine's integer planes, so
        the float y/cb/cr are converted to them and back (apply_lut3d_float
        documents the scale). Bright saturated pixels come down in luma and lose
        chroma; near the top of the Y range colour goes fully neutral.

    The pivot is shared between the two settings — see luma_terms for why.
    """
    y, cb, cr = rgb_to_ycc(rgb, cross, gain, saturation)
    if suppress is not None:
        f = chroma_suppres_gain(y, suppress)
        cb, cr = cb * f, cr * f
    if lut3d:
        if lut_advanced is not None:
            lut = lut_advanced
        if contrast_advanced is not None:
            contrast = contrast_advanced
    y = luma_gamma(y, pivot, contrast, lut)
    if lut3d:
        y, cb, cr = apply_lut3d_float(y, cb, cr)
    return ycc_to_rgb(y, cb, cr)
