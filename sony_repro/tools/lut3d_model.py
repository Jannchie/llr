r"""Bit-exact numpy model of ZcTask3DLut (Edit.exe RVA 0x36f340) -- the "advanced
colour reproduction" 3-D LUT stage, between YGamma and SIMDSpica.

Runs on the three YCC planes in place:
    plane 0 = Y   (uint16, 0..16383)
    plane 1 = Cr  (uint16, centred on 32768)
    plane 2 = Cb  (uint16, centred on 32768)

Descriptor (desc = [[[task+0x68]+0xc8]+0x49920]) as captured:
    [0x08] ptr   -- unrelated block
    [0x10] ptr   -- second cell grid, NOT read by this exec
    [0x18] ptr   -- the 3-D cell grid  (33*33*33 cells * 3 outputs * 8 corners * int16)
    [0x20] 33    -- axis size, used as stride for the v axis
    [0x28] 33    -- axis size; [0x20]*[0x28] = 1089 = stride for the u axis
    [0x2c] 5     -- shift = 14 - 5 = 9   (Y  -> cell index)
    [0x30] 5     -- shift = 16 - 5 = 11  (C' -> cell index)
    [0x34] 511   -- Y fraction mask   (9 bits)
    [0x38] 2047  -- C fraction mask   (11 bits)
    [0x48] ptr   -- the 1-D chroma warp curve; the code uses ptr + 0x10000, i.e. the
                    curve is int16[65537] indexed by a SIGNED value in [-32768, +32767],
                    so the bytes BEFORE the 0x10000 offset are the negative half and
                    they ARE used.

Floating-point constants: all rip-relative, read out of Edit.exe (image base
0x140000000).  RVA -> value:
    0x4DEB38  3.7e-05     u = Cb'*this + Cr'*1.401988
    0x4DEDC8  1.401988
    0x4DEB68  0.000135    v = Cr'*this + Cb'*1.771978
    0x4DEE10  1.771978
    0x4DF168  32768.0     chroma centre
    0x467768  -32768.0f   lower bound (comiss)
    0x4667A0   32767.0f   upper bound (comiss)
    0x4DF348  -32768.0    lower clamp
    0x4DF160   32767.0    upper clamp
    0x4DED10  0.564341    Cb_out = v_out*this - u_out*5.4e-05     (1/1.771978)
    0x4DEB40  5.4e-05
    0x4DED48  0.713273    Cr_out = u_out*this - v_out*1.5e-05     (1/1.401988)
    0x4DEB10  1.5e-05
So the LUT axes are the BT.601-ish colour differences u ~ (R-Y), v ~ (B-Y), and the
same 2x2 matrix is inverted on the way out.

Usage:
    uv run python sony_repro/tools/lut3d_model.py [npz ...]
"""
from __future__ import annotations

import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
#: compact, portable copy of the two tables (they are static -- see the note)
TABLE_NPZ = os.path.join(HERE, "lut3d_table.npz")

# ---- rip-relative constants, read from Edit.exe (see RVAs above) -------------
C_CENTRE = 32768.0
M_CB_U, M_CR_U = 3.7e-05, 1.401988      # xmm10, xmm11
M_CR_V, M_CB_V = 0.000135, 1.771978     # xmm12, xmm13
LO_F, HI_F = np.float32(-32768.0), np.float32(32767.0)
LO_D, HI_D = -32768.0, 32767.0
K_V_CB, K_U_CB = 0.564341, 5.4e-05      # Cb_out = v*K_V_CB - u*K_U_CB
K_U_CR, K_V_CR = 0.713273, 1.5e-05      # Cr_out = u*K_U_CR - v*K_V_CR


