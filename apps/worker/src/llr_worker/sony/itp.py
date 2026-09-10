r"""Edit.exe's demosaic, `ZcTaskSIMDITP`: the entry point and the backend switch.

The operator itself -- how every stage was decoded and why each formula is the
shape it is -- lives in `itp_numpy`, which stays the reference implementation and
is still tested against the engine dumps. This module picks which of the two
transcriptions runs it:

* `itp_numba` (default) computes each stage in one compiled pass over the tile.
  It is bit-identical to `itp_numpy` -- same operation order, same float32
  rounding points, same unwritten zeros, checked on the fixture, on random
  mosaics and on a whole 33 MP frame -- and 13x faster at the same thread count
  (that frame on a 12900K, six threads: 7.51 s -> 0.56 s), because the numpy
  path is bandwidth bound rather than compute bound.
* `itp_numpy` runs when `LLR_ITP_BACKEND=numpy` is set (read once, at import),
  and automatically if `numba` cannot be imported.

Every stage function -- `convert`, `lp_h`, `aggregate`, `anisotropy`, `pack` and
the rest -- is re-exported here from `itp_numpy`, so `sony_repro/tools` and the
tests keep reaching them through `sony.itp`.
"""
from __future__ import annotations

import os
import sys
from concurrent.futures import ThreadPoolExecutor

import numpy as np

from . import itp_numpy
from .itp_numpy import (  # noqa: F401  (re-exported: the decoded stages)
    AGGREGATE_TAPS,
    ANISO_BIAS,
    ANISO_FLOOR,
    ANISO_HI,
    ANISO_LO,
    ANISO_SLOPE,
    COEF_CROSS,
    COEF_SAME,
    CRIT_P0,
    CRIT_P1,
    CRIT_P2,
    ENGINE_WHITE,
    HALO,
    LEVEL_MAX,
    MALVAR,
    F,
    Rect,
    aggregate,
    anisotropy,
    base_candidates,
    colour_diff_fields,
    confidence,
    convert,
    cost_h,
    cost_v,
    direction_weight,
    edge_weight,
    green_candidates,
    green_mask,
    lerp,
    lp_h,
    lp_v,
    malvar,
    mid_h,
    mid_v,
    pack,
    wb_gains,
)

#: Which transcription runs. Read once from LLR_ITP_BACKEND at import; set it to
#: "numpy" for an A/B against the reference. Tests flip this attribute directly.
BACKEND = (os.environ.get("LLR_ITP_BACKEND") or "numba").strip().lower()

try:
    from . import itp_numba as _numba_impl
except Exception as exc:  # pragma: no cover - depends on the install
    _numba_impl = None
    print(f"llr: numba unavailable ({exc}); sony.itp falls back to numpy", file=sys.stderr)

#: Rows of output per strip. Must be even: the tile's CFA phase is the padded
#: frame's, so an odd start row swaps green with red/blue and the strip seam
#: shows (true of the numpy path too -- it is why the seam test uses 40).
#: 128 to 384 all measure within 10% of each other on the 33 MP frame; 128 is
#: the smallest of those, and each worker holds eight float32 planes of
#: (strip_rows + 64) x width, so it is also the cheapest (~44 MB per worker).
STRIP_ROWS = 128

#: Twelve strips in flight. Measured on the 33 MP frame (12900K, 8 P + 8 E
#: cores, strip_rows=128): 1 thread 2.78 s, 4 -> 0.74, 6 -> 0.56, 12 -> 0.43,
#: 24 -> 0.39. Past twelve the E-cores only add stragglers for 10% more, at
#: twice the peak memory, so this stops there.
WORKERS = min(12, os.cpu_count() or 6)


def _kernel(mosaic: np.ndarray):
    """The numba module to use for this array, or None for the numpy path."""
    if _numba_impl is None or BACKEND == "numpy":
        return None
    if mosaic.dtype != np.uint16:
        # the kernel is typed for the documented uint16 mosaic; anything else
        # (a float probe, an int32 view) goes down the reference path rather
        # than compiling a second specialisation for it
        return None
    return _numba_impl


def itp_tile(mosaic: np.ndarray, gains: np.ndarray, black: float,
             rect: Rect) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """One engine tile: uint16 mosaic (black included) -> (R, G, B) uint16 planes.

    `rect` is the valid rectangle inside the tile (the engine's is the tile inset
    by its 16-pixel halo). Outputs are trustworthy from about 12 pixels inside
    `rect`; the margin holds each stage's unwritten zeros.
    """
    impl = _kernel(mosaic)
    if impl is None:
        return itp_numpy.itp_tile(mosaic, gains, black, rect)
    x0, y0, x1, y1 = (int(v) for v in rect)
    return impl.itp_tile(np.ascontiguousarray(mosaic),
                         np.ascontiguousarray(gains, F), float(black), x0, y0, x1, y1)


