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

CHROMA_BASE_TAG = 0x7842
CHROMA_ILLUMINANT_TAGS = (0x7843, 0x7844, 0x7845, 0x7846)

LUMA_WEIGHTS = (2432, 4864, 896)   # at lv+0x218f6, right after the tone LUT
LUMA_SHIFT = 8192
ILLUMINANT_UNIT = 1024             # a weight of 1024 means 1.0
CHROMA_LIMIT = 0.5                 # the engine clamps Cb/Cr to +-8192 of 16383

# BT.601, in the engine's own fixed point (/10000).
_R_CR, _G_CR, _G_CB, _B_CB = 1.4020, 0.7141, 0.3441, 1.7720


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


def rgb_to_ycc(rgb: np.ndarray, cross: np.ndarray, gain: np.ndarray,
               ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Display-encoded RGB in [0, 1] -> Y, Cb, Cr (chroma centred on zero)."""
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    y = (r * LUMA_WEIGHTS[0] + g * LUMA_WEIGHTS[1] + b * LUMA_WEIGHTS[2]) / LUMA_SHIFT
    u, v = r - g, b - g
    v2 = np.where(u >= 0, cross[1], cross[3]) * u + v
    u2 = np.where(v >= 0, cross[0], cross[2]) * v + u
    cr = np.clip(np.where(u2 >= 0, gain[1], gain[3]) * u2, -CHROMA_LIMIT, CHROMA_LIMIT)
    cb = np.clip(np.where(v2 >= 0, gain[0], gain[2]) * v2, -CHROMA_LIMIT, CHROMA_LIMIT)
    return y, cb, cr


def ycc_to_rgb(y: np.ndarray, cb: np.ndarray, cr: np.ndarray) -> np.ndarray:
    """Plain BT.601. All of the styling is in the forward direction."""
    return np.clip(np.stack([
        y + _R_CR * cr,
        y - _G_CR * cr - _G_CB * cb,
        y + _B_CB * cb,
    ], -1), 0.0, 1.0)


def apply_chroma(rgb: np.ndarray, cross: np.ndarray, gain: np.ndarray) -> np.ndarray:
    """The whole stage: display-encoded RGB in, display-encoded RGB out."""
    return ycc_to_rgb(*rgb_to_ycc(rgb, cross, gain))
