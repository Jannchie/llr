"""Bit-exact numpy re-implementation of Edit.exe 0x140396290
(ZcTaskSIMDMarble setup / "downsample chroma + crop to inner rect").

Reverse engineered from tools/marble_disasm/fn_140396290.txt.

Signature in the binary:
    int setup_6290(ZcTask *task /*rcx*/, int W /*edx*/, int H /*r8d*/)

It reads the *current* 3-plane set from task+8 (full-res Y / C1 / C2, all the
same geometry), reads the inner rect from task+0x30..0x3c (x0,y0,x1,y1),
allocates a new set of three planes

    plane0 : W   x H   (Y)
    plane1 : W/2 x H   (C1)
    plane2 : W/2 x H   (C2)

fills them, attaches the new set to the task via 0x14016b640 and finally
resets the rect to (0, 0, W, H).

The fill is three sequential loop nests, all *scalar* (there is no AVX2 in
this particular routine -- the vector code lives in the caller/exec):

    iw = x1 - x0            ; inner width   (line 43-44)
    ih = y1 - y0            ; inner height  (line 45-46)
    hw = iw / 2             ; C trunc-toward-zero, cdq/sub/sar (line 96-100)
    HW = W  / 2             ; likewise (line 59-64), = width of the chroma planes

  (1) main copy, j in [0,ih), i in [0,hw):
        Yout[j][2*i  ] = Yin [y0+j][x0+2*i  ]
        Yout[j][2*i+1] = Yin [y0+j][x0+2*i+1]
        C1out[j][i]    = (uint32)(C1in[y0+j][x0+2*i] + C1in[y0+j][x0+2*i+1]) >> 1
        C2out[j][i]    = (uint32)(C2in[y0+j][x0+2*i] + C2in[y0+j][x0+2*i+1]) >> 1

  (2) right edge padding, j in [0,ih), i in [hw, HW):
        Yout[j][2*i] = Yout[j][2*i+1] = Yin [y0+j][x1]
        C1out[j][i]                   = C1in[y0+j][x1]
        C2out[j][i]                   = C2in[y0+j][x1]
      (note: it samples column x1, i.e. one *past* the inner rect)

  (3) bottom edge padding, i in [0,HW), r in [ih, H):
        Yout[r][2*i] = Yout[r][2*i+1] = Yout[ih-1][2*i]     <-- both from 2*i
        C1out[r][i]                   = C1out[ih-1][i]
        C2out[r][i]                   = C2out[ih-1][i]

For the captured call (W=1064, H=616, rect=(8,8,1072,624)) iw==W and ih==H,
so loops (2) and (3) do nothing.

Key instructions establishing the chroma rule (fn_140396290.txt):
    line 128  movzx r14d, word ptr [rax + rbp]      ; C1in[row][x0+2i]
    line 141  movzx r10d, word ptr [rax + r9]       ; C1in[row][x0+2i+1]
    line 160  lea   edx, [r14 + r10]                ; 32-bit sum, no +1 bias
    line 161  shr   edx, 1                          ; logical >>1  == truncation
    line 166  mov   word ptr [rcx + rax], dx        ; store 16-bit
There is no `inc`/`add 1` between the lea and the shr, and no `add edx,1`
anywhere on that path, so it is a truncating average (floor), NOT round-half-up.
"""

import numpy as np


