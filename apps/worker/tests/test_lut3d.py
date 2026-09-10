"""ZcTask3DLut, Edit's "advanced colour reproduction" (sony/lut3d.py).

The stage was decoded and verified against Imaging Edge's own buffers before
this port existed: nineteen tiles across two bodies, two frames and eleven
Creative Looks, three planes each, whole tile including the borders, all
100.0000% bit-exact (sony_repro/notes/static-3dlut.md). These tests pin the port
to that — the integer form against a crop of one of those tiles, the generated
warp curve against the engine's dumped one, and the float path the shader
mirrors against the sweep the note reports.
"""

from pathlib import Path

import numpy as np
import pytest

from llr_worker.sony.chroma import apply_chroma
from llr_worker.sony.lut3d import (
    CURVE_CENTRE,
    GRID_LAST,
    GRID_N,
    apply_lut3d_planes,
    cell_grid,
    lut3d_points,
    warp_curve,
)

FIXTURE = Path(__file__).parent / "fixtures" / "lut3d_tile.npz"

# The engine's own interpolated values for DSC03015 (VV2) — the same eight the
# ChromaSuppres tests and the shader's vitest mirror use.
CROSS = np.array([-0.261719, -0.222656, -0.230469, -0.167969], np.float32)
GAIN = np.array([1.078125, 0.632812, 0.929688, 1.09375], np.float32)
BT601 = np.array([0.299, 0.587, 0.114], np.float32)


def _chroma_of(rgb: np.ndarray) -> tuple[float, float]:
    """(luma, chroma magnitude) of one display-encoded pixel, via BT.601.

    YCC2RGB is plain BT.601, so the two chroma planes can be read straight back
    out of the stage's RGB output — which is what lets these tests talk about
    what the stage did rather than about the colour it landed on.
    """
    y = float(rgb @ BT601)
    return y, float(np.hypot((rgb[0] - y) / 1.4020, (rgb[2] - y) / 1.7720))


def test_the_integer_stage_reproduces_the_engine_s_own_tile_bit_for_bit() -> None:
    """The claim the module rests on, checked on the engine's own pixels.

    The fixture is the 128x128 crop of tile 3 of lut3d_export_grid1.npz with the
    largest luma change (mean |dY| 54 of 16383, against 50 over the whole tile),
    taken from inside the tile's valid rect. It touches 83 of the grid's cells,
    so this is not one cell's arithmetic passing for the whole table. Nothing
    here is approximate: all three planes have to match exactly, because the
    engine's own output is what "correct" means.
    """
    data = np.load(FIXTURE)
    got = apply_lut3d_planes(data["tile_in"])
    want = data["tile_out"]
    assert got.dtype == want.dtype
    for plane, name in enumerate(("Y", "Cr", "Cb")):
        assert np.array_equal(got[..., plane], want[..., plane]), name

    # The crop was chosen for this: a stage that did nothing would also pass a
    # bit-exactness test on a tile it happened not to move.
    d = want[..., 0].astype(np.int64) - data["tile_in"][..., 0].astype(np.int64)
    assert d.max() < 0                       # every pixel here comes down
    assert int(np.abs(d).mean()) == 54


def test_the_warp_curve_is_generated_and_still_equals_the_dumped_table() -> None:
    """The one table that is a formula, so it ships as one.

    The engine holds int16[65537] here; `curve[i] = sign(i) * min(trunc(32768 *
    (|i|/32768)^(2/3)), 32767)` reproduces every one of those entries, which is
    why only the point grid is stored as data. The fixture carries the dump so
    this stays a comparison against the engine rather than against itself.

    The saturation at the very top is the engine's own: the formula overflows
    int16 at |i| = 32768 and the dumped table holds 32767 there.
    """
    curve = warp_curve()
    assert curve.shape == (65537,)
    assert curve.dtype == np.int16
    assert not curve.flags.writeable      # shared and cached; nobody may edit it
    assert np.array_equal(curve, np.load(FIXTURE)["curve"])

    # Odd, monotone, and extremely fine near neutral — which is what makes a
    # 32-step chroma axis enough resolution for a colour stage.
    assert curve[CURVE_CENTRE] == 0
    assert list(curve[CURVE_CENTRE:CURVE_CENTRE + 4]) == [0, 32, 50, 66]
    assert np.array_equal(curve[CURVE_CENTRE + 1:-1], -curve[CURVE_CENTRE - 1:0:-1])
    assert (np.diff(curve.astype(np.int32)) >= 0).all()
    assert curve[-1] == 32767


def test_the_cell_store_is_the_point_grid_expanded_by_the_gray_code() -> None:
    """1.7 MB of engine table, 122 KB of data — the rest is redundancy.

    Cell (iu, iv, iy) slot k holds the point at (iu+f0, iv+f1, iy+yf) with
    (f1, f0, yf) gray-coded on k, which is how the engine gets all eight corners
    of a cell out of one cache line. Reading the expansion back has to give the
    points exactly, or the corners are being fetched in the wrong order — an
    error that would show up as a subtle hue shift, not as a crash.
    """
    points = lut3d_points()
    assert points.shape == (GRID_N, GRID_N, GRID_N, 3)
    assert points.dtype == np.int16
    assert not points.flags.writeable

    grid = cell_grid().reshape(GRID_N, GRID_N, GRID_N, 3, 8)
    back = np.zeros_like(points)
    i = np.arange(GRID_LAST)
    for k, (b1, b0, by) in enumerate(
            ((0, 0, 0), (0, 0, 1), (0, 1, 1), (0, 1, 0),
             (1, 0, 0), (1, 0, 1), (1, 1, 1), (1, 1, 0))):
        back[i[:, None, None] + b0, i[None, :, None] + b1, i[None, None, :] + by] = \
            grid[:GRID_LAST, :GRID_LAST, :GRID_LAST, :, k]
    assert np.array_equal(back, points)

    # The coordinates are 5-bit, so the 32nd layer of each axis is only ever a
    # cell's far corner — the dump has it zero and never reads it as a cell.
    assert (grid[GRID_LAST] == 0).all()
    assert (grid[:, GRID_LAST] == 0).all()
    assert (grid[:, :, GRID_LAST] == 0).all()


