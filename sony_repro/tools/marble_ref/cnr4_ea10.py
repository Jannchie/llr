r"""Bit-exact numpy re-implementation of ZcTaskSIMDMarble `cnr4_ea10` (Edit.exe 0x14038ea10).

Call in the engine: cnr4(factor=4, ctx, task).  Inputs are the task's current set
(= cnr3 output, replicate-padded by 6 px, 278x166) plus ctx.tmp (original half-res C2).
Outputs are written into ctx planes "b2" (ctx+0x18) and "b1" (ctx+0x20), 532x616 each.

Structure (from the disassembly):

  1. Allocate a temp set [Y(W x H, never written), U1(W/2+12 x H), U2(W/2+12 x H)].
  2. For every low-res row j (0..Hlo-1):  bilinear upsample set[1] -> U1, set[2] -> U2
     with fixed-point weights (sum 128, table `WEIGHTS`), phase-centred, writing rows
     4j+2 .. 4j+5 and columns 2i+1, 2i+2 for low-res column i.  (Rows 0,1 and column 0 of
     U1/U2 are NEVER written -> uninitialised heap memory; see `u1_init`/`u2_init`.)
     Then call 0x14038e4a0(factor, ctx, tmpset, y0=4j) which processes rows 4j..4j+3.
  3. 0x14038e4a0 per pixel:
        b2 = U1
        k  = trunc((min(2^23 - clamp((|U1-32768| - lo1*256)*r1, 0, 2^23),
                        clamp(((U2-32768) - lo2*256)*r2, 0, 2^23)) + 32768) / 65536)   # 0..128
        a  = k * strength                                                   # ctx+0x78 (205)
        b1 = trunc((tmp*a + (32768-a)*U2 + 16384) / 32768), clamp 0..65535
     where r1 = ctx+0x7c = RECIP[hi1-lo1], r2 = ctx+0x80 = RECIP[hi2-lo2] (RECIP[n]=round(32768/n)),
     computed by cnr4 itself and stored back into ctx.  Columns 0..(W2 - W2%8 - 1) use the AVX
     float32 path (products are rounded to float32!), the remaining W2%8 columns use exact
     int32 scalar code.  Both are emulated separately here.

Only ctx fields +0x28 W, +0x2c H, +0x68, +0x6c, +0x70, +0x74, +0x78 are read (+0x7c/+0x80 written).
The original half-res C1 plane is NOT used anywhere; `c1_orig` is accepted for interface
compatibility only.
"""
import struct

import numpy as np

# int32 tables at 0x140559640/660/680/6a0 (factor 4) : index k = 2*row_phase + col_phase
# row phases 0..3 <-> dy = 1/8, 3/8, 5/8, 7/8 ; col phases 0..1 <-> dx = 1/4, 3/4
# weight of A0 (row j, col i), A1 (row j, col i+1), B1 (row j+1, col i+1), B0 (row j+1, col i)
WEIGHTS_F4 = {
    'A0': [84, 28, 60, 20, 36, 12, 12, 4],
    'A1': [28, 84, 20, 60, 12, 36, 4, 12],
    'B1': [4, 12, 12, 36, 20, 60, 28, 84],
    'B0': [12, 4, 36, 12, 60, 20, 84, 28],
}
# factor 8 tables at 0x1405596c0/740/7c0/840 : index k = 4*row_phase + col_phase (8 x 4 phases)
WEIGHTS_F8 = {
    'A0': [105, 75, 45, 15, 91, 65, 39, 13, 77, 55, 33, 11, 63, 45, 27, 9, 49, 35, 21, 7, 35, 25, 15, 5, 21, 15, 9, 3, 7, 5, 3, 1],
    'A1': [15, 45, 75, 105, 13, 39, 65, 91, 11, 33, 55, 77, 9, 27, 45, 63, 7, 21, 35, 49, 5, 15, 25, 35, 3, 9, 15, 21, 1, 3, 5, 7],
    'B1': [1, 3, 5, 7, 3, 9, 15, 21, 5, 15, 25, 35, 7, 21, 35, 49, 9, 27, 45, 63, 11, 33, 55, 77, 13, 39, 65, 91, 15, 45, 75, 105],
    'B0': [7, 5, 3, 1, 21, 15, 9, 3, 35, 25, 15, 5, 49, 35, 21, 7, 63, 45, 27, 9, 77, 55, 33, 11, 91, 65, 39, 13, 105, 75, 45, 15],
}

