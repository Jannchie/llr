"""Sony's "advanced colour reproduction" — `ZcTask3DLut`, RVA 0x36f340.

The seventh stage of the engine's YCC section, and the only one the user can
switch off:

    RGB2YCC -> AreaComp -> ChromaSuppres -> YGamma -> **3DLut**
            -> SIMDSharpness -> SIMDSpica -> YCC2RGB

Imaging Edge runs it only when 色彩复制 is set to 高级 (Edit's default is 标准,
which is this stage absent). What it does, measured on the model below: bright
saturated pixels come down in luma and lose chroma, and near the top of the Y
range colour is pulled all the way back to neutral. That is most of what makes
Edit's output resemble the camera's own JPEG.

Per pixel, on the engine's three planes — Y on its 0..16383 scale (int16, and it
does overshoot), Cr and Cb unsigned and centred on 32768:

    u = Cb'*3.7e-05  + Cr'*1.401988          BT.601-ish colour differences,
    v = Cr'*0.000135 + Cb'*1.771978          u ~ R-Y and v ~ B-Y
    a = curve[trunc(clamp(u))] + 0x8000      the 1-D warp, signed index
    b = curve[trunc(clamp(v))] + 0x8000
    (a >> 11, b >> 11, Y >> 9)               5-bit grid coordinates
    (a & 2047, b & 2047, Y & 511)            11/11/9-bit fractions
    -> trilinear over a 33x33x33 x 3 int16 point grid
    -> Y' straight out; (u', v') back through the inverse 2x2 matrix

Every floating-point constant is rip-relative and was read out of Edit.exe; the
disassembly, the descriptor layout and the derivation are in
sony_repro/notes/static-3dlut.md, and sony_repro/tools/lut3d_model.py is the
reference this module is a port of.

**Both tables are static.** The same dump reproduces two bodies (ILCE-7CM2 and
ILCE-7M5), two different frames and all eleven Creative Looks — nineteen tiles,
three planes each, whole tile including the borders, **100.0000% bit-exact,
maxdiff 0**. So they ship as data rather than being read per file. The 1-D warp
curve is not even data: it is exactly

    curve[i] = sign(i) * min(trunc(32768 * (|i|/32768)^(2/3)), 32767)

on all 65537 entries (test_lut3d.py checks that against the dump), so only the
point grid is stored — data/lut3d_points.npz, 122 KB.

Why a *point* grid and not the engine's own table: the engine holds 33^3 cells
of 3 outputs x 8 corners = 1.7 MB, which is the same 107811 numbers expanded so
that one cache line holds all eight corners of a cell. `cell_grid()` rebuilds
that expansion; `lut3d_points()` is the compact form, and it is what ships to
the browser as public/sony-lut3d.bin.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import numpy as np

#: The compact point grid, dumped from the running engine (see the module note).
LUT3D_DATA = Path(__file__).resolve().parent / "data" / "lut3d_points.npz"

GRID_N = 33                # 33 grid points per axis...
GRID_LAST = 32             # ...of which only 0..31 are ever indexed; see below
STRIDE_V = GRID_N          # [desc+0x20]
STRIDE_U = GRID_N * GRID_N # [desc+0x20] * [desc+0x28] = 1089
SHIFT_Y = 9                # 14 - [desc+0x2c]: Y -> grid coordinate
SHIFT_C = 11               # 16 - [desc+0x30]: warped chroma -> grid coordinate
MASK_Y = 511               # [desc+0x34], the 9-bit Y fraction
MASK_C = 2047              # [desc+0x38], the 11-bit chroma fraction
WEIGHT_SUM = 512           # the eight trilinear weights always add up to this

CURVE_SIZE = 65537         # int16[65537], indexed by a *signed* value...
CURVE_CENTRE = 32768       # ...so index i lives at i + this
CURVE_EXP = 2.0 / 3.0

CHROMA_CENTRE = 32768.0    # xmm9; both chroma planes are unsigned around this
# The forward 2x2, xmm10..xmm13. Off-diagonal terms of 3.7e-05 and 1.35e-04 are
# not noise: they are in the binary and they are reproduced here verbatim.
M_CB_U, M_CR_U = 3.7e-05, 1.401988
M_CR_V, M_CB_V = 0.000135, 1.771978
# ...and its inverse on the way out, xmm14/xmm15 and K1/K2. 0.564341 = 1/1.771978
# and 0.713273 = 1/1.401988.
K_V_CB, K_U_CB = 0.564341, 5.4e-05
K_U_CR, K_V_CR = 0.713273, 1.5e-05
# comiss against a float32 pair, then the clamp itself is done in double.
CLAMP_LO_F, CLAMP_HI_F = np.float32(-32768.0), np.float32(32767.0)
CLAMP_LO, CLAMP_HI = -32768, 32767

#: Corner slot k inside a cell, as the (f1, f0, yf) offsets it holds. The order
#: is gray-coded, not binary — the engine's MAC walks the eight corners in this
#: sequence and the weights below are indexed to match.
GRAY = ((0, 0, 0), (0, 0, 1), (0, 1, 1), (0, 1, 0),
        (1, 0, 0), (1, 0, 1), (1, 1, 1), (1, 1, 0))

#: Where the float pipeline and the engine's planes meet. 1.0 in llr's chroma
#: path is 16383 on the engine's scale — the same LUMA_FULL_SCALE YGamma's table
#: is indexed and divided by — and the two chroma planes ride that same scale
#: offset by 0x8000. sony_repro/tools/ycc_exact.py is where that was pinned: it
#: compares llr's own `cr + 32768` against the engine's plane 1 pixel by pixel
#: and lands within 1 LSB, with Y bit-identical.
PLANE_SCALE = 16383.0
PLANE_CENTRE = 32768.0


@lru_cache(maxsize=1)
def warp_curve() -> np.ndarray:
    """The 1-D chroma warp, int16[65537] indexed by `i + CURVE_CENTRE`.

    Generated rather than stored: the closed form below reproduces the engine's
    dumped table on every one of the 65537 entries (test_lut3d.py). It is an
    odd, monotone power-law compression whose resolution near neutral is what
    makes a 32-step chroma axis enough — curve[1] = 32, curve[2] = 50.

    The int16 saturation at the very top is the engine's, not a choice: the
    formula overflows at |i| = 32768 and the engine's table holds 32767 there.

    The negative half is the busy one. The engine holds `data + 0x10000` and
    indexes it with a sign-extended value, which for a long time read as a
    header — measured over four tiles the index reaches -32768 (pinned by the
    clamp) and only +2337.
    """
    i = np.arange(-CURVE_CENTRE, CURVE_CENTRE + 1, dtype=np.int64)
    mag = np.trunc(CURVE_CENTRE * (np.abs(i) / CURVE_CENTRE) ** CURVE_EXP)
    curve = (np.sign(i) * np.minimum(mag, 32767)).astype(np.int16)
    curve.flags.writeable = False
    return curve


@lru_cache(maxsize=1)
def lut3d_points() -> np.ndarray:
    """The point grid, int16 (33, 33, 33, 3), indexed [iu][iv][iy][output].

    Axis 0 is u (~R-Y) after the warp, axis 1 is v (~B-Y), axis 2 is Y; the
    three outputs are (Y', u', v') and they carry the output's own magnitude
    1:1, because the eight weights sum to 512 and the MAC is shifted back by 9.

    Only 0..31 is ever reached on each axis — the coordinates are 5-bit — so the
    32nd layer of each axis exists solely to give the last cell its far corners.

    Handed out read-only: this is shared, cached, and the wire asset is written
    straight from it.
    """
    with np.load(LUT3D_DATA) as z:
        points = np.ascontiguousarray(z["points"], np.int16).reshape(GRID_N, GRID_N, GRID_N, 3)
    points.flags.writeable = False
    return points


@lru_cache(maxsize=1)
def cell_grid() -> np.ndarray:
    """The engine's own cell store, int16 (33^3, 3, 8), rebuilt from the points.

    Cell (iu, iv, iy) slot k holds the point at (iu+f0, iv+f1, iy+yf) with
    (f1, f0, yf) = GRAY[k] — verified against the dumped 1.7 MB block, bit for
    bit. Kept in this shape because the pixel loop wants all eight corners of a
    cell in one fancy-indexed fetch, exactly as the engine wants them in one
    cache line. The unreachable 32nd layer stays zero, as it is in the dump.
    """
    points = lut3d_points()
    grid = np.zeros((GRID_N, GRID_N, GRID_N, 3, 8), np.int16)
    i = np.arange(GRID_LAST)
    for k, (b1, b0, by) in enumerate(GRAY):
        grid[:GRID_LAST, :GRID_LAST, :GRID_LAST, :, k] = points[
            i[:, None, None] + b0, i[None, :, None] + b1, i[None, None, :] + by]
    return grid.reshape(-1, 3, 8)


def _trunc_i32(x: np.ndarray) -> np.ndarray:
    """cvttss2si / cvttsd2si: round toward zero, into int32."""
    return np.trunc(x).astype(np.int64).astype(np.int32)


def apply_lut3d_planes(planes: np.ndarray) -> np.ndarray:
    """The exact integer stage: uint16 (h, w, 3) of (Y, Cr, Cb), in and out.

    Y is read the way the engine reads it — `movzx`, i.e. unsigned — so a plane
    that overshot 16383 upstream indexes the top of the Y axis rather than
    wrapping. Cr and Cb are the unsigned planes centred on 32768.

    Bit-exact against Imaging Edge's own output on nineteen captured tiles; the
    fixture in tests/fixtures/lut3d_tile.npz is a crop of one of them.
    """
    y_u = planes[..., 0].astype(np.int64)
    cr = planes[..., 1].astype(np.float64) - CHROMA_CENTRE
    cb = planes[..., 2].astype(np.float64) - CHROMA_CENTRE

    # The forward matrix is computed in double and narrowed to float (cvtpd2ps)
    # before the bounds test, then truncated. Skipping the narrowing costs a
    # unit on large values, which is a whole grid cell after the >> 11.
    u = (cb * M_CB_U + cr * M_CR_U).astype(np.float32)
    v = (cr * M_CR_V + cb * M_CB_V).astype(np.float32)
    iu = np.where(u < CLAMP_LO_F, CLAMP_LO, np.where(u > CLAMP_HI_F, CLAMP_HI, _trunc_i32(u)))
    iv = np.where(v < CLAMP_LO_F, CLAMP_LO, np.where(v > CLAMP_HI_F, CLAMP_HI, _trunc_i32(v)))

    curve = warp_curve()
    a = curve[(iu + CURVE_CENTRE).astype(np.int64)].astype(np.int32) + np.int32(CURVE_CENTRE)
    b = curve[(iv + CURVE_CENTRE).astype(np.int64)].astype(np.int32) + np.int32(CURVE_CENTRE)

    f0 = (a & MASK_C).astype(np.int32)
    f1 = (b & MASK_C).astype(np.int32)
    yf = (y_u & MASK_Y).astype(np.int32)
    big_f0 = np.int32(MASK_C + 1) - f0
    big_f1 = np.int32(MASK_C + 1) - f1
    big_yf = np.int32(MASK_Y + 1) - yf

    cell = ((a >> SHIFT_C).astype(np.int64) * STRIDE_U
            + (b >> SHIFT_C).astype(np.int64) * STRIDE_V
            + (y_u >> SHIFT_Y))

    # The eight trilinear weights, all int32: multiply two fractions, drop three
    # bits, multiply the third, then round at 2^18 and shift 19. The `>> 3` is
    # *after* the multiply and really does throw those bits away — cancelling it
    # algebraically changes the answer. w0 is the remainder rather than a ninth
    # product, which is what keeps the sum at exactly 512 and parks every
    # rounding error on one corner; computing it costs +-1 somewhere else.
    half = np.int32(0x40000)
    p_f0_yf = (f0 * yf) >> 3
    p_f0_yf_hi = (f0 * big_yf) >> 3
    p_big_f0_yf = (big_f0 * yf) >> 3
    p_big_f0_yf_hi = (big_f0 * big_yf) >> 3
    w2 = (p_f0_yf * big_f1 + half) >> 19
    w6 = (p_f0_yf * f1 + half) >> 19
    w3 = (p_f0_yf_hi * big_f1 + half) >> 19
    w7 = (p_f0_yf_hi * f1 + half) >> 19
    w1 = (p_big_f0_yf * big_f1 + half) >> 19
    w5 = (p_big_f0_yf * f1 + half) >> 19
    w4 = (p_big_f0_yf_hi * f1 + half) >> 19
    w0 = np.int32(WEIGHT_SUM) - w7 - w6 - w5 - w4 - w3 - w2 - w1
    w = np.stack([w0, w1, w2, w3, w4, w5, w6, w7], axis=-1)

    corners = cell_grid()[cell]                       # (h, w, 3, 8) int16
    acc = np.einsum("...ck,...k->...c", corners.astype(np.int32), w, dtype=np.int32)
    acc >>= 9

    out = np.empty(planes.shape, np.uint16)
    # Y has a lower clamp and no upper one: the engine's `test/cmovs` sends the
    # negatives to zero and then stores 16 bits, so an overshoot *truncates*
    # rather than saturating. Transcribed, not tidied up.
    out[..., 0] = np.where(acc[..., 0] < 0, 0, acc[..., 0] & 0xFFFF).astype(np.uint16)

    # The two chroma accumulators go int32 -> float32 -> double before the
    # inverse matrix (cvtdq2ps then cvtps2pd). Going straight to double differs
    # by a unit on large values.
    ud = acc[..., 1].astype(np.float32).astype(np.float64)
    vd = acc[..., 2].astype(np.float32).astype(np.float64)
    cb_o = _trunc_i32(vd * K_V_CB - ud * K_U_CB).astype(np.int64) + 0x8000
    cr_o = _trunc_i32(ud * K_U_CR - vd * K_V_CR).astype(np.int64) + 0x8000
    out[..., 1] = np.clip(cr_o, 0, 0xFFFF).astype(np.uint16)
    out[..., 2] = np.clip(cb_o, 0, 0xFFFF).astype(np.uint16)
    return out


def apply_lut3d_float(y: np.ndarray, cb: np.ndarray, cr: np.ndarray,
                      ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """The same stage for the float pipeline: normalised y/cb/cr in and out.

    `y` is on [0, 1] and the two chroma channels are centred on zero, which is
    what `rgb_to_ycc` produces and what `ycc_to_rgb` consumes. The conversion to
    the engine's planes and back is the whole of the adaptation:

        Y  = trunc(y * 16383)                 as ycc_exact.py measured it, and
                                              as ygamma_planes stores it
        Cr = round(cr * 16383) + 32768        the same scale, offset to unsigned
        Cb = round(cb * 16383) + 32768

    Truncation on Y because that is the engine's own rounding at this point in
    the chain and it is bit-exact there; round-to-nearest on the chroma because
    the engine's integer planes were produced by stages llr runs in float, so
    the nearest plane is the best reconstruction available — the residual is the
    1 LSB of 16383 ycc_exact.py measures, well inside this stage's own step.

    Y comes back with the engine's lower clamp only; the upper end is left to
    `ycc_to_rgb`, which clips as the engine's YCC2RGB does.
    """
    y_in = np.asarray(y, np.float64)
    planes = np.stack([
        np.clip(np.trunc(y_in * PLANE_SCALE), 0, 0xFFFF),
        np.clip(np.rint(np.asarray(cr, np.float64) * PLANE_SCALE) + PLANE_CENTRE, 0, 0xFFFF),
        np.clip(np.rint(np.asarray(cb, np.float64) * PLANE_SCALE) + PLANE_CENTRE, 0, 0xFFFF),
    ], axis=-1).astype(np.uint16)
    out = apply_lut3d_planes(planes)

    # Hand back the caller's own float width: the render path carries float32,
    # and widening it here would silently double the frame's memory.
    in_dtype = np.asarray(y).dtype
    dtype = in_dtype if np.issubdtype(in_dtype, np.floating) else np.float64
    plane = out.astype(np.float64)
    return (
        (plane[..., 0] / PLANE_SCALE).astype(dtype, copy=False),
        ((plane[..., 2] - PLANE_CENTRE) / PLANE_SCALE).astype(dtype, copy=False),
        ((plane[..., 1] - PLANE_CENTRE) / PLANE_SCALE).astype(dtype, copy=False),
    )