def demosaic(mosaic: np.ndarray, wb_rggb: tuple[float, float, float, float], black: float,
             strip_rows: int = STRIP_ROWS, workers: int = WORKERS) -> np.ndarray:
    """The whole frame: RGGB uint16 mosaic (black included) -> float32 (h, w, 3)
    camera RGB on llr's scale (engine 14-bit planes / ENGINE_WHITE).

    Reflect-pads by HALO (even, so the CFA phase is preserved) and runs the tile
    pipeline over horizontal strips that overlap by HALO on each side, taking
    only each strip's interior. Row count per strip is a cache/memory knob, not a
    result knob: every *even* strip_rows gives the same array, bit for bit. An
    odd one does not -- it starts a tile on the wrong CFA phase.
    """
    if mosaic.ndim != 2:
        raise ValueError("mosaic must be a 2-D Bayer plane")
    impl = _kernel(mosaic)
    if impl is None:
        return itp_numpy.demosaic(mosaic, wb_rggb, black, strip_rows, workers)
    h, w = mosaic.shape
    gains = wb_gains(wb_rggb)
    padded = np.pad(mosaic, HALO, mode="reflect")
    out = np.empty((h, w, 3), F)
    scale = F(1.0 / ENGINE_WHITE)
    pw = w + 2 * HALO
    bl = float(black)

    def run(r0: int) -> None:
        r1 = min(h, r0 + strip_rows)
        tile = padded[r0:r1 + 2 * HALO, :]
        th = tile.shape[0]
        impl.strip(tile, gains, bl, HALO // 2, HALO // 2, pw - HALO // 2, th - HALO // 2,
                   HALO, th - HALO, HALO, pw - HALO, scale, out[r0:r1])

    starts = list(range(0, h, strip_rows))
    # the kernels are nogil, so strips really do overlap on cores
    n_workers = max(1, min(workers, len(starts)))
    if n_workers == 1:
        for r0 in starts:
            run(r0)
    else:
        with ThreadPoolExecutor(max_workers=n_workers) as pool:
            list(pool.map(run, starts))
    return out


def warmup() -> None:
    """Compile the kernels (both entry points) so the first frame does not.

    Cold, with an empty numba cache, this is 6.0 s; warm, from the on-disk cache
    `cache=True` writes next to the module, 0.09 s. The daemon calls it on a
    background thread at startup -- the kernels are nogil, so it does not block
    the event loop.
    """
    impl = _kernel(np.zeros((2, 2), np.uint16))
    if impl is None:
        return
    mos = np.zeros((2 * HALO + 4, 2 * HALO + 4), np.uint16)
    demosaic(mos, (1024.0, 1024.0, 1024.0, 1024.0), 512.0, strip_rows=mos.shape[0], workers=1)
    itp_tile(mos, wb_gains((1024.0, 1024.0, 1024.0, 1024.0)), 512.0,
             (HALO // 2, HALO // 2, mos.shape[1] - HALO // 2, mos.shape[0] - HALO // 2))


# -- rawpy front end --------------------------------------------------------------

#: LibRaw's raw_pattern for an RGGB sensor described as "RGBG": indices into
#: color_desc, so [[R, G], [G2, B]].
_RGGB_PATTERN = [[0, 1], [3, 2]]


def supports(raw) -> bool:
    """Whether this rawpy handle is a 2x2 RGGB mosaic with the inputs ITP needs.

    The engine's phase logic (green on (x ^ y) & 1, the per-row normalisers, the
    S = (-1)^(x+y) sign) is written for RGGB; other CFAs go back to LibRaw.
    """
    try:
        pattern = raw.raw_pattern
        return (pattern is not None and pattern.tolist() == _RGGB_PATTERN
                and raw.color_desc == b"RGBG" and raw.raw_image.ndim == 2
                and raw.camera_whitebalance is not None and len(raw.camera_whitebalance) >= 4
                and raw.black_level_per_channel is not None)
    except Exception:
        return False


def orient(arr: np.ndarray, flip: int) -> np.ndarray:
    """Apply LibRaw's `sizes.flip` the way postprocess does (cli.camera_crop_rect
    uses the same convention for the crop rectangle): 3 is 180 degrees, 5 is
    90 counter-clockwise, 6 is 90 clockwise."""
    if flip == 3:
        return np.ascontiguousarray(np.rot90(arr, 2))
    if flip == 5:
        return np.ascontiguousarray(np.rot90(arr, 1))
    if flip == 6:
        return np.ascontiguousarray(np.rot90(arr, -1))
    return arr


def demosaic_rawpy(raw, half_size: bool = False) -> np.ndarray:
    """Camera RGB from an open rawpy handle, in the frame postprocess would give.

    Reads `raw.raw_image` -- so a RAW-domain denoiser that wrote the mosaic in
    place (denoise_raw_inplace) is honoured, which is the engine's own order:
    RawNRSIMD, then ITP. Output is float32 (h, w, 3) on llr's camera-RGB scale
    (see ENGINE_WHITE), oriented per `sizes.flip`, and for `half_size` binned
    2x2 after demosaicing so the preview carries the same character as the
    export (LibRaw's half_size skips demosaicing altogether).
    """
    sizes = raw.sizes
    top, left = int(sizes.top_margin), int(sizes.left_margin)
    h, w = int(sizes.height), int(sizes.width)
    mosaic = raw.raw_image[top:top + h, left:left + w]
    cwb = [float(v) for v in raw.camera_whitebalance]
    # rawpy reports cam_mul in color_desc order (R, G, B, G2); a body that
    # leaves G2 unset reports 0 there, in which case it equals G.
    g2 = cwb[3] if cwb[3] > 0 else cwb[1]
    wb_rggb = (cwb[0], cwb[1], g2, cwb[2])
    black = float(np.mean([float(v) for v in raw.black_level_per_channel]))
    rgb = demosaic(np.ascontiguousarray(mosaic), wb_rggb, black)
    if half_size:
        hh, ww = (h // 2) * 2, (w // 2) * 2
        rgb = rgb[:hh, :ww].reshape(hh // 2, 2, ww // 2, 2, 3).mean(axis=(1, 3), dtype=F)
    return orient(rgb, int(sizes.flip or 0))
