"""Sony's Dynamic Range Optimizer, reproduced.

The engine's stage (ZcTaskVatr) is a bilateral-grid local tone map:

    gain = 2 ** (Tone(Mlog) - Mlog)

applied to all three channels alike, which is why it never shifts colour. Both
halves are solved (PIPELINE.md 7.10.4.6 / 7.10.5):

* `Tone` is a piecewise cubic Bezier the *camera* wrote into the RAW, per shot,
  at SR2 tags 0x781b / 0x781c. sr2.dro_curve rebuilds it; it matches the
  engine's own buffer on every frame checked, to float32 noise.
* `Mlog` is a local log-luminance mean sliced out of an 8x6x14 bilateral grid
  the engine builds from the RAW Bayer data. `dro_grid` rebuilds that, and
  against the engine's own dumped grid it is exact to float32.

Two paths ship, and they differ only in what indexes the tone curve:

* `dro_grid` — the real thing. The consumer interpolates the grid to get Mlog.
* `dro_gain_table` — the fallback, substituting the pixel's own log luminance
  for the local mean, which turns Sony's local operator into a global one along
  the same curve. Used when the grid cannot be built (non-Bayer source, odd
  geometry).

Measured whole-frame against the engine's own stage output on its strongest DRO
frame, rebuilding the grid here and interpolating it as the shader does:

    leaving DRO out    2.029 /255 RMS
    global fallback    1.241 /255
    grid               0.075 /255      97.4% of pixels bit-identical

The strength control is not invented either. Across 16 frames the departure
curves are 98.8% one principal component, so a shot's DRO is very nearly a
single scalar times a fixed shape, and scaling the departure moves along the
family Sony actually uses rather than off it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, NamedTuple

import numpy as np

from .dro_presets import dro_preset_curve
from .sr2 import DRO_CURVE_POINTS, DRO_LOG_CEILING, dro_curve

# Neutral white sits exactly on the curve's last sample: the engine forms its
# luminance as 0.299R + 0.587G + 0.114B over a 14-bit plane, and
# log2(16383 * 0.5) is DRO_LOG_CEILING. Normalised luma therefore enters the
# curve's domain as log2(luma * DRO_LUMA_WHITE).
DRO_LUMA_WHITE = 8191.5
# BT.601, which is what the engine uses here (its own weights are these halved,
# against a plane it has already shifted down by one bit).
DRO_LUMA_WEIGHTS = (0.299, 0.587, 0.114)
# Samples of the shipped gain table. Uniform in log luminance like the engine's
# own 104-entry table, so the shadows — where DRO does nearly all of its work —
# get the resolution instead of the highlights. 128 samples left a 0.08 error in
# the gain at the curve's sharpest bend, which is visible; 512 holds it to 0.005.
DRO_LUT_SIZE = 512
DRO_MAX_STRENGTH = 2.0
# What the grid path puts white on. The engine forms BT.601 over its 14-bit
# plane, so normalised white enters the luma axis at log2(16383) = 14.0 and is
# clamped to DRO_LOG_CEILING. Deliberately not DRO_LUMA_WHITE: that one exists
# so the *global* fallback leaves white untouched, which the grid does not need
# because its local mean rarely reaches the top of the axis in the first place.
DRO_GRID_LUMA_WHITE = 16383.0

# ---------------------------------------------------------------------------
# The bilateral grid.
#
# Everything below is the engine's own construction, read out of ZcParameter's
# vatr block and verified against the engine's dumped intermediates at every
# stage (sony_repro/tools/vatr_grid.py). The tables are firmware constants:
# identical on every frame checked, so they are baked in here rather than read
# per shot.
# ---------------------------------------------------------------------------

DRO_GRID_NX, DRO_GRID_NY = 8, 6          # coarse (spatial) cells, vatr+0x08/0x0c
DRO_GRID_BINS = 14                       # luma bins, vatr+0xd8
DRO_FINE_X, DRO_FINE_Y = 112, 84         # fine cells across the source, +0x14/0x18
DRO_COARSE = 14                          # fine cells per coarse cell, vatr+0x10
DRO_DOWNSAMPLE = 4                       # vatr+0x2c
# vatr+0xe0: the luma-axis kernel, indexed by |j - k| clamped to 8. Exact
# multiples of 1/256, which is how you can tell it is a table and not a fit.
DRO_BIN_KERNEL = np.array([226, 155, 83, 35, 11, 3, 1, 1, 1], dtype=np.float64) / 256.0
# vatr+0x104: per-bin blend between the bin itself and the local weighted mean.
# 1 at both ends means deep shadow and highlight get no local adaptation at all.
DRO_BLEND = np.array(
    [1.0, 0.75, 0.5, 0.25, 0, 0, 0, 0, 0, 0.25, 0.5, 0.75, 1.0, 1.0], dtype=np.float64
)
# vatr+0x48/0x4c/0x50. The render's luma is BT.601 halved (they sum to 0.5), but
# the *grid builder* multiplies its averaged green by 0x50 rather than 0x4c, so
# the weights it actually uses sum to 0.2635 and red outweighs green. That looks
# like a slip in Sony's code — the decompiler showed the offset twice and it was
# tempting to call it an artefact — but it is real, and reproducing DRO requires
# copying it. Getting this wrong shifts the whole grid by 1.35 stops.
DRO_W_R, DRO_W_G, DRO_W_B = 0.14949999749660492, 0.29350000619888306, 0.05700000002980232
# vatr+0x496c: the log2 LUT's step. The engine interpolates log2 off a table of
# log2(8i) rather than calling it, and the difference is visible in the low bins.
DRO_LOG_STEP = 8.0
DRO_LOG_DIRECT = 16384.0                 # at or above this it calls log2 for real


class DroGrid(NamedTuple):
    """The engine's bilateral grid, plus how to address it from image space.

    `num` and `den` are (ny, nx, bins). They are interpolated *separately* and
    divided at the end — Mlog is not the interpolation of num/den, so both have
    to travel.

    `uv` is the affine map from normalised image coordinates to continuous
    coarse-cell coordinates: `u = uv[0] + uv[1]*x + uv[2]*y`, likewise `v` from
    uv[3..5]. It carries the sensor margin, the camera crop and the orientation
    flip, so the consumer never has to know about any of them. The cross terms
    are non-zero only for the 90-degree flips.
    """

    num: np.ndarray
    den: np.ndarray
    uv: tuple[float, float, float, float, float, float]


def _to_log(v: np.ndarray) -> np.ndarray:
    """The engine's `to_log` (FUN_140189930): a lerped table of log2(8i)."""
    knots = np.arange(2050, dtype=np.float64)
    table = np.where(knots == 0, 0.0, np.log2(np.maximum(knots * DRO_LOG_STEP, 1e-30)))
    out = np.zeros_like(v)
    high = v >= DRO_LOG_DIRECT
    out[high] = np.log2(v[high])
    mid = (v >= 0.0) & ~high
    t = v[mid] / DRO_LOG_STEP
    i = t.astype(np.int64)
    out[mid] = table[i] + (table[np.minimum(i + 1, 2049)] - table[i]) * (t - i)
    return out


