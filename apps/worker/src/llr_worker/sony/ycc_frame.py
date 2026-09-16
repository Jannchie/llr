"""Sony's M/S-size ARW: the chroma LibRaw replicated, interpolated instead.

A Lossless Compressed RAW written at M or S size is not a mosaic. The body
demosaics and downsamples in camera and stores the result as a chroma-
subsampled YCbCr LJPEG (`SonyRawFileType: Sony Lossless Compressed RAW 2`,
LibRaw `sony_ycbcr_load_raw`): the S-size frames measured here are 4:2:2, one
Cb/Cr pair per two horizontal pixels; the decoder also carries a 4:2:0 path.
LibRaw fills the missing chroma by *replication* -- its 4:2:2 / 4:2:0 LJPEG
decoders hand every pixel of a site the same Cb/Cr -- and converts each pixel
to RGB (`ycc2rgb`: BT.601, float32, truncated). The result is a full-colour frame whose colour changes in
two-pixel steps: on a coloured edge that is not vertical, a gold spectacle
frame against skin, say, the step reads as a staircase once the image is
viewed at 1:1 or larger. Imaging Edge's own export of the same frame shows the
same pairs, only softened by its chroma stages, so it is a property of the
decode, not of the sensor.

This module undoes the replication. The BT.601 conversion is linear, so the
shared chroma of a pair is recovered from its two RGB values (the mean over
the pair, to average the truncation), the chroma plane is resampled to the
pixel grid with the triangle filter every JPEG decoder uses for the same job
(libjpeg's "fancy upsampling": sites centred between the pixels they served,
3/4 : 1/4 weights), and the change is applied as a *delta* through the same
matrix -- where the interpolated chroma equals the replicated one, the pixel
is bit-identical to LibRaw's. Luma is untouched by construction.

The chroma site position was checked against the body's own JPEG of the same
frame (the camera renders it from the full-resolution sensor): the centred
site correlates its chroma gradients best (0.488 against 0.439 / 0.443 for
left- and right-aligned), which is also the JFIF convention.

The subsampling is read off the pixels rather than off the file: rawpy does
not expose the LJPEG SOF, and a pair that shares Cb/Cr shares R-G and B-G
exactly, so the fraction of equal neighbouring differences names the layout
(and its phase) unambiguously. A frame that has already been through here
shows no pairs and is left alone, which makes the operation idempotent.
"""
from __future__ import annotations

from typing import Any

import numpy as np

F = np.float32

#: LibRaw sonycc.cpp ycc2rgb: R = Y + KR*Cr, G = Y - KGB*Cb - KGR*Cr, B = Y + KB*Cb.
KR, KGB, KGR, KB = 1.40200, 0.34414, 0.71414, 1.77200

#: (R-G, B-G) = M @ (Cr, Cb); its inverse recovers the pair's chroma.
_M = np.array([[KR + KGR, KGB], [KGR, KB + KGB]], np.float64)
_M_INV = np.linalg.inv(_M).astype(F)

#: Fraction of neighbouring pixels sharing chroma above which the axis counts
#: as subsampled. Measured: 0.99998 within pairs, 0.007 across them.
_SHARED_FRACTION = 0.9


def is_ycc_frame(raw: Any) -> bool:
    """True for a LibRaw handle holding one of these frames.

    They are the RGBG files with no CFA pattern and a four-channel image; a
    mosaic has a pattern, a rendered image never reaches rawpy.
    """
    if getattr(raw, "raw_pattern", None) is not None:
        return False
    image = getattr(raw, "raw_image", None)
    return (image is not None and image.ndim == 3 and image.shape[-1] == 4
            and image.dtype == np.uint16 and getattr(raw, "color_desc", None) == b"RGBG")


def _shared_fraction(diff: np.ndarray, axis: int, phase: int) -> float:
    """How often neighbours (starting at `phase`, stepping by two) hold the same value."""
    d = np.abs(np.diff(diff, axis=axis))
    d = d[:, phase::2] if axis == 1 else d[phase::2]
    return float((d < 0.5).mean()) if d.size else 0.0


def detect_subsampling(rgb: np.ndarray) -> tuple[tuple[int, int], tuple[int, int]] | None:
    """The chroma layout of a LibRaw-decoded YCC frame, from its pixels.

    Returns ((step_h, step_v), (phase_h, phase_v)): the step is 2 on an axis
    whose neighbours share chroma, and the phase is the column / row parity
    the pairs start on. None when neither axis is subsampled (4:4:4, or a
    frame already interpolated).
    """
    layout = []
    for axis in (1, 0):
        # Every fourth line across the axis under test is evidence enough.
        sub = rgb[::4] if axis == 1 else rgb[:, ::4]
        d = sub[..., 0].astype(F) - sub[..., 1].astype(F)
        fractions = [_shared_fraction(d, axis, phase) for phase in (0, 1)]
        best = int(np.argmax(fractions))
        layout.append((2, best) if fractions[best] >= _SHARED_FRACTION else (1, 0))
    (step_h, phase_h), (step_v, phase_v) = layout
    if step_h == 1 and step_v == 1:
        return None
    return (step_h, step_v), (phase_h, phase_v)


