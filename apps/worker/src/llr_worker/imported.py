"""Decode already-rendered images (JPEG/PNG/TIFF) into the same working space
the RAW path delivers: scene-linear ProPhoto RGB, D50.

The browser's render pipeline hardcodes that contract, so supporting non-RAW
sources is entirely a decode-side problem: undo the file's transfer function,
rotate its primaries into ProPhoto, and nothing downstream needs to change.

Colour management is matrix-only, and deliberately split:

* The **primaries** come from the embedded ICC profile's rXYZ/gXYZ/bXYZ
  colorants, which are already adapted to the D50 PCS. Reading them gives an
  exact matrix for any matrix/TRC profile rather than only the handful we can
  recognise by name.
* The **transfer function** is matched by profile description, because Pillow
  exposes no way to read the TRC curves. Unrecognised profiles fall back to the
  sRGB curve, which is right for the overwhelming majority of files that carry
  a custom-named profile with standard-ish encoding.

A full ImageCms profile-to-profile transform would handle even LUT-based
profiles, but it pulls in littleCMS rendering-intent semantics (its default is
perceptual, not relative colorimetric) for a case that essentially does not
occur in photographic files.
"""

from __future__ import annotations

from io import BytesIO
from pathlib import Path
from typing import Any, Callable

import numpy as np
from PIL import Image, ImageCms, ImageOps

from .dcp import XYZ_D50_TO_PROPHOTO, srgb_decode_float

# sRGB primaries adapted to D50 (Lindbloom). Used when a file carries no ICC
# profile at all — untagged photographic images are sRGB by universal convention.
SRGB_TO_XYZ_D50 = np.array(
    [
        [0.4360747, 0.3850649, 0.1430804],
        [0.2225045, 0.7168786, 0.0606169],
        [0.0139322, 0.0971045, 0.7141733],
    ],
    dtype=np.float32,
)


def _gamma_to_linear(exponent: float) -> Callable[[np.ndarray], np.ndarray]:
    return lambda value: np.power(value, exponent)


def _prophoto_to_linear(value: np.ndarray) -> np.ndarray:
    # ROMM RGB: gamma 1.8 with a linear toe of slope 16 below 16/512.
    return np.where(value < 0.031248, value / 16.0, np.power(value, 1.8))


def _rec2020_to_linear(value: np.ndarray) -> np.ndarray:
    alpha, beta = 1.09929682680944, 0.018053968510807
    return np.where(value < beta * 4.5, value / 4.5, np.power((value + alpha - 1.0) / alpha, 1.0 / 0.45))


# Matched against a lowercased ICC profile description. Iterated longest-first
# so "adobe rgb (1998)" cannot be shadowed by a bare "rgb" entry.
_TRC_BY_DESCRIPTION: dict[str, Callable[[np.ndarray], np.ndarray]] = {
    "srgb": srgb_decode_float,
    "display p3": srgb_decode_float,
    "displayp3": srgb_decode_float,
    "dci-p3": _gamma_to_linear(2.6),
    "adobe rgb": _gamma_to_linear(563.0 / 256.0),
    "prophoto": _prophoto_to_linear,
    "romm": _prophoto_to_linear,
    "rec2020": _rec2020_to_linear,
    "rec. 2020": _rec2020_to_linear,
    "bt.2020": _rec2020_to_linear,
}


_TRC_NEEDLES_LONGEST_FIRST = sorted(_TRC_BY_DESCRIPTION, key=len, reverse=True)


def _match_transfer(description: str) -> tuple[str, Callable[[np.ndarray], np.ndarray]]:
    lowered = description.lower()
    for needle in _TRC_NEEDLES_LONGEST_FIRST:
        if needle in lowered:
            return needle, _TRC_BY_DESCRIPTION[needle]
    return "srgb (assumed)", srgb_decode_float


