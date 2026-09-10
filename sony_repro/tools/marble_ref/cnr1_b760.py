"""Bit-exact numpy re-implementation of Edit.exe `0x14038b760` (ZcTaskSIMDMarble CNR step 1).

Called as ``cnr1(factor=4, ctx, task)`` from ``ZcTaskSIMDMarble::exec`` (0x14038a791).

What the function does
----------------------
It builds a brand new 3-plane set of size ``ceil(W/factor) x ceil(H/factor)`` and
fills it with a box-average pyramid level of the *current* plane set:

  * plane 0 (Y, full width W)          -> ``factor x factor``      box
  * plane 1 (C1, half width W/2)       -> ``factor/2 x factor``    box
  * plane 2 (C2, half width W/2)       -> ``factor/2 x factor``    box

The divisor is a power of two, so the binary does ``(sum + (1<<(s-1))) >> s``
(scalar tail) / ``trunc((sum + 2^(s-1)) / 2^s)`` in float32 (AVX2 body); both are
identical for non-negative sums.  Afterwards a **zero result is forced to 1**
(``cmove`` in the scalar path, ``vcmpeqps``/``vblendvps`` against 1.0f in the
AVX2 path) -- the pyramid level is used as a divisor further downstream.

Shift/round constants (0x14038b7bf..0x14038b7ef)::

    factor == 8 : shift_c = 5, shift_y = 6
    else        : shift_c = 3, shift_y = 4     <- factor == 4, the call we replicate
    round_y = 1 << (shift_y - 1)               ([rbp+0x38])
    round_c = 1 << (shift_c - 1)               ([rbp+0x40])

Note the constants are hard-wired for factor in {4, 8}; ``shift_y`` is
``log2(factor*factor)`` and ``shift_c`` is ``log2(factor*factor/2)``.

Edge handling (scalar tail, 0x14038be10..0x14038bf4b): source samples whose row
index is ``>= ctx->H`` (0x2c) or whose column index is ``>= ctx->W`` (0x28) --
``>= ctx->W/2`` for chroma -- are simply *not accumulated* (they contribute 0),
while the divisor stays the full ``1 << shift``.  Partial boxes are therefore
darkened, not renormalised.  The AVX2 fast path (which covers
``x < W - (W % 8)``) does no clamping at all; for the shipping geometry
(W=1064, H=616, factor=4) both W%8 and H%factor are 0 so the two paths agree
everywhere.

This function does **not** write anything into ctx (rdi is only read at +0x28
and +0x2c); it only allocates the new set and attaches it via 0x14016b640.
"""

import numpy as np

__all__ = ["cnr1_b760", "shifts_for_factor"]


def shifts_for_factor(factor):
    """(shift_c, shift_y) exactly as 0x14038b7bf..0x14038b7d6 computes them."""
    if factor == 8:
        return 5, 6
    return 3, 4


def _box_reduce(src, out_h, out_w, box_h, box_w, shift):
    """Sum a box_h x box_w box, add 1<<(shift-1), arithmetic-shift right by
    `shift`, then map 0 -> 1.  Out-of-range source samples contribute nothing."""
    h, w = src.shape
    acc = np.zeros((out_h, out_w), dtype=np.int64)

    for dy in range(box_h):
        ys = np.arange(out_h, dtype=np.int64) * box_h + dy
        ok_y = ys < h
        if not ok_y.any():
            continue
        for dx in range(box_w):
            xs = np.arange(out_w, dtype=np.int64) * box_w + dx
            ok_x = xs < w
            if not ok_x.any():
                continue
            acc[np.ix_(ok_y, ok_x)] += src[np.ix_(ys[ok_y], xs[ok_x])].astype(np.int64)

    res = (acc + (1 << (shift - 1))) >> shift          # add r9d,round / sar r9d,cl
    res[res == 0] = 1                                   # test/cmove  &  vcmpeqps/vblendvps
    return (res & 0xFFFF).astype(np.uint16)             # `mov word ptr [..], r9w`


def cnr1_b760(y, c1, c2, factor=4):
    """Reproduce the new 3-plane set built by 0x14038b760.

    Parameters
    ----------
    y  : (H, W)   uint16 -- plane 0 of the current set (luma, +4096 biased)
    c1 : (H, W/2) uint16 -- plane 1 of the current set (chroma 1, 32768 centred)
    c2 : (H, W/2) uint16 -- plane 2 of the current set (chroma 2)
    factor : int -- the `ecx` argument (4 in the CNR branch).

    Returns
    -------
    (y_s, c1_s, c2_s), each ((H+f-1)//f, (W+f-1)//f) uint16.
    """
    y = np.asarray(y)
    c1 = np.asarray(c1)
    c2 = np.asarray(c2)

    h, w = y.shape                                      # ctx->H (0x2c), ctx->W (0x28)
    shift_c, shift_y = shifts_for_factor(factor)

    out_w = (w + factor - 1) // factor                  # 0x14038b835: (W+f-1)/f
    out_h = (h + factor - 1) // factor                  # 0x14038b842: (H+f-1)/f

    y_s = _box_reduce(y, out_h, out_w, factor, factor, shift_y)
    # chroma column step is `factor/2` (0x14038beb6: eax=f; cdq; sub; sar 1)
    cbox = factor // 2
    c1_s = _box_reduce(c1, out_h, out_w, factor, cbox, shift_c)
    c2_s = _box_reduce(c2, out_h, out_w, factor, cbox, shift_c)
    return y_s, c1_s, c2_s


def cnr1_b760_float_simd(y, c1, c2, factor=4):
    """Literal float32 transcription of the AVX2 body (0x14038bbd0..0x14038bdac).

    Only valid where the box is fully in bounds; kept to prove that the float
    path and the integer shift path are bit-identical (every partial sum is an
    exact integer < 2**24, and the divisor is a power of two)."""
    h, w = y.shape
    shift_c, shift_y = shifts_for_factor(factor)
    out_h, out_w = h // factor, w // factor
    cbox = factor // 2

    def go(src, bh, bw, shift):
        s = src[: out_h * bh, : out_w * bw].astype(np.float32)
        s = s.reshape(out_h, bh, out_w, bw)
        acc = np.zeros((out_h, out_w), np.float32)
        for dy in range(bh):
            for dx in range(bw):
                acc = acc + s[:, dy, :, dx]
        v = np.trunc((acc + np.float32(1 << (shift - 1))) / np.float32(1 << shift))
        v = np.where(v == 0, np.float32(1.0), v)
        return v.astype(np.int32).astype(np.uint16)

    return go(y, factor, factor, shift_y), go(c1, factor, cbox, shift_c), go(c2, factor, cbox, shift_c)
