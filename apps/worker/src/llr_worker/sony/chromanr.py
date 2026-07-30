"""Reproduce the chroma cleanup Edit.exe does in its final stage.

`ZcTaskSIMDMarble` is the last stage of the engine's chain and, besides Clarity,
it removes the fine-scale chroma noise. Measured on its own input and output at
full resolution (`sony_repro/tools/marble_fit.py`, and the ledger in
`notes/measured-chroma-gap.md`):

* the fine-scale colour differences drop 11x to 21x -- R-G 70.6 to 3.3 in 14-bit
  units -- while the whole-tile standard deviation stays put, 623.7 to 623.6, so
  large-scale colour structure survives;
* luma moves about 0.3%, so the operator is chroma-only;
* the output's fine chroma band is *uncorrelated* with the input's, |r| at most
  0.032 with a linear fit explaining under 0.1%. The band is not shrunk, it is
  **removed**, and the small remainder matches 14-bit quantisation of the colour
  transform happening in the same stage.

So the shape is: zero the chroma detail below a cutoff scale, keep everything
above it, leave luma alone. That is what this module does.

**This belongs after the tone curve, not before.** A per-channel non-linear curve
turns luma noise into chroma noise, so cleaning chroma first would simply have it
manufactured again downstream -- which is why the engine does this in its final
stage. Running this early is not a weaker version of the fix, it is no fix.

⚠️ Two things the measurement does not settle. The cutoff scale is inferred from
box radii 9 through 31 scoring identically and from the whole-tile std being
unchanged, so "about 20px" is a bracket, not a calibration -- hence `levels` is a
parameter to sweep rather than a constant to trust. And nothing measured says how
the engine treats a *sharp colour edge*: a step contains every scale, so a plain
lowpass softens it, and whether Marble protects such edges is untested. If a test
frame shows colour bleeding across hard edges, that is the first thing to revisit.
"""

from __future__ import annotations

import numpy as np

#: The engine's own YCbCr coefficients, read out of `ZcTaskYCC2RGB` (RVA
#: 0x3713e0) rather than taken from a standard: 1e4 fixed point, with the
#: inverse using 14020 for Cr into R, 3441 and 7141 into G, and 17720 for Cb
#: into B, over a Y coefficient of 10000. The engine's midpoint offset
#: 0x14ab0000 equals (3441 + 7141) * 32768 exactly, which is how the read was
#: confirmed. These are Rec.601 to four decimals; using the engine's integers
#: keeps the round trip identical to its own.
_CR_TO_R = 14020 / 10000
_CB_TO_G = 3441 / 10000
_CR_TO_G = 7141 / 10000
_CB_TO_B = 17720 / 10000

#: Luma weights implied by the inverse above, i.e. the forward transform's rows.
_LUMA = np.array([0.299, 0.587, 0.114], dtype=np.float32)

#: Sets the band this works over; the guided radius is ``2**levels // 2``.
#:
#: Calibrated against Edit's output on three frames (`tools/tone_axis.py`). The
#: guided filter converges rather than overshooting -- 1.95x, 1.35x, 1.22x, 1.18x
#: of Edit's chroma-to-luma ratio for levels 3 through 6 -- because preserving
#: edges bounds how much it can take out. Level 4 is chosen on per-frame
#: agreement rather than on the median: it gives 0.103 / 0.176 / 0.138 against
#: Edit's 0.102 / 0.179 / 0.081, so two frames land within 2% and the third is
#: the outlier at any level.
#:
#: That per-frame tracking is the point. The unguided scale split reached 1.08x
#: at the median but its per-frame values were 0.040 / 0.045 / 0.048 -- flat,
#: while Edit's vary nearly threefold. Matching a median while ignoring the
#: variation means the mechanism is wrong even when the summary number is right.
DEFAULT_LEVELS = 4


def _halve(plane: np.ndarray) -> np.ndarray:
    """2x2 box average. Odd rows or columns are dropped, and `_expand` pads back."""
    h, w = plane.shape[0] & ~1, plane.shape[1] & ~1
    p = plane[:h, :w]
    return (p[0::2, 0::2] + p[1::2, 0::2] + p[0::2, 1::2] + p[1::2, 1::2]) * np.float32(0.25)


def _upsample(plane: np.ndarray, axis: int) -> np.ndarray:
    """Double `axis`, interpolating without shifting the image.

    A box-decimated parent sits at the midpoint of its two children, so each
    child is 3/4 its own parent plus 1/4 the parent on its side. Averaging the
    doubled pixels instead -- the obvious way -- moves everything half a pixel per
    level, which accumulates into a visible offset by level three and which no
    amount of noise testing will reveal, because noise has no position to shift.
    """
    n = plane.shape[axis]
    idx = np.arange(n)
    prev = np.take(plane, np.clip(idx - 1, 0, n - 1), axis=axis)
    nxt = np.take(plane, np.clip(idx + 1, 0, n - 1), axis=axis)
    even = np.float32(0.75) * plane + np.float32(0.25) * prev
    odd = np.float32(0.75) * plane + np.float32(0.25) * nxt
    out = np.repeat(plane, 2, axis=axis)
    lo: list[slice] = [slice(None)] * plane.ndim
    hi: list[slice] = [slice(None)] * plane.ndim
    lo[axis] = slice(0, None, 2)
    hi[axis] = slice(1, None, 2)
    out[tuple(lo)] = even
    out[tuple(hi)] = odd
    return out


