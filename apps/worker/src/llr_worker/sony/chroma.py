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

import numpy as np

from .sr2 import LookCalibration

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
# The LUT is identity above Y ~ 1024 and departs from it by at most 16 in 16383
# below that, so it is dropped here: the approximation is bounded at 0.1% and
# confined to deep shadow. Removing it left a consistent luma bias — measured
# against the engine's own frames on eight shots, median 0.0139 too dark before,
# 0.0009 after.
LUMA_FULL_SCALE = 16383.0          # the engine's luma range, and the pivot's units
LUMA_CONTRAST_UNIT = 16384.0       # tag 0x780e's units: 16384 means x1.0


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


def luma_terms(cal: LookCalibration, fade: int = 0) -> tuple[float, float]:
    """This shot's YGamma pivot and contrast, for its Fade setting.

    The engine indexes both tables at Fade*10 and interpolates in tenths, so a
    whole-number Fade — all the camera can write — lands exactly on an entry.
    Out-of-range values clamp rather than extrapolate, matching the engine: past
    90 it holds the last entry, and Fade has no negative side to begin with.
    """
    i = int(np.clip(fade, 0, cal.luma_pivot.size - 1))
    return (float(cal.luma_pivot[i]) / LUMA_FULL_SCALE,
            float(cal.luma_contrast[i]) / LUMA_CONTRAST_UNIT)


def luma_gamma(y: np.ndarray, pivot: float, contrast: float) -> np.ndarray:
    """YGamma: pull luma toward `pivot` by `contrast`, and clip. Chroma is untouched.

    At Fade 0 the pivot is zero and this is the plain gain it was first measured
    as; with Fade on, the pivot rises to about 0.65 and the contrast drops below
    one, which lifts the shadows while pulling the highlights down — the faded
    look, done in one stage on luma alone.
    """
    return np.clip((y - pivot) * contrast + pivot, 0.0, 1.0)


def ycc_to_rgb(y: np.ndarray, cb: np.ndarray, cr: np.ndarray) -> np.ndarray:
    """Plain BT.601. All of the styling is in the forward direction."""
    return np.clip(np.stack([
        y + _R_CR * cr,
        y - _G_CR * cr - _G_CB * cb,
        y + _B_CB * cb,
    ], -1), 0.0, 1.0)


def apply_chroma(rgb: np.ndarray, cross: np.ndarray, gain: np.ndarray,
                 pivot: float = 0.0, contrast: float = 1.0,
                 saturation: float = 1.0) -> np.ndarray:
    """The whole YCC section: display-encoded RGB in, the same out.

    Grey does not survive this unchanged — YGamma moves it. That is the engine's
    behaviour, not a bug in the chroma maths: the two chroma planes really are
    untouched, and only Y moves.
    """
    y, cb, cr = rgb_to_ycc(rgb, cross, gain, saturation)
    return ycc_to_rgb(luma_gamma(y, pivot, contrast), cb, cr)