def setup_6290(y, c1, c2, x0, y0, W, H, x1=None, y1=None):
    """Reproduce 0x140396290 bit-exactly.

    Parameters
    ----------
    y, c1, c2 : uint16 (h, w) full-resolution input planes (task+8 set).
    x0, y0    : top-left of the task inner rect (task+0x30, task+0x34).
    W, H      : the edx / r8d arguments (output Y plane size).
    x1, y1    : bottom-right of the inner rect (task+0x38, task+0x3c).
                Default to x0+W / y0+H, which is what the captured task holds.

    Returns
    -------
    (y_out (H,W), c1_out (H,W//2), c2_out (H,W//2)) all uint16.
    """
    y = np.asarray(y)
    c1 = np.asarray(c1)
    c2 = np.asarray(c2)
    assert y.dtype == np.uint16 and c1.dtype == np.uint16 and c2.dtype == np.uint16

    if x1 is None:
        x1 = x0 + W
    if y1 is None:
        y1 = y0 + H

    iw = x1 - x0                       # line 43-44
    ih = y1 - y0                       # line 45-46
    HW = int(W / 2) if W >= 0 else -int(-W // 2)   # sar-after-cdq == trunc
    hw = int(iw / 2) if iw >= 0 else -int(-iw // 2)

    # planes come out of 0x140390d90 -> calloc'ed / zero-initialised buffers
    y_out = np.zeros((H, W), dtype=np.uint16)
    c1_out = np.zeros((H, HW), dtype=np.uint16)
    c2_out = np.zeros((H, HW), dtype=np.uint16)

    # ---- loop 1: crop Y, crop + 2:1 horizontal decimate chroma ------------
    if ih > 0 and hw > 0:
        rows = slice(y0, y0 + ih)
        even = slice(x0, x0 + 2 * hw, 2)          # x0 + 2i
        odd = slice(x0 + 1, x0 + 2 * hw, 2)       # x0 + 2i + 1

        y_out[:ih, 0:2 * hw:2] = y[rows, even]
        y_out[:ih, 1:2 * hw:2] = y[rows, odd]

        # 32-bit unsigned add then logical shift right by 1 (truncate)
        s1 = c1[rows, even].astype(np.uint32) + c1[rows, odd].astype(np.uint32)
        c1_out[:ih, :hw] = (s1 >> np.uint32(1)).astype(np.uint16)
        s2 = c2[rows, even].astype(np.uint32) + c2[rows, odd].astype(np.uint32)
        c2_out[:ih, :hw] = (s2 >> np.uint32(1)).astype(np.uint16)

    # ---- loop 2: right-edge replication, sampling source column x1 --------
    if ih > 0 and hw < HW:
        ye = y[y0:y0 + ih, x1]
        c1e = c1[y0:y0 + ih, x1]
        c2e = c2[y0:y0 + ih, x1]
        for i in range(hw, HW):
            y_out[:ih, 2 * i] = ye
            y_out[:ih, 2 * i + 1] = ye
            c1_out[:ih, i] = c1e
            c2_out[:ih, i] = c2e

    # ---- loop 3: bottom-edge replication of row ih-1 ----------------------
    if HW > 0 and ih < H:
        last = ih - 1
        yl = y_out[last, 0:2 * HW:2].copy()   # only the *even* columns are read
        c1l = c1_out[last, :HW].copy()
        c2l = c2_out[last, :HW].copy()
        for r in range(ih, H):
            y_out[r, 0:2 * HW:2] = yl
            y_out[r, 1:2 * HW:2] = yl         # both halves get column 2*i
            c1_out[r, :HW] = c1l
            c2_out[r, :HW] = c2l

    return y_out, c1_out, c2_out


def setup_6290_scalar(y, c1, c2, x0, y0, W, H, x1=None, y1=None):
    """Literal transcription of the three loop nests (slow, for cross-check)."""
    if x1 is None:
        x1 = x0 + W
    if y1 is None:
        y1 = y0 + H
    iw = x1 - x0
    ih = y1 - y0
    HW = int(W / 2)
    hw = int(iw / 2)

    y_out = np.zeros((H, W), dtype=np.uint16)
    c1_out = np.zeros((H, HW), dtype=np.uint16)
    c2_out = np.zeros((H, HW), dtype=np.uint16)

    for j in range(ih):
        sr = y0 + j
        for i in range(hw):
            sx = x0 + 2 * i
            y_out[j, 2 * i] = y[sr, sx]
            y_out[j, 2 * i + 1] = y[sr, sx + 1]
            c1_out[j, i] = (int(c1[sr, sx]) + int(c1[sr, sx + 1])) >> 1
            c2_out[j, i] = (int(c2[sr, sx]) + int(c2[sr, sx + 1])) >> 1

    for j in range(ih):
        sr = y0 + j
        for i in range(hw, HW):
            y_out[j, 2 * i] = y[sr, x1]
            y_out[j, 2 * i + 1] = y[sr, x1]
            c1_out[j, i] = c1[sr, x1]
            c2_out[j, i] = c2[sr, x1]

    for i in range(HW):
        yv = y_out[ih - 1, 2 * i]
        c1v = c1_out[ih - 1, i]
        c2v = c2_out[ih - 1, i]
        for r in range(ih, H):
            y_out[r, 2 * i] = yv
            y_out[r, 2 * i + 1] = yv
            c1_out[r, i] = c1v
            c2_out[r, i] = c2v

    return y_out, c1_out, c2_out