class Params:
    """The descriptor fields the exec actually reads."""

    def __init__(self, sy=33, sx=33, sh_y=9, sh_c=11, mask_y=511, mask_c=2047):
        self.stride_v = sy                 # [desc+0x20]
        self.stride_u = sy * sx            # [desc+0x20] * [desc+0x28]
        self.sh_y = sh_y                   # 14 - [desc+0x2c]
        self.sh_c = sh_c                   # 16 - [desc+0x30]
        self.mask_y = mask_y               # [desc+0x34]
        self.mask_c = mask_c               # [desc+0x38]

    @classmethod
    def from_desc_i32(cls, i32):
        return cls(sy=i32[0x20 // 4], sx=i32[0x28 // 4],
                   sh_y=14 - i32[0x2C // 4], sh_c=16 - i32[0x30 // 4],
                   mask_y=i32[0x34 // 4], mask_c=i32[0x38 // 4])


#: corner slot k inside a cell, as (f1, f0, yf) offsets -- gray-coded, not binary
GRAY = ((0, 0, 0), (0, 0, 1), (0, 1, 1), (0, 1, 0),
        (1, 0, 0), (1, 0, 1), (1, 1, 1), (1, 1, 0))


class Table:
    """curve: int16[65537] signed-indexed 1-D warp; grid: int16[cells, 3, 8].

    The engine's cell store is a redundant expansion of a single 33x33x33x3 point
    grid (verified exactly): cell (iu, iv, iy) slot k holds the point at
    (iu + f0, iv + f1, iy + yf).  ``points`` is that compact form.
    """

    def __init__(self, curve: np.ndarray, grid: np.ndarray):
        self.curve = np.ascontiguousarray(curve, np.int16)
        self.grid = np.ascontiguousarray(grid, np.int16)

    @classmethod
    def from_npz(cls, z):
        """From a full frida capture (needs data48 + grid18)."""
        return cls(z["data48"].view("<i2")[:65537], z["grid18"].view("<i2").reshape(-1, 3, 8))

    @classmethod
    def from_points(cls, curve, points, n=33):
        """From the compact 33^3x3 point grid; rebuilds the engine's cell store."""
        points = np.asarray(points, np.int16).reshape(n, n, n, 3)
        grid = np.zeros((n, n, n, 3, 8), np.int16)
        i = np.arange(n - 1)
        for k, (b1, b0, by) in enumerate(GRAY):
            grid[:n - 1, :n - 1, :n - 1, :, k] = points[i[:, None, None] + b0,
                                                        i[None, :, None] + b1,
                                                        i[None, None, :] + by]
        return cls(curve, grid.reshape(-1, 3, 8))

    @classmethod
    def load(cls, path):
        z = np.load(path)
        return cls.from_points(z["curve"], z["points"])

    def points(self, n=33):
        g = self.grid.reshape(n, n, n, 3, 8)
        p = np.zeros((n, n, n, 3), np.int16)
        i = np.arange(n - 1)
        for k, (b1, b0, by) in enumerate(GRAY):
            p[i[:, None, None] + b0, i[None, :, None] + b1,
              i[None, None, :] + by] = g[:n - 1, :n - 1, :n - 1, :, k]
        return p

    def save(self, path):
        np.savez_compressed(path, curve=self.curve, points=self.points())


def _f32(x):
    return np.asarray(x, np.float64).astype(np.float32)


def _trunc_i32(x):
    """cvttss2si / cvttsd2si: round toward zero."""
    return np.trunc(x).astype(np.int64).astype(np.int32)


def apply_3dlut(planes_in: np.ndarray, table: Table, params: Params) -> np.ndarray:
    """planes_in uint16 (h, w, 3) = (Y, Cr, Cb) -> planes_out uint16 (h, w, 3)."""
    p = params
    y_u = planes_in[..., 0].astype(np.int64)          # movzx -> unsigned
    cr = planes_in[..., 1].astype(np.float64) - C_CENTRE
    cb = planes_in[..., 2].astype(np.float64) - C_CENTRE

    # --- 2x2 forward matrix, computed in double, narrowed to float, clamped ----
    u = _f32(cb * M_CB_U + cr * M_CR_U)               # cvtpd2ps
    v = _f32(cr * M_CR_V + cb * M_CB_V)
    iu = np.where(LO_F > u, -32768, np.where(u > HI_F, 32767, _trunc_i32(u)))  # noqa: SIM300
    iv = np.where(LO_F > v, -32768, np.where(v > HI_F, 32767, _trunc_i32(v)))  # noqa: SIM300

    # --- 1-D warp curve, signed index (this is what the 0x10000 offset is for) --
    a = table.curve[(iu + 0x8000).astype(np.int64)].astype(np.int32) + np.int32(0x8000)
    b = table.curve[(iv + 0x8000).astype(np.int64)].astype(np.int32) + np.int32(0x8000)

    f0 = (a & p.mask_c).astype(np.int32)              # 11-bit fraction on the u axis
    f1 = (b & p.mask_c).astype(np.int32)              # 11-bit fraction on the v axis
    yf = (y_u & p.mask_y).astype(np.int32)            # 9-bit fraction on the Y axis
    F0 = np.int32(p.mask_c + 1) - f0
    F1 = np.int32(p.mask_c + 1) - f1
    yF = np.int32(p.mask_y + 1) - yf

    cell = ((a >> p.sh_c).astype(np.int64) * p.stride_u
            + (b >> p.sh_c).astype(np.int64) * p.stride_v
            + (y_u >> p.sh_y))

    # --- trilinear weights: ((x*y >> 3) * z + 0x40000) >> 19, they sum to 512 ---
    R = np.int32(0x40000)
    p_f0_yf = (f0 * yf) >> 3
    p_f0_yF = (f0 * yF) >> 3
    p_F0_yf = (F0 * yf) >> 3
    p_F0_yF = (F0 * yF) >> 3
    w2 = (p_f0_yf * F1 + R) >> 19
    w6 = (p_f0_yf * f1 + R) >> 19
    w3 = (p_f0_yF * F1 + R) >> 19
    w7 = (p_f0_yF * f1 + R) >> 19
    w1 = (p_F0_yf * F1 + R) >> 19
    w5 = (p_F0_yf * f1 + R) >> 19
    w4 = (p_F0_yF * f1 + R) >> 19
    w0 = np.int32(512) - w7 - w6 - w5 - w4 - w3 - w2 - w1
    # corner order inside a cell is gray-coded on (f1, f0, yf):
    #   k: 0=000 1=001 2=011 3=010 4=100 5=101 6=111 7=110
    w = np.stack([w0, w1, w2, w3, w4, w5, w6, w7], axis=-1)

    corners = table.grid[cell]                        # (h, w, 3, 8) int16
    acc = np.einsum("...ck,...k->...c", corners.astype(np.int32), w, dtype=np.int32)
    acc >>= 9

    out = np.empty(planes_in.shape, np.uint16)
    # Y: negative -> 0, otherwise the low 16 bits are stored (no upper clamp).
    out[..., 0] = np.where(acc[..., 0] < 0, 0, acc[..., 0] & 0xFFFF).astype(np.uint16)

    # chroma: int32 -> float32 -> double, inverse 2x2, +0x8000, clamp [0, 65535]
    ud = acc[..., 1].astype(np.float32).astype(np.float64)
    vd = acc[..., 2].astype(np.float32).astype(np.float64)
    cb_o = _trunc_i32(vd * K_V_CB - ud * K_U_CB).astype(np.int64) + 0x8000
    cr_o = _trunc_i32(ud * K_U_CR - vd * K_V_CR).astype(np.int64) + 0x8000
    out[..., 1] = np.clip(cr_o, 0, 0xFFFF).astype(np.uint16)
    out[..., 2] = np.clip(cb_o, 0, 0xFFFF).astype(np.uint16)
    return out


# --------------------------------------------------------------------------- #
def check(path, inset=3, table=None):
    import json
    z = np.load(path, allow_pickle=True)
    info = json.loads(str(z["desc"]))
    params = Params.from_desc_i32(info["i32"])
    if table is None:
        table = Table.from_npz(z) if "grid18" in z.files else Table.load(TABLE_NPZ)
    print(f"== {os.path.basename(path)}")
    print(f"   grid {table.grid.shape} curve {table.curve.shape} "
          f"strides u={params.stride_u} v={params.stride_v} "
          f"shifts {params.sh_y}/{params.sh_c} masks {params.mask_y}/{params.mask_c}")
    ok = True
    for i in range(8):
        if f"t{i}_in" not in z.files:
            continue
        a, b, m = z[f"t{i}_in"], z[f"t{i}_out"], z[f"t{i}_meta"]
        x0, y0, x1, y1 = (int(v) for v in m[0x30 // 4:0x40 // 4])
        x0, y0 = max(x0, 0) + inset, max(y0, 0) + inset
        x1, y1 = min(x1, a.shape[1]) - inset, min(y1, a.shape[0]) - inset
        got = apply_3dlut(a, table, params)
        sl = (slice(y0, y1), slice(x0, x1))
        line = [f"   tile{i} {a.shape[1]}x{a.shape[0]} rect[{x0}:{x1},{y0}:{y1}]"]
        for k, nm in enumerate(("Y", "Cr", "Cb")):
            g, e = got[sl][..., k].astype(np.int32), b[sl][..., k].astype(np.int32)
            eq = (g == e)
            pct = 100.0 * eq.mean()
            d = np.abs(g - e)
            line.append(f"{nm} {pct:.4f}% maxdiff={d.max()}")
            ok &= bool(eq.all())
        print("  ".join(line))
    print("   ALL BIT-EXACT" if ok else "   *** MISMATCH ***")
    return ok


if __name__ == "__main__":
    args = sys.argv[1:]
    if args and args[0] == "--export-table":
        z = np.load(args[1], allow_pickle=True)
        Table.from_npz(z).save(TABLE_NPZ)
        print("wrote", TABLE_NPZ, os.path.getsize(TABLE_NPZ), "bytes")
        raise SystemExit(0)
    args = args or [os.path.join(HERE, "lut3d_export_grid1.npz"),
                    os.path.join(HERE, "lut3d_export_cs_DSC02919-lut3d.npz")]
    rc = 0
    for a in args:
        rc |= 0 if check(a) else 1
    raise SystemExit(rc)
