"""Bit-exact numpy re-implementation of Edit.exe 0x14038c480
(ZcTaskSIMDMarble chroma-NR pass 2, "cnr2_c480").

Reverse engineered from tools/marble_disasm/fn_14038c480.txt.

Signature in the binary:
    int cnr2_c480(int factor /*ecx*/, Ctx *ctx /*rdx*/, ZcTask *task /*r8*/)

Geometry (factor = 4, ctx+0x28 W = 1064, ctx+0x2c H = 616):
    ws = (W - 1 + factor) / factor = 266        (line 27-32)
    hs = (H - 1 + factor) / factor = 154        (line 68-72)
    new set = alloc(3 planes, ws + 12 = 278, hs + 12 = 166)   (line 73-79)
    loop  y in [4, hs + 8)  = [4, 162)          (rbp+0x6c, line 132-133)
    loop  x in [4, ws + 8)  = [4, 274)          (rbp+0x30, line 134-135)
i.e. it processes the 266x154 core PLUS two extra rows/cols of the 6-px
replicate padding on every side; the outermost 4 rows / 4 cols of the new
set are never written (they hold whatever the allocator left behind -- in
the capture that is stale data from a previously freed set).

Planes read (all from the current set, task+8):   Y = plane0, C1 = plane1,
C2 = plane2.  Planes written: plane1 and plane2 of the NEW set only.  Plane0
of the new set is fetched (line 87-89) but never written.  ctx is read-only.

Per-pixel thresholds (int32, vpmulld / +128 / vpsrad 8 = floor((v*k+128)/256)):
    t0   = clamp(((Y  * p34 + 128) >> 8) + p38, 0, 65535)          ; p34=-4, p38=982
    a1   = clamp((((C1 - 32768) * p3c + 128) >> 8) + p40, 0, 65535) ; p3c=-1, p40=158
    thr1 = clamp((t0 * a1 + 128) >> 8, 0, 65535)
    thr1 = clamp((thr1 * p4c + 128) >> 8, 256, 32768)               ; p4c=652
    a2   = clamp((((C2 - 32768) * p44 + 128) >> 8) + p48, 0, 65535) ; p44=-1, p48=187
    thr2 = clamp((t0 * a2 + 128) >> 8, 0, 65535)
    thr2 = clamp((thr2 * p50 + 128) >> 8, 256, 32768)               ; p50=652
    thrY = (float)p30                                               ; p30=8192
(vector body line 213-266; the scalar tail at line 1000-1090 is identical
except that the intermediate clamp low bound is 1 instead of 0, which cannot
change the final result because of the subsequent max(.,256)).

Neighbourhood: 5x5 samples on a stride-2 grid, dy,dx in {-4,-2,0,2,4}
(row loop: r15=5 iterations, ebx += 2 starting at y-4, line 300-352; the
column taps come from vpshufb picking words x-4,x-2,x,x+2 of an 8-word load
plus a separately loaded word at x+4 inserted into lane 4; lanes 5..7 are
zero and are excluded by the "!= 0" tests).  All compares in float32:

    keep = |Yc - Yn| <= thrY  and  |C1c - C1n| <= thr1  and  |C2c - C2n| <= thr2
           and Yn != 0 and C1n != 0 and C2n != 0
    sum1 = sum(C1n * keep), sum2 = sum(C2n * keep), n = sum(keep)

Division by reciprocal table at 0x1405594d0 (26 dwords, read from Edit.exe):
    tab[0] = 32768, tab[k] = round(32768 / k) for k = 1..25
    k = n if 1 <= n <= 25 else 0                      (line 397-403)
    out1 = min((uint32)(sum1 * tab[k] + 0x4000) >> 15, 65535)
    out2 = min((uint32)(sum2 * tab[k] + 0x4000) >> 15, 65535)
Float sums are integers < 2^24 so vcvtps2dq is exact.

Finally the new set is attached with 0x14016b640(task, newset, 0) (line 1240).
"""

import numpy as np

# 0x1405594d0 .. +0x68 in Edit.exe (.data, file offset 0x557ed0)
RECIP_TABLE = np.array(
    [32768, 32768, 16384, 10923, 8192, 6554, 5461, 4681, 4096, 3641, 3277,
     2979, 2731, 2521, 2341, 2185, 2048, 1928, 1820, 1725, 1638, 1560, 1489,
     1425, 1365, 1311], dtype=np.uint32)

DEFAULT_CTX = dict(W=1064, H=616, p30=8192, p34=-4, p38=982, p3c=-1, p40=158,
                   p44=-1, p48=187, p4c=652, p50=652)