# uint16 table at 0x140559540 : RECIP[n] = round(32768 / n)  (RECIP[0] = 0, RECIP[1] = 32768)
RECIP = [0, 32768] + [int(np.floor(32768.0 / n + 0.5)) for n in range(2, 256)]

PAD = 6  # replicate padding of the low-res set produced by scratch_a70


def ctx_params_from_bytes(raw):
    """Parse the ctx blob (npz key step14_cnr4_ea10_pre_ctx) into the dict cnr4_ea10 needs."""
    raw = bytes(np.asarray(raw).tobytes())
    i32 = lambda off: struct.unpack_from('<i', raw, off)[0]
    return {
        'W': i32(0x28), 'H': i32(0x2c),
        'lo1': i32(0x68), 'hi1': i32(0x6c),   # ramp on |U1 - 32768| / 256   (full -> zero)
        'lo2': i32(0x70), 'hi2': i32(0x74),   # ramp on (U2 - 32768) / 256   (zero -> full)
        'strength': i32(0x78),                # 205 = 0.8 * 256
    }


def _recip(lo, hi):
    """ctx+0x7c / +0x80 computation: signed lookup of RECIP[|hi-lo|]."""
    d = hi - lo
    return RECIP[d] if d >= 0 else -RECIP[-d]


def upsample_plane(src, H, W2, factor=4, init=None):
    """Bilinear 2x(h) x factor(v) upsample of a padded low-res plane (as the engine does).

    src: uint16 (Hlo+2*PAD, Wlo+2*PAD).  Returns int64 array (H, W2+12) where rows 0..1 and
    column 0 keep `init` (uninitialised memory in the engine; zeros by default).
    """
    src = src.astype(np.int64)
    hs = factor // 2                     # horizontal factor for the 4:2:2 chroma planes
    Wlo = (W2 * 2 + factor - 1) // factor
    Hlo = (H + factor - 1) // factor
    if factor == 4:
        tab, ncol = WEIGHTS_F4, 2
    elif factor == 8:
        tab, ncol = WEIGHTS_F8, 4
    else:
        raise ValueError('factor must be 4 or 8')
    out = np.zeros((H, W2 + 12), np.int64) if init is None else init.astype(np.int64).copy()

    A = src[PAD:PAD + Hlo, PAD:PAD + Wlo]            # row j,   col i
    A1 = src[PAD:PAD + Hlo, PAD + 1:PAD + Wlo + 1]   # row j,   col i+1
    B = src[PAD + 1:PAD + Hlo + 1, PAD:PAD + Wlo]    # row j+1, col i
    B1 = src[PAD + 1:PAD + Hlo + 1, PAD + 1:PAD + Wlo + 1]
    for r in range(factor):
        rows = hs + r + factor * np.arange(Hlo)      # 4j + 2 + r
        ok = rows < H                                # last band only writes rows 4j+2, 4j+3
        for c in range(ncol):
            k = ncol * r + c
            v = (tab['A1'][k] * A1 + tab['A0'][k] * A + tab['B1'][k] * B1 + tab['B0'][k] * B + 64) >> 7
            v = np.minimum(v, 65535)                 # vpackusdw
            cols = factor // 4 + hs * np.arange(Wlo) + c   # 2i + 1 + c
            out[np.ix_(rows[ok], cols)] = v[ok]
    return out