def test_the_sweep_the_note_reports_comes_back_out_of_the_port() -> None:
    """What the stage does, in the engine's own units (static-3dlut.md 7).

    Three things this table says, and all three are the point of the stage:
    neutral picks up a constant +31 chroma bias and loses a little luma; a
    saturated mid-tone is left alone or pushed; and a saturated *highlight* is
    pulled hard — 12288 down to 10198 — with its chroma collapsing back to
    neutral as Y approaches full scale. That last row is what makes Edit's
    output look like the camera's JPEG.
    """
    cases = {
        (512, 32768, 32768): (448, 32799, 32799),
        (8192, 32768, 32768): (8158, 32799, 32799),
        (16383, 32768, 32768): (16316, 32799, 32799),
        (8192, 40000, 26000): (8574, 38286, 27621),
        (12288, 40000, 26000): (10198, 37373, 28463),
        # At the top of the Y range the chroma is thrown away outright: 40000
        # (a Cr of +7232) comes back as 32801, thirty-three from neutral.
        (16383, 40000, 26000): (16250, 32801, 32795),
    }
    planes = np.array([[list(k) for k in cases]], np.uint16)
    got = apply_lut3d_planes(planes)[0]
    for row, want in zip(got, cases.values(), strict=True):
        assert tuple(int(v) for v in row) == want


def test_a_neutral_pixel_is_not_left_alone_and_that_is_the_engine() -> None:
    """The stage moves grey, by exactly the +31 the note measured.

    Worth its own test because the obvious expectation — a colour stage leaves
    neutrals neutral — is wrong here, and a port "fixed" to satisfy it would no
    longer be the engine. The bias is a constant 31 of 16383 on both chroma
    planes, which lands as under 0.006 of separation between the channels.
    """
    grey = np.array([[[0.5, 0.5, 0.5]]], np.float32)
    off = apply_chroma(grey, CROSS, GAIN)[0, 0]
    on = apply_chroma(grey, CROSS, GAIN, lut3d=True)[0, 0]
    assert off[0] == pytest.approx(off[1]) == pytest.approx(off[2])

    y_off, sat_off = _chroma_of(off)
    y_on, sat_on = _chroma_of(on)
    assert sat_off == pytest.approx(0.0, abs=1e-6)
    # 31/16383 on both planes; the magnitude of the pair is sqrt(2) times that.
    assert sat_on == pytest.approx(np.hypot(31, 31) / 16383, abs=2e-4)
    # And Y comes down by well under a percent, as the 8192 -> 8158 row says.
    assert -0.01 < y_on - y_off < 0.0


def test_a_bright_saturated_pixel_comes_down_in_both_luma_and_chroma() -> None:
    """The stage's whole reason for existing, through the float path.

    A display-linear (0.9, 0.5, 0.35) stays inside [0, 1] after the round trip
    — so no clamp can stand in for the effect — and lands at luma 0.60 with a
    chroma magnitude of 0.35, well into the region the note's sweep says is
    pushed. Both numbers have to fall, and neither by a rounding-sized amount.
    """
    rgb = np.array([[[0.9, 0.5, 0.35]]], np.float32)
    off = apply_chroma(rgb, CROSS, GAIN)[0, 0]
    on = apply_chroma(rgb, CROSS, GAIN, lut3d=True)[0, 0]
    assert off.min() > 0.0
    assert off.max() < 1.0

    y_off, sat_off = _chroma_of(off)
    y_on, sat_on = _chroma_of(on)
    assert y_on < y_off - 0.005
    assert sat_on < sat_off * 0.99
    # Off by default: the switch is Edit's own 色彩复制 radio, whose default is
    # 标准 — this stage absent.
    assert np.array_equal(apply_chroma(rgb, CROSS, GAIN, lut3d=False)[0, 0], off)


def test_the_float_path_agrees_with_the_integer_stage_it_stands_in_for() -> None:
    """The adaptation, checked where it can be: on the planes themselves.

    The float pipeline has no 14-bit planes, so `apply_chroma(lut3d=True)`
    reconstructs them (Y truncated onto 0..16383, chroma rounded onto the same
    scale and offset by 0x8000) and converts back. Feeding the reconstruction
    the values it would produce for a known pixel has to give the engine's own
    answer for that pixel, or the scale is wrong somewhere.
    """
    from llr_worker.sony.lut3d import apply_lut3d_float

    y = np.array([[8192 / 16383]])
    cr = np.array([[(40000 - 32768) / 16383]])
    cb = np.array([[(26000 - 32768) / 16383]])
    y_o, cb_o, cr_o = apply_lut3d_float(y, cb, cr)
    assert round(float(y_o[0, 0]) * 16383) == 8574
    assert round(float(cr_o[0, 0]) * 16383 + 32768) == 38286
    assert round(float(cb_o[0, 0]) * 16383 + 32768) == 27621