def _grid_geometry(width: int, height: int) -> tuple[int, int, int, int] | None:
    """unit and origin, exactly as FUN_1401887b0 derives them from the source."""
    unit_x = ((width >> 2) // DRO_FINE_X) * 4
    unit_y = ((height >> 2) // DRO_FINE_Y) * 4
    if unit_x < DRO_DOWNSAMPLE or unit_y < DRO_DOWNSAMPLE:
        return None
    org_x = (width - DRO_FINE_X * unit_x) >> 1 & 0xFFFE
    org_y = (height - DRO_FINE_Y * unit_y) >> 1 & 0xFFFE
    return unit_x, unit_y, org_x, org_y


def _fine_means(bayer: np.ndarray, black: float) -> np.ndarray | None:
    """RAW Bayer -> the 84x112 grid of fine-cell mean log luminances.

    FUN_140188b60 walks the sensor on a 4-pixel lattice taking one 2x2 quad per
    step, then FUN_140189bd0 box-averages that into fine cells. Both are folded
    together here because the intermediate is a 1792x1260 float image nothing
    else wants.
    """
    height, width = bayer.shape
    geom = _grid_geometry(width, height)
    if geom is None:
        return None
    unit_x, unit_y, org_x, org_y = geom
    step = DRO_DOWNSAMPLE
    cell_x, cell_y = unit_x // step, unit_y // step
    cols = DRO_FINE_X * cell_x
    rows = DRO_FINE_Y * cell_y
    if org_y + rows * step > height or org_x + cols * step > width:
        return None

    rr = org_y + np.arange(rows) * step
    cc = org_x + np.arange(cols) * step
    src = bayer.astype(np.int64)
    q00 = src[np.ix_(rr, cc)]
    q01 = src[np.ix_(rr, cc + 1)]
    q10 = src[np.ix_(rr + 1, cc)]
    q11 = src[np.ix_(rr + 1, cc + 1)]
    bl = round(black)
    # Positional, not pattern-driven: the engine hardcodes these slots too.
    luma = (
        np.maximum(q00 - bl, 0) * DRO_W_R
        + np.maximum(((q01 + q10) >> 1) - bl, 0) * DRO_W_B
        + np.maximum(q11 - bl, 0) * DRO_W_B
    ).astype(np.float64)
    ylog = _to_log(luma)
    return ylog.reshape(DRO_FINE_Y, cell_y, DRO_FINE_X, cell_x).mean(axis=(1, 3))


def _grid_from_fine(fine: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Fine-cell means -> (num, den), i.e. steps 5-7 of PIPELINE 7.10.5."""
    nx, ny, bins = DRO_GRID_NX, DRO_GRID_NY, DRO_GRID_BINS
    # Per coarse cell, a 14-bin histogram of its 196 fine means. Note this axis
    # is scaled by TOP/14 while the *lookup* axis is unit-width; the engine is
    # inconsistent here and both halves have been checked against it.
    idx = np.clip((fine / DRO_LOG_CEILING * bins).astype(np.int64), 0, bins - 1)
    blocks = idx.reshape(ny, DRO_COARSE, nx, DRO_COARSE).transpose(0, 2, 1, 3)
    flat = blocks.reshape(ny * nx, DRO_COARSE * DRO_COARSE)
    hist = np.stack([np.bincount(row, minlength=bins)[:bins] for row in flat])
    hist = hist.reshape(ny, nx, bins) / float(DRO_COARSE * DRO_COARSE)

    # Four-neighbour smoothing: centre always 1/2, the other 1/2 split equally
    # between however many neighbours exist (so 1/8 inside, 1/6 on an edge,
    # 1/4 in a corner). Not a 3x3 — the diagonals are not read.
    shifts = ((0, 1), (0, -1), (1, 0), (-1, 0))
    total = np.zeros_like(hist)
    count = np.zeros((ny, nx), dtype=np.float64)
    for dy, dx in shifts:
        src = np.roll(np.roll(hist, dy, axis=0), dx, axis=1)
        ok = np.ones((ny, nx), dtype=bool)
        if dy > 0:
            ok[:dy] = False
        elif dy < 0:
            ok[dy:] = False
        if dx > 0:
            ok[:, :dx] = False
        elif dx < 0:
            ok[:, dx:] = False
        total += np.where(ok[..., None], src, 0.0)
        count += ok
    smooth = 0.5 * hist + 0.5 * total / count[..., None]

    j = np.arange(bins)
    kernel = DRO_BIN_KERNEL[np.minimum(np.abs(j[:, None] - j[None, :]), 8)]
    den = smooth @ kernel.T
    weighted = (smooth * j) @ kernel.T
    num = (j * den - weighted) * DRO_BLEND + weighted
    return num, den


def _image_to_grid_uv(raw: Any, crop: tuple[int, int, int, int] | None) -> tuple[
    float, float, float, float, float, float
] | None:
    """Affine map from normalised output-image coordinates to coarse-cell space.

    The output image is LibRaw's visible area, flipped to the camera's
    orientation and then trimmed to `crop` (in that flipped frame). The grid is
    indexed in raw sensor coordinates, so this walks the chain backwards. It is
    evaluated at three corners rather than composed symbolically — the flips are
    easy to get subtly wrong and the numeric route cannot drift out of step with
    apply_camera_crop.
    """
    sizes = getattr(raw, "sizes", None)
    if sizes is None:
        return None
    geom = _grid_geometry(int(raw.raw_image.shape[1]), int(raw.raw_image.shape[0]))
    if geom is None:
        return None
    unit_x, unit_y, org_x, org_y = geom
    span_x, span_y = DRO_COARSE * unit_x, DRO_COARSE * unit_y
    vis_w, vis_h = int(sizes.width), int(sizes.height)
    left, top = int(sizes.left_margin), int(sizes.top_margin)
    flip = int(sizes.flip or 0)
    # The flipped frame the crop rect lives in.
    frame_w, frame_h = (vis_h, vis_w) if flip in (5, 6) else (vis_w, vis_h)
    cx, cy, cw, ch = crop if crop is not None else (0, 0, frame_w, frame_h)

    def to_grid(u: float, v: float) -> tuple[float, float]:
        fx, fy = cx + u * cw, cy + v * ch
        if flip == 3:
            vx, vy = vis_w - fx, vis_h - fy
        elif flip == 5:
            vx, vy = vis_w - fy, fx
        elif flip == 6:
            vx, vy = fy, vis_h - fx
        else:
            vx, vy = fx, fy
        return (
            (left + vx - org_x - span_x * 0.5) / span_x,
            (top + vy - org_y - span_y * 0.5) / span_y,
        )

    u0, v0 = to_grid(0.0, 0.0)
    u1, v1 = to_grid(1.0, 0.0)
    u2, v2 = to_grid(0.0, 1.0)
    return (u0, u1 - u0, u2 - u0, v0, v1 - v0, v2 - v0)


def dro_grid(raw: Any, crop: tuple[int, int, int, int] | None = None) -> DroGrid | None:
    """Rebuild the engine's bilateral grid from an open RawPy handle.

    `crop` is the camera crop rect in the flipped output frame, i.e. the first
    element of cli.camera_crop_rect's return — pass None when the render keeps
    the whole visible area. Returns None when the source is not a single-plane
    Bayer image the engine's geometry fits.
    """
    try:
        bayer = raw.raw_image
        black = float(np.mean(np.asarray(raw.black_level_per_channel, dtype=np.float64)))
    except (AttributeError, TypeError, ValueError):
        return None
    if bayer is None or bayer.ndim != 2:
        return None
    fine = _fine_means(bayer, black)
    if fine is None:
        return None
    uv = _image_to_grid_uv(raw, crop)
    if uv is None:
        return None
    num, den = _grid_from_fine(fine)
    return DroGrid(num=num, den=den, uv=uv)


def dro_grid_json(grid: DroGrid | None) -> dict[str, Any] | None:
    """Flatten a grid for the wire. Row-major (y, x, bin), num and den apart.

    Seven significant digits, not repr: these end up in a float32 texture, and
    full precision would nearly double the header this rides in for a change to
    Mlog four orders of magnitude below anything visible. The affine map keeps
    its digits — an error there is a spatial offset, not a rounding one.
    """
    if grid is None:
        return None
    return {
        "nx": DRO_GRID_NX,
        "ny": DRO_GRID_NY,
        "bins": DRO_GRID_BINS,
        "num": [float(f"{v:.7g}") for v in grid.num.ravel()],
        "den": [float(f"{v:.7g}") for v in grid.den.ravel()],
        "uv": [float(v) for v in grid.uv],
    }


def dro_local_mean(grid: DroGrid, x: np.ndarray, y: np.ndarray, ylog: np.ndarray) -> np.ndarray:
    """Mlog by trilinear interpolation, the reference for the shader's version.

    `x`/`y` are normalised image coordinates. num and den are interpolated
    independently and divided at the end, which is what the engine does and is
    not the same as interpolating their ratio.
    """
    a0, a1, a2, b0, b1, b2 = grid.uv
    u = np.clip(a0 + a1 * x + a2 * y, 0.0, DRO_GRID_NX - 1.0)
    v = np.clip(b0 + b1 * x + b2 * y, 0.0, DRO_GRID_NY - 1.0)
    t = np.clip(ylog, 0.0, DRO_LOG_CEILING)
    iu, iv = np.floor(u).astype(np.int64), np.floor(v).astype(np.int64)
    it = np.floor(t).astype(np.int64)
    # The engine's own clamp: the upper bin is bins-1 once the lower one reaches
    # bins-2, so the top two bins share an edge rather than extrapolating.
    ih = np.where(it < DRO_GRID_BINS - 2, it + 1, DRO_GRID_BINS - 1)
    it = np.clip(it, 0, DRO_GRID_BINS - 1)
    fu, fv, ft = u - iu, v - iv, t - it
    num = np.zeros_like(t)
    den = np.zeros_like(t)
    for du in (0, 1):
        wu = fu if du else 1.0 - fu
        cu = np.minimum(iu + du, DRO_GRID_NX - 1)
        for dv in (0, 1):
            wv = fv if dv else 1.0 - fv
            cv = np.minimum(iv + dv, DRO_GRID_NY - 1)
            for dt in (0, 1):
                w = wu * wv * (ft if dt else 1.0 - ft)
                cb = ih if dt else it
                num += w * grid.num[cv, cu, cb]
                den += w * grid.den[cv, cu, cb]
    return np.where(den == 0.0, DRO_LOG_CEILING, num / np.maximum(den, 1e-30))


def dro_gain_table(
    raw_path: str | Path, strength: float = 1.0, size: int = DRO_LUT_SIZE,
) -> list[float] | None:
    """Multiplicative gain against log luminance, or None if the shot has no DRO.

    Entry i is the gain at `Ylog = i / (size - 1) * DRO_LOG_CEILING`; a consumer
    holding normalised luma L looks up `log2(L * DRO_LUMA_WHITE)` on that scale.
    Clamped at both ends, as the engine clamps its own.
    """
    curve = dro_curve(raw_path)
    if curve is None:
        return None
    return gain_table_from_curve(curve, strength, size)


def dro_level_gain_table(
    level: int, strength: float = 1.0, size: int = DRO_LUT_SIZE,
) -> list[float]:
    """The same table for a manual DRO level, which needs no file at all.

    The preset curves are Edit.exe's, not the shot's, so this works on a frame
    whose RAW carries no curve — the case the as-shot path has to give up on.
    """
    return gain_table_from_curve(dro_preset_curve(level), strength, size)


def gain_table_from_curve(
    curve: np.ndarray, strength: float = 1.0, size: int = DRO_LUT_SIZE,
) -> list[float]:
    """Departure from identity, resampled and exponentiated into a gain table."""
    strength = max(0.0, min(DRO_MAX_STRENGTH, float(strength)))
    src = np.arange(DRO_CURVE_POINTS) * (DRO_LOG_CEILING / DRO_CURVE_POINTS)
    at = np.linspace(0.0, DRO_LOG_CEILING, size)
    # The table stops at 12.875 while normalised white reaches 12.9998, so the
    # last 1% of the range has to be extrapolated. The engine holds the tone
    # *value* there, which works out to dimming white by a ninth — but only
    # because its local mean puts almost nothing that high in the first place.
    # A global operator sends every clipped highlight there, so copying that
    # rule dims exactly the pixels the engine was lifting: measured against the
    # engine's own output it costs 1.39/255 against 1.24. Holding the departure
    # (i.e. staying inside the table's domain) leaves white alone, which is both
    # closer and the only sane behaviour for a control someone can turn up.
    departure = np.interp(at, src, curve - src) * strength
    return [float(v) for v in np.exp2(departure)]


def scale_dro_gain(table: list[float], strength: float) -> list[float]:
    """Re-scale an as-shot gain table without going back to the file.

    Strength scales the departure in the log domain, so this is a power, not a
    multiply: doubling the strength squares the gain. Moving the control is a
    profile rebuild rather than a decode, and this is the whole reason it can be.
    """
    strength = max(0.0, min(DRO_MAX_STRENGTH, float(strength)))
    if strength == 1.0:
        return list(table)
    if strength == 0.0:
        return [1.0] * len(table)
    return [float(g) ** strength for g in table]


def apply_dro(rgb: np.ndarray, raw_path: str | Path, strength: float = 1.0) -> np.ndarray:
    """The same gain, applied here rather than shipped to a shader.

    Only for offline checking against the engine — the render applies it in the
    shader, where the strength can move without re-decoding. Kept in step with
    dro_gain_table by construction: both come from the same curve.
    """
    table = dro_gain_table(raw_path, strength)
    if table is None:
        return rgb
    luma = np.maximum(rgb @ np.array(DRO_LUMA_WEIGHTS, dtype=rgb.dtype), 1e-9)
    ylog = np.log2(luma * DRO_LUMA_WHITE)
    at = np.linspace(0.0, DRO_LOG_CEILING, len(table))
    gain = np.interp(np.clip(ylog, 0.0, DRO_LOG_CEILING), at, np.asarray(table))
    return rgb * gain[..., None]