def _blend_rows(U1, U2, tmp, W2, p):
    """0x14038e4a0 for all rows at once (it is row-separable). Returns (b2, b1) uint16."""
    lo1s = p['lo1'] << 8
    lo2s = p['lo2'] << 8
    r1 = _recip(p['lo1'], p['hi1'])
    r2 = _recip(p['lo2'], p['hi2'])
    st = p['strength']
    nvec = W2 - (W2 % 8)   # columns handled by the AVX2 float path (W2 - W2%8 with C semantics)

    u1 = U1[:, :W2].astype(np.int64)
    u2 = U2[:, :W2].astype(np.int64)
    t = tmp[:, :W2].astype(np.int64)

    b2 = np.clip(u1, 0, 65535).astype(np.uint16)
    b1 = np.zeros_like(b2)

    # ---- vector (float32) path, columns 0..nvec-1 ----
    f = np.float32
    a = np.abs(u1[:, :nvec] - 32768).astype(f)
    a = (a - f(lo1s)) * f(r1)                          # vsubps, vmulps (rounded)
    c1 = np.minimum(np.maximum(a, f(0)), f(8388608))
    s1 = f(8388608) - c1
    b = (u2[:, :nvec].astype(f) - f(32768)) - f(lo2s)
    b = b * f(r2)
    c2 = np.minimum(np.maximum(b, f(0)), f(8388608))
    m = np.minimum(s1, c2)
    k = np.trunc((m + f(32768)) / f(65536))
    alpha = k * f(st)
    p1 = t[:, :nvec].astype(f) * alpha
    p2 = (f(32768) - alpha) * u2[:, :nvec].astype(f)
    s = (p1 + p2) + f(16384)
    o = np.trunc(s / f(32768))
    o = np.minimum(np.maximum(o, f(0)), f(65536))
    b1[:, :nvec] = np.minimum(o.astype(np.int64), 65535).astype(np.uint16)

    # ---- scalar (int32) path, columns nvec..W2-1 ----
    if nvec < W2:
        x1 = np.abs(u1[:, nvec:] - 32768) - lo1s
        c1 = np.clip(x1 * r1, 0, 0x800000)
        s1 = 0x800000 - c1
        x2 = u2[:, nvec:] - lo2s - 32768
        c2 = np.clip(x2 * r2, 0, 0x800000)
        m = np.minimum(c2, s1)
        k = (m + 0x8000) >> 16
        alpha = k * st
        tot = ((0x8000 - alpha) * u2[:, nvec:] + t[:, nvec:] * alpha + 0x4000) & 0xFFFFFFFF
        o = np.minimum(tot >> 15, 0xFFFF)
        b1[:, nvec:] = o.astype(np.uint16)
    return b2, b1


def cnr4_ea10(in0, in1, in2, c1_orig, c2_orig, ctx_params, factor=4, u1_init=None, u2_init=None):
    """Re-implementation of 0x14038ea10.

    in0/in1/in2 : the task's current set (cnr3 output, padded 278x166); in0 unused.
    c1_orig     : unused by the engine (kept for the requested signature).
    c2_orig     : ctx.tmp, original half-res C2 (616x532).
    ctx_params  : dict from ctx_params_from_bytes (keys W,H,lo1,hi1,lo2,hi2,strength).
    u1_init/u2_init : optional (H, W/2+12) arrays giving the content of the engine's
                  uninitialised temp planes (rows 0..1 and column 0 are never written).
    Returns (b2, b1) uint16 (H, W/2).
    """
    p = dict(ctx_params)
    W, H = p['W'], p['H']
    W2 = W // 2
    U1 = upsample_plane(np.asarray(in1), H, W2, factor, u1_init)
    U2 = upsample_plane(np.asarray(in2), H, W2, factor, u2_init)
    b2, b1 = _blend_rows(U1, U2, np.asarray(c2_orig), W2, p)
    # side effects on ctx (the function stores these back)
    p['r1_0x7c'] = _recip(p['lo1'], p['hi1'])
    p['r2_0x80'] = _recip(p['lo2'], p['hi2'])
    ctx_params.update(p)
    return b2, b1