def _expand(plane: np.ndarray, shape: tuple[int, int]) -> np.ndarray:
    """Shift-free doubling on both axes, padded back to `shape`."""
    up = _upsample(_upsample(plane, 0), 1)
    dh, dw = shape[0] - up.shape[0], shape[1] - up.shape[1]
    if dh > 0 or dw > 0:
        up = np.pad(up, ((0, max(dh, 0)), (0, max(dw, 0))), mode="edge")
    return up[: shape[0], : shape[1]]


def coarse_only(plane: np.ndarray, levels: int = DEFAULT_LEVELS) -> np.ndarray:
    """`plane` with everything below the cutoff scale removed.

    Repeated halving then expansion back. A pyramid rather than one wide box
    because a box's rolloff is slow enough to eat structure well above its own
    width, and the engine demonstrably keeps that structure.
    """
    cur = plane.astype(np.float32, copy=True)
    shapes: list[tuple[int, int]] = []
    for _ in range(max(levels, 0)):
        if min(cur.shape) < 2:
            break
        shapes.append(cur.shape)
        cur = _halve(cur)
    for shape in reversed(shapes):
        cur = _expand(cur, shape)
    return cur


#: Regularisation for the luma-guided variant, in units of the luma range. Small
#: enough that a real luma edge dominates it, large enough that flat areas fall
#: back to a plain average.
#:
#: ⚠️ Not calibrated -- `DEFAULT_LEVELS` was swept with this held at 1e-4, and the
#: two interact: the guide carries luma noise too, so where the guide's local
#: variance is comparable to eps the filter treats that noise as structure and
#: keeps some of the chroma noise correlated with it. On the real frames that
#: still landed within 2% of Edit on two of three, but on a synthetic flat field
#: -- where the guide is *nothing but* noise -- it is the degenerate case and
#: removes much less than the unguided split. Sweeping the pair jointly against
#: `tools/tone_axis.py` is the obvious next refinement.
GUIDE_EPS = 1e-4


def _box(plane: np.ndarray, radius: int) -> np.ndarray:
    """Box mean with edge padding, via a summed-area table."""
    k = 2 * radius + 1
    p = np.pad(plane.astype(np.float64), radius, mode="edge")
    cs = np.pad(p.cumsum(0).cumsum(1), ((1, 0), (1, 0)))
    total = cs[k:, k:] - cs[:-k, k:] - cs[k:, :-k] + cs[:-k, :-k]
    return (total / (k * k)).astype(np.float32)


def guided_by_luma(chroma: np.ndarray, luma: np.ndarray, radius: int,
                   eps: float = GUIDE_EPS) -> np.ndarray:
    """Smooth `chroma` but stop at edges that exist in `luma`.

    A plain scale split matches Edit's *flat-area* numbers and still fails the
    picture: on a poster with hard colour boundaries it washes small saturated
    marks out and mushes colour edges, while Edit keeps them crisp. The flat-area
    metric cannot see that -- there are no edges in a flat area -- so matching it
    is necessary and not sufficient.

    Luma guidance is the mechanism that fits everything measured: Edit leaves luma
    untouched, so luma still carries every edge, and colour boundaries in real
    images almost always coincide with one.
    """
    mean_i = _box(luma, radius)
    mean_p = _box(chroma, radius)
    cov = _box(luma * chroma, radius) - mean_i * mean_p
    var_i = _box(luma * luma, radius) - mean_i * mean_i
    a = cov / (var_i + np.float32(eps))
    b = mean_p - a * mean_i
    return _box(a, radius) * luma + _box(b, radius)


def apply_chroma_nr(rgb: np.ndarray, levels: int = DEFAULT_LEVELS,
                    guide: bool = True) -> np.ndarray:
    """Remove the fine chroma band from `rgb`, leaving luma untouched.

    `rgb` is float32 (h, w, 3) in any consistent scale. The luma plane is carried
    through unchanged -- not approximately, exactly -- which is why this converts
    to chroma differences rather than smoothing the channels: the engine moves
    luma by about 0.3% and every bit of that belongs to its Clarity branch, not to
    the chroma cleanup.
    """
    if rgb.ndim != 3 or rgb.shape[-1] != 3:
        raise ValueError(f"expected an (h, w, 3) image, got {rgb.shape}")
    if levels <= 0:
        return rgb.astype(np.float32, copy=True)
    a = rgb.astype(np.float32, copy=False)
    y = a @ _LUMA
    # Chroma as the two colour differences the measurement was made on, scaled to
    # Cb/Cr so the inverse below is the engine's own.
    cr = (a[..., 0] - y) / np.float32(_CR_TO_R)
    cb = (a[..., 2] - y) / np.float32(_CB_TO_B)
    if guide:
        # 2**levels is the cutoff the scale split would have had, so the guided
        # variant covers the same band -- the difference is only that it stops at
        # luma edges instead of averaging across them.
        radius = max(1, 2 ** max(levels, 1) // 2)
        cr = guided_by_luma(cr, y, radius)
        cb = guided_by_luma(cb, y, radius)
    else:
        cr = coarse_only(cr, levels)
        cb = coarse_only(cb, levels)
    out = np.empty_like(a)
    out[..., 0] = y + np.float32(_CR_TO_R) * cr
    out[..., 1] = y - np.float32(_CB_TO_G) * cb - np.float32(_CR_TO_G) * cr
    out[..., 2] = y + np.float32(_CB_TO_B) * cb
    return out
