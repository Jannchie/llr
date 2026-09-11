r"""Edit.exe's demosaic, `ZcTaskSIMDITP`: what it computes, and the frame around it.

The stages themselves are the compiled kernels in `itp_numba`, one pass per
stage; this module is the entry point (`demosaic`, `itp_tile`, `warmup`), the
rawpy front end, and the record of how the operator was decoded.

This is the step llr had never reproduced: LibRaw's AHD was standing in for it,
and the measured gap (notes/measured-chroma-gap.md §2.19.1, §2.20) sat exactly
here -- with denoising off on both sides, llr's demosaic left the noise ~25%
more chromatic than Edit's (0.848 against 0.678 chroma/luma), and Edit's
demosaic halves the chroma/luma ratio relative to a neutral one while keeping
*more* luma detail. Both are properties of this operator.

How it was decoded (sony_repro/tools/itp_flow_probe.py, itp_dump_probe.py,
itp_verify.py; per-stage disassembly summaries under tmp/itp_summ/): the
processor object's whole vtable was read at runtime, which turned the already
decoded scalar orchestrators (static-itp-spica.md §2.5/§2.6) into the SIMD
data flow for free; then every stage's inputs and outputs were dumped from one
exec and each formula was checked against them on three frames (two
bodies' worth of white balance, ISO 320/2000/4000). Every stage is bit-exact
except the two that accumulate nine or more float32 terms, which agree to
float32 rounding (max 2.4e-4 on values ~1000); end to end the three uint16
output planes match the engine to within 1 LSB on a handful of pixels.

The data flow. `W` is the mosaic converted to float32 (black subtracted,
white-balance gain applied); every plane is full-tile, float32.

    orch_110  direction weights
        cost_h / cost_v   v = (2c - l - r)/4 over same-colour taps (spacing 2);
                          second output keeps |v| on green sites only
        aggregate         5x5 weighted sum of that sparse map (see aggregate())
        direction_weight  m1 = A/(A+B), A = max(0, aggH-1)+1/8, B likewise on aggV
        confidence        m3 = 0.5 * min(r, 1/r), r = (aggH+1)/(aggV+1)
    orch_118  the base plane (green everywhere)
        base_candidates   f1/f2: at green sites the [1,0,6,0,1]/8 low-pass of W,
                          elsewhere the midpoint of the two neighbours (H / V)
        green_candidates  e1/e2: 9-tap FIR of W with one coefficient set on green
                          sites and another on red/blue sites (H / V)
        d = (f + e)/2;  blend = (1-m1)*d_h + m1*d_v
        malvar            5x5 Malvar-style kernel on the green-only checkerboard
        anisotropy        a [0.5, 0.96) map from four directional activities
        base = (1-w)*blend + w*malvar,  w = max(m3, anisotropy)
    orch_190  the two colour-difference planes
        lp_h/lp_v, mid_h/mid_v   the same primitives as base_candidates, unmasked
        colour_diff_fields       four direction-filled (R-G) / (B-G) fields
        bD  = (1-m1)*t1 + m1*t2       b50 = (1-m1)*t3 + m1*t4
    pack   R = trunc(clamp(bD + base)), G = trunc(clamp(base)), B = trunc(clamp(b50 + base))
           clamped to 0..16383

The output domain: W = gain * (raw - black) with gain = trunc(WB * 33/32) / 2048
per RGGB phase (three frames, exact), so sensor white on green lands at
0.515625 * 15871 = 8184 -- Sony's tone LUT white index is 8192 (sony/tone.py),
which is how the rest of the Sony chain already reads camera RGB. Dividing the
uint16 planes by 8192 therefore drops straight into `apply_sony_profile`.

Tiling: the engine runs 1136x684 tiles with a 16-pixel halo and consumes only
the central 1024 pixels. Here the frame is padded by HALO (reflect, Bayer-safe)
and processed in horizontal strips with the same halo; strips are seamless
because no stage reaches further than 12 pixels (verified in the tests by
comparing strips against a whole-frame run).

Things measured rather than assumed, worth keeping:

* aggregate()'s row-0 taps are shifted right by two columns and its normaliser
  is chosen per *row* only. Both are literal in the disassembly and both are
  needed for bit-exactness; the symmetric reading is wrong.
* colour_diff_fields() picks the planes that feed the edge weight by the
  *centre* pixel's phase, not the side pixels'. The opposite reading matched
  exactly half of every plane.
* direction_weight()'s dead zone and regulariser (1.0 and 1/8) were reverse-
  fitted from the dumps before the parameter block confirmed them; they are not
  zero even though a dump of the block after the exec reads zero.
"""
from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor

