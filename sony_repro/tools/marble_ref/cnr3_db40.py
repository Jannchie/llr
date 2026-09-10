"""Bit-exact numpy re-implementation of Edit.exe 0x14038db40 ("cnr3_db40").

Third stage of the ZcTaskSIMDMarble chroma-NR branch.  Input: the 3-plane set
produced by cnr2_c480 (padded low-res YCC, 166x278 for a 1064x616 tile with
factor 4).  Output: a NEW 3-plane set of the same size where

    plane1 / plane2 (chroma) = 3x3 Gaussian ([1 2 1;2 4 2;1 2 1]/16) blended
                               with the centre pixel:
        S    = sum_{dy,dx} k[dy][dx] * in[y+dy][x+dx]            (weights sum 16)
        out  = (16*c*p + S*(256-p) + 2048) >> 12                (uint32/uint64 math)
        with c = in[y][x],  p = ctx+0x54 for plane1, ctx+0x58 for plane2
    plane0 (luma)            = NEVER written (uninitialised heap memory).

Only the core region y in [6, H6), x in [6, W6) of the (H6+6) x (W6+6) planes, with
    W6 = (W-1+factor)//factor + 6,  H6 = (H-1+factor)//factor + 6
is written; the 6-pixel border of the output planes is left as allocated
(observed as zero in the capture).

Quirk faithfully reproduced: the AVX2 path uses (256 - p54) as the blur
weight for BOTH planes, while the centre weight is p54 / p58 respectively.
The scalar tail (columns from 3*(W6//3) on) uses (256 - p58) for plane2.
Both agree when p54 == p58 (128 in all captures so far).

ctx fields read: +0x28 W, +0x2c H, +0x54 p1, +0x58 p2.  Nothing is written
back to ctx.
"""
import numpy as np

KERNEL = np.array([[1, 2, 1], [2, 4, 2], [1, 2, 1]], dtype=np.uint64)


def _blend_plane(src, p_centre, p_blur_vec, p_blur_scalar, x0, x1, y0, y1, x_scalar_from):
    """src: uint16 (H, W).  Returns uint16 (H, W) with the core region filled."""
    s = src.astype(np.uint64)
    out = np.zeros_like(src)
    # 3x3 weighted sum on the core (1-px halo always available: core starts at 6)
    S = np.zeros((y1 - y0, x1 - x0), dtype=np.uint64)
    for dy in range(3):
        for dx in range(3):
            S += KERNEL[dy, dx] * s[y0 - 1 + dy:y1 - 1 + dy, x0 - 1 + dx:x1 - 1 + dx]
    c = s[y0:y1, x0:x1]
    # vector path columns [x0, x_scalar_from) use p_blur_vec; scalar tail uses p_blur_scalar
    pb = np.full((1, x1 - x0), np.uint64(p_blur_scalar), dtype=np.uint64)
    pb[:, :max(0, x_scalar_from - x0)] = np.uint64(p_blur_vec)
    v = (np.uint64(16) * c * np.uint64(p_centre) + S * pb + np.uint64(2048)) >> np.uint64(12)
    out[y0:y1, x0:x1] = (v & np.uint64(0xFFFF)).astype(np.uint16)
    return out


def cnr3_db40(y_in, c1_in, c2_in, ctx_params, factor=4):
    """y_in, c1_in, c2_in: uint16 (H6, W6) planes of the cnr2 output set.
    ctx_params: dict with keys 'W', 'H' (tile size, ctx+0x28/+0x2c) and
                'p54', 'p58' (ctx+0x54 / +0x58; 128 in captures).
    Returns (out0, out1, out2) uint16 (H6, W6).  out0 is undefined in the
    real engine (unwritten heap); we return zeros."""
    W = int(ctx_params.get('W', 1064))
    H = int(ctx_params.get('H', 616))
    p54 = int(ctx_params.get('p54', 128))
    p58 = int(ctx_params.get('p58', 128))
    W6 = (W - 1 + factor) // factor + 6      # loop end (exclusive)
    H6 = (H - 1 + factor) // factor + 6
    assert c1_in.shape == (H6 + 6, W6 + 6), (c1_in.shape, (H6 + 6, W6 + 6))  # planes are +12
    # AVX2 loop: x = 6, 9, ..., while x < xv = (W6/3)*3 (signed C division);
    # each step writes 4 lanes x..x+3 and advances 3.  The scalar tail then
    # runs x = xv .. W6-1 and overwrites column xv, so the vector blur weight
    # survives only on [6, xv).
    xv = (W6 // 3) * 3
    x_scalar_from = xv if xv > 6 else 6
    out1 = _blend_plane(c1_in, p54, 256 - p54, 256 - p54, 6, W6, 6, H6, x_scalar_from)
    out2 = _blend_plane(c2_in, p58, 256 - p54, 256 - p58, 6, W6, 6, H6, x_scalar_from)
    out0 = np.zeros_like(y_in)
    return out0, out1, out2