def _matrix_from_icc(profile: ImageCms.ImageCmsProfile) -> np.ndarray | None:
    """Build RGB -> XYZ(D50) from the profile's colorant tags.

    ICC colorants are stored relative to the D50 PCS white, so they compose
    directly with XYZ_D50_TO_PROPHOTO without a further chromatic adaptation.
    Each Pillow colorant is ((X, Y, Z), (x, y, Y)); we want the XYZ triple as a
    matrix column. LUT-based profiles carry no colorants and return None.
    """
    inner: Any = profile.profile
    try:
        columns = [
            getattr(inner, "red_colorant")[0],
            getattr(inner, "green_colorant")[0],
            getattr(inner, "blue_colorant")[0],
        ]
    except (AttributeError, TypeError, IndexError):
        return None
    if any(column is None or len(column) != 3 for column in columns):
        return None
    matrix = np.array(columns, dtype=np.float32).T
    # A colorant matrix that cannot map white to a sane luminance is not one we
    # can trust; better to fall back to sRGB than to render a wildly wrong image.
    if not np.isfinite(matrix).all() or abs(float(np.linalg.det(matrix))) < 1e-6:
        return None
    return matrix


def _resolve_color_space(icc_bytes: bytes | None) -> tuple[np.ndarray, Callable[[np.ndarray], np.ndarray], str]:
    if not icc_bytes:
        return SRGB_TO_XYZ_D50, srgb_decode_float, "sRGB (untagged)"

    try:
        profile = ImageCms.ImageCmsProfile(BytesIO(icc_bytes))
        description = (ImageCms.getProfileDescription(profile) or "").strip()
    except (ImageCms.PyCMSError, OSError, ValueError):
        return SRGB_TO_XYZ_D50, srgb_decode_float, "sRGB (unreadable ICC)"

    _, transfer = _match_transfer(description)
    matrix = _matrix_from_icc(profile)
    if matrix is None:
        return SRGB_TO_XYZ_D50, transfer, f"{description or 'ICC'} (no colorants; sRGB primaries)"
    return matrix, transfer, description or "embedded ICC"


def _normalized_pixels(image: Image.Image) -> np.ndarray:
    """Image pixels as float32 in [0, 1], preserving 16-bit input where Pillow does."""
    array = np.asarray(image)
    if array.dtype == np.uint16:
        return np.divide(array, 65535.0, dtype=np.float32)
    if array.dtype in (np.float32, np.float64):
        return array.astype(np.float32, copy=False)
    return np.divide(array, 255.0, dtype=np.float32)


def decode_image_linear(path: Path) -> tuple[np.ndarray, dict[str, Any]]:
    """Decode a rendered image to linear ProPhoto (D50) float32 HWC.

    Returns the pixels plus a colour-profile record shaped like the RAW path's,
    so the frontend can treat both sources uniformly.
    """
    with Image.open(path) as opened:
        # EXIF orientation must be applied here: unlike the RAW path, nothing
        # downstream rotates these pixels.
        image = ImageOps.exif_transpose(opened)
        icc_bytes = image.info.get("icc_profile")
        if image.mode not in ("RGB", "I;16", "I;16B", "I;16L"):
            image = image.convert("RGB")
        pixels = _normalized_pixels(image)

    if pixels.ndim == 2:
        pixels = np.repeat(pixels[:, :, None], 3, axis=2)
    if pixels.shape[-1] > 3:
        # Drop alpha: the pipeline is opaque end to end, and premultiplying here
        # would bake the matte into pixels the user may want to recover.
        pixels = pixels[:, :, :3]

    to_xyz_d50, transfer, description = _resolve_color_space(icc_bytes)

    # Clip before the transfer function: the curves are only defined on [0, 1],
    # and a float TIFF can legitimately carry out-of-range values.
    linear = transfer(np.clip(pixels, 0.0, 1.0)).astype(np.float32, copy=False)
    linear = linear @ (XYZ_D50_TO_PROPHOTO @ to_xyz_d50).T.astype(np.float32)
    np.clip(linear, 0.0, None, out=linear)

    info = {
        "kind": "rendered-image",
        "matrix": f"{description} to linear ProPhoto",
        "toneCurveSamples": 0,
        "workingSpace": "linear-prophoto-d50",
        "profileToneCurve": None,
        "sourceProfile": description,
        "note": (
            "Rendered image, not RAW: highlights were clipped by the camera or "
            "encoder, so there is no recovery headroom above white."
        ),
    }
    return np.ascontiguousarray(linear, dtype=np.float32), info