def _along(plane: np.ndarray, axis: int) -> np.ndarray:
    """The 2-D plane with `axis` last: a view, so writes through it land in the caller's array."""
    return plane if axis == 1 else plane.T


def _upsample_axis(sites: np.ndarray, axis: int, length: int, phase: int) -> np.ndarray:
    """libjpeg's fancy upsampling along one axis: 3/4 of the nearest site, 1/4 of the next.

    Sites sit centred between the two pixels they served; the first `phase`
    pixels and any odd tail take their nearest site unfiltered.
    """
    out_shape = (sites.shape[0], length) if axis == 1 else (length, sites.shape[1])
    out = np.empty(out_shape, F)
    s = _along(sites, axis)
    o = _along(out, axis)
    n = s.shape[-1]
    span = min(2 * n, length - phase)
    lo = o[:, phase:phase + span:2]          # the pixel before each site's centre
    hi = o[:, phase + 1:phase + span:2]      # the pixel after it
    m = lo.shape[-1]
    lo[:, 0] = s[:, 0]
    np.multiply(s[:, 1:m], 0.75, out=lo[:, 1:])
    lo[:, 1:] += 0.25 * s[:, :m - 1]
    k = hi.shape[-1]
    if k:
        np.multiply(s[:, :k], 0.75, out=hi)
        hi[:, :k - 1] += 0.25 * s[:, 1:k]
        hi[:, k - 1] += 0.25 * s[:, min(k, n - 1)]
    if phase:
        o[:, :phase] = s[:, :1]
    if phase + span < length:
        o[:, phase + span:] = s[:, n - 1:n]
    return out


def _site_mean(plane: np.ndarray, axis: int, phase: int) -> np.ndarray:
    """Mean over each pair along `axis`, pairs starting at `phase`."""
    a = _along(plane, axis)
    n = (a.shape[-1] - phase) // 2
    mean = 0.5 * (a[:, phase:phase + 2 * n:2] + a[:, phase + 1:phase + 2 * n:2])
    return mean if axis == 1 else mean.T


def interpolate_chroma(rgb: np.ndarray) -> tuple[int, int] | None:
    """Replace the replicated chroma of `rgb` (uint16, (H, W, >=3)) with interpolated chroma, in place.

    Returns the (step_h, step_v) it found, or None when the frame carried no
    replicated chroma and was left untouched. Pixels LibRaw clipped (a channel
    at 0 or 65535) no longer satisfy the linear model, so their chroma is not
    trusted for the pair and they are not changed.
    """
    layout = detect_subsampling(rgb)
    if layout is None:
        return None
    (step_h, step_v), (phase_h, phase_v) = layout
    h, w = rgb.shape[:2]
    r: np.ndarray = np.asarray(rgb[..., 0], F)
    g: np.ndarray = np.asarray(rgb[..., 1], F)
    b: np.ndarray = np.asarray(rgb[..., 2], F)
    weight: np.ndarray = np.asarray((r > 0) & (r < 65535) & (g > 0) & (g < 65535) & (b > 0) & (b < 65535), F)

    # The chroma each pixel currently carries; a pair's mean averages the
    # truncation of the two conversions. Clipped pixels are excluded from the
    # mean and a site with nothing left counts as neutral -- the only pixels
    # that can then move are its neighbours', by a quarter of the difference.
    d1, d2 = r - g, b - g
    cr = _M_INV[0, 0] * d1 + _M_INV[0, 1] * d2
    cb = _M_INV[1, 0] * d1 + _M_INV[1, 1] * d2
    steps = ((1, step_h, phase_h), (0, step_v, phase_v))
    site_cr, site_cb, site_w = cr * weight, cb * weight, weight
    for axis, step, phase in steps:
        if step == 2:
            site_cr, site_cb, site_w = (_site_mean(x, axis, phase) for x in (site_cr, site_cb, site_w))
    site_w = np.maximum(site_w, 0.5)
    site_cr /= site_w
    site_cb /= site_w
    for axis, step, phase in steps:
        if step == 2:
            length = h if axis == 0 else w
            site_cr = _upsample_axis(site_cr, axis, length, phase)
            site_cb = _upsample_axis(site_cb, axis, length, phase)

    d_cr = (site_cr - cr) * weight
    d_cb = (site_cb - cb) * weight
    rgb[..., 0] = np.clip(np.round(r + KR * d_cr), 0, 65535).astype(np.uint16)
    rgb[..., 1] = np.clip(np.round(g - KGB * d_cb - KGR * d_cr), 0, 65535).astype(np.uint16)
    rgb[..., 2] = np.clip(np.round(b + KB * d_cb), 0, 65535).astype(np.uint16)
    return step_h, step_v


def interpolate_chroma_inplace(raw: Any) -> str | None:
    """Run :func:`interpolate_chroma` on a rawpy handle's image, for the postprocess that follows.

    Mutates ``raw.raw_image`` like the RAW-domain denoiser does, so
    ``raw.postprocess()`` scales and white-balances the interpolated pixels.
    Returns a one-line note for the decode log, or None when the handle is
    not one of these frames.
    """
    if not is_ycc_frame(raw):
        return None
    found = interpolate_chroma(raw.raw_image)
    if found is None:
        return "chroma already full resolution"
    return f"chroma 4:2:{'0' if found[1] == 2 else '2'} interpolated"