def ctx_params_from_bytes(raw):
    """Build the ctx_params dict from the 160-byte ctx blob (npz *_ctx key)."""
    v = np.frombuffer(np.asarray(raw, dtype=np.uint8).tobytes(), dtype='<i4')
    f = lambda off: int(v[off // 4])
    return dict(W=f(0x28), H=f(0x2c), p30=f(0x30), p34=f(0x34), p38=f(0x38),
                p3c=f(0x3c), p40=f(0x40), p44=f(0x44), p48=f(0x48),
                p4c=f(0x4c), p50=f(0x50))


def _mul_rs8(a, k):
    """vpmulld a,k ; vpaddd 128 ; vpsrad 8  (int32 wrap, arithmetic shift)."""
    a = a.astype(np.int32)
    with np.errstate(over='ignore'):
        p = a * np.int32(k) + np.int32(128)
    return p >> 8


def _mul_rs8_v(a, b):
    """Same as _mul_rs8 but with a per-pixel multiplier (vpmulld a,b)."""
    with np.errstate(over='ignore'):
        p = a.astype(np.int32) * b.astype(np.int32) + np.int32(128)
    return p >> 8


def cnr2_c480(y_pad, c1_pad, c2_pad, ctx_params=None, factor=4, pad_fill=0):
    """Reproduce 0x14038c480 bit-exactly.

    Parameters
    ----------
    y_pad, c1_pad, c2_pad : uint16 (hs+12, ws+12) planes of the current set
                            (the replicate-padded output of scratch_a70).
    ctx_params : dict with W, H (ctx+0x28/+0x2c) and p30..p50 (ctx+0x30..+0x50).
                 See DEFAULT_CTX / ctx_params_from_bytes.
    factor     : ecx argument (4).
    pad_fill   : value used for the never-written outer 4 rows / 4 cols and
                 for the never-written Y plane (the binary leaves stale heap
                 contents there; 0 by default).

    Returns
    -------
    (y_out, c1_out, c2_out) uint16, same shape as the inputs.
    y_out is NOT computed by the binary (plane0 of the new set is never
    written); it is returned filled with pad_fill.
    """
    p = dict(DEFAULT_CTX)
    if ctx_params:
        p.update(ctx_params)
    Y = np.asarray(y_pad)
    C1 = np.asarray(c1_pad)
    C2 = np.asarray(c2_pad)
    assert Y.dtype == np.uint16 and C1.dtype == np.uint16 and C2.dtype == np.uint16
    assert Y.shape == C1.shape == C2.shape

    ws = int((p['W'] - 1 + factor) / factor)   # idiv truncates; operands > 0
    hs = int((p['H'] - 1 + factor) / factor)
    hh, ww = hs + 12, ws + 12
    assert Y.shape == (hh, ww), (Y.shape, (hh, ww))

    y0, y1 = 4, hs + 8
    x0, x1 = 4, ws + 8
    ys = slice(y0, y1)
    xs = slice(x0, x1)

    # ---- per-pixel thresholds (centre pixel) --------------------------------
    Yc = Y[ys, xs].astype(np.int32)
    C1c = C1[ys, xs].astype(np.int32)
    C2c = C2[ys, xs].astype(np.int32)

    t0 = np.clip(_mul_rs8(Yc, p['p34']) + np.int32(p['p38']), 0, 65535)
    a1 = np.clip(_mul_rs8(C1c - 32768, p['p3c']) + np.int32(p['p40']), 0, 65535)
    thr1 = np.clip(_mul_rs8_v(t0, a1), 0, 65535)
    thr1 = np.clip(_mul_rs8(thr1, p['p4c']), 256, 32768)
    a2 = np.clip(_mul_rs8(C2c - 32768, p['p44']) + np.int32(p['p48']), 0, 65535)
    thr2 = np.clip(_mul_rs8_v(t0, a2), 0, 65535)
    thr2 = np.clip(_mul_rs8(thr2, p['p50']), 256, 32768)

    thrY = np.float32(p['p30'])
    thr1f = thr1.astype(np.float32)
    thr2f = thr2.astype(np.float32)
    Ycf = Yc.astype(np.float32)
    C1cf = C1c.astype(np.float32)
    C2cf = C2c.astype(np.float32)

    # ---- 5x5 stride-2 thresholded box average --------------------------------
    sum1 = np.zeros(Yc.shape, np.float32)
    sum2 = np.zeros(Yc.shape, np.float32)
    cnt = np.zeros(Yc.shape, np.float32)
    Yf = Y.astype(np.float32)
    C1f = C1.astype(np.float32)
    C2f = C2.astype(np.float32)
    for dy in (-4, -2, 0, 2, 4):
        rs = slice(y0 + dy, y1 + dy)
        for dx in (-4, -2, 0, 2, 4):
            cs = slice(x0 + dx, x1 + dx)
            Yn = Yf[rs, cs]
            C1n = C1f[rs, cs]
            C2n = C2f[rs, cs]
            keep = (np.abs(Ycf - Yn) <= thrY)
            keep &= (np.abs(C1cf - C1n) <= thr1f)
            keep &= (np.abs(C2cf - C2n) <= thr2f)
            keep &= (Yn != 0) & (C1n != 0) & (C2n != 0)
            sum1 += np.where(keep, C1n, np.float32(0))
            sum2 += np.where(keep, C2n, np.float32(0))
            cnt += keep.astype(np.float32)

    n = np.rint(cnt).astype(np.int64)            # vcvtps2dq (exact integers)
    k = np.where((n >= 1) & (n <= 25), n, 0)
    rec = RECIP_TABLE[k]                          # uint32
    s1 = np.rint(sum1).astype(np.int64).astype(np.uint32)
    s2 = np.rint(sum2).astype(np.int64).astype(np.uint32)
    with np.errstate(over='ignore'):
        o1 = ((s1 * rec + np.uint32(0x4000)) >> 15)
        o2 = ((s2 * rec + np.uint32(0x4000)) >> 15)
    o1 = np.minimum(o1, 65535).astype(np.uint16)
    o2 = np.minimum(o2, 65535).astype(np.uint16)

    y_out = np.full((hh, ww), pad_fill, np.uint16)
    c1_out = np.full((hh, ww), pad_fill, np.uint16)
    c2_out = np.full((hh, ww), pad_fill, np.uint16)
    c1_out[ys, xs] = o1
    c2_out[ys, xs] = o2
    return y_out, c1_out, c2_out


def written_region(shape, factor=4, ctx_params=None):
    """(row slice, col slice) of the region the binary actually writes."""
    p = dict(DEFAULT_CTX)
    if ctx_params:
        p.update(ctx_params)
    ws = int((p['W'] - 1 + factor) / factor)
    hs = int((p['H'] - 1 + factor) / factor)
    return slice(4, hs + 8), slice(4, ws + 8)