import numpy as np

from . import itp_numba

F = np.float32

#: Halo around a strip / the frame. The engine uses 16 and consumes 40 pixels
#: inside its tile; the stages reach at most ~12 pixels, so 32 keeps the
#: consumed region well clear of any stage's unwritten margin.
HALO = 32

#: Sony's tone-LUT white index: the divisor that maps the engine's 14-bit
#: planes onto llr's camera RGB scale (sony/tone.py TONE_INDEX_WHITE).
ENGINE_WHITE = 8192.0

Rect = tuple[int, int, int, int]


def wb_gains(wb_rggb: tuple[float, float, float, float]) -> np.ndarray:
    """vt58's per-phase gains: trunc(WB * 33/32) / 2048.

    WB 1838/1024/1024/2155 -> 1895/1056/1056/2222, 2455/1024/1024/1782 ->
    2531/1056/1056/1837, 2503/1024/1024/1596 -> 2581/1056/1056/1645; all three
    frames reproduce the engine's W to 1e-10.
    """
    return np.array([np.trunc(float(v) * 33.0 / 32.0) / 2048.0 for v in wb_rggb], F)


def convert(mosaic: np.ndarray, gains: np.ndarray, black: float) -> np.ndarray:
    """vt58 (0x35ad60): uint16 mosaic -> float32 W, gain * (v - black) per phase."""
    W = np.empty(mosaic.shape, F)
    itp_numba._convert(np.ascontiguousarray(mosaic, np.uint16),
                       np.ascontiguousarray(gains, F), float(black), W)
    return W


#: Rows of output per strip. Must be even: the tile's CFA phase is the padded
#: frame's, so an odd start row swaps green with red/blue and the strip seam
#: shows (it is why the seam test uses 40).
#: 128 to 384 all measure within 10% of each other on the 33 MP frame; 128 is
#: the smallest of those, and each worker holds eight float32 planes of
#: (strip_rows + 64) x width, so it is also the cheapest (~44 MB per worker).
STRIP_ROWS = 128

#: Twelve strips in flight. Measured on the 33 MP frame (12900K, 8 P + 8 E
#: cores, strip_rows=128): 1 thread 2.78 s, 4 -> 0.74, 6 -> 0.56, 12 -> 0.43,
#: 24 -> 0.39. Past twelve the E-cores only add stragglers for 10% more, at
#: twice the peak memory, so this stops there.
WORKERS = min(12, os.cpu_count() or 6)


def itp_tile(mosaic: np.ndarray, gains: np.ndarray, black: float,
             rect: Rect) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """One engine tile: uint16 mosaic (black included) -> (R, G, B) uint16 planes.

    `rect` is the valid rectangle inside the tile (the engine's is the tile inset
    by its 16-pixel halo). Outputs are trustworthy from about 12 pixels inside
    `rect`; the margin holds each stage's unwritten zeros.
    """
    x0, y0, x1, y1 = (int(v) for v in rect)
    return itp_numba.itp_tile(np.ascontiguousarray(mosaic, np.uint16),
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
    h, w = mosaic.shape
    gains = wb_gains(wb_rggb)
    padded = np.pad(np.asarray(mosaic, np.uint16), HALO, mode="reflect")
    out = np.empty((h, w, 3), F)
    scale = F(1.0 / ENGINE_WHITE)
    pw = w + 2 * HALO
    bl = float(black)

    def run(r0: int) -> None:
        r1 = min(h, r0 + strip_rows)
        tile = padded[r0:r1 + 2 * HALO, :]
        th = tile.shape[0]
        itp_numba.strip(tile, gains, bl, HALO // 2, HALO // 2, pw - HALO // 2, th - HALO // 2,
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
