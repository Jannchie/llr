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
#: ⚠️ **Not a calibration axis at the shipped subsample.** `levels` reaches the
#: fast path as ``max(1, radius // subsample)``, so with subsample 8 the values
#: 1 through 4 are one and the same 3x3 coefficient box, bit for bit -- and the
#: GLSL port, a fixed 8x8 moment box plus a 3x3 coefficient box, *is* that
#: setting. Only 5 and up are distinguishable. A sweep over levels 3..6 was run
#: before this was noticed and half of it measured nothing; see
#: sony_repro/notes/measured-chroma-gap.md 2.11 and the test named
#: `test_levels_below_the_subsample_are_one_setting`.
#:
#: 4 is kept because it is what the GLSL implements and because the exact form
#: (subsample < 2), where the radius does bite, agrees with it there.
DEFAULT_LEVELS = 4

#: How much of the filtered chroma survives, matching CHROMA_COMPOSE_SHADER's
#: `u_amount` and CHROMA_AMOUNT in passes.ts.
#:
#: This is the axis that carries the calibration, because it is the only one
#: that can. `levels` collapses at the shipped subsample (above), and `eps` only
#: has authority while it is comparable to the guide's own variance -- on a
#: photograph luma detail dominates ``var_I``, and 13x of eps buys 1.6-3.4x of
#: output where low-ISO frames needed 6-9x.
#:
#: 0.90 is the value that minimises mean |log(llr/Edit)| of the absolute colour
#: difference over all sixteen engine captures: geometric mean 0.98, median
#: 1.01. The previous setting of 1.0 scored 0.543 against this one's 0.351, and
#: sat at a geometric mean of 0.67 -- it over-cleaned.
#:
#: **No ISO term**, and that is a measured decision rather than an omission.
#: Fitting ``amount = k*log2(ISO/100) + b`` gives k = +0.019 per stop, a residual
#: RMS of 0.097 against the constant model's 0.101, and a score of 0.347 against
#: 0.351 -- one percent, for a term the engine's neighbours do carry. The +0.506
#: ISO correlation reported earlier was an artefact of sweeping `eps`: eps'
#: effectiveness depends on ``var_I``, which rises with noise, which rises with
#: ISO. On this axis the correlation is +0.284 and buys nothing.
#:
#: What remains is not ISO and not yet explained: at 0.90 the per-frame ratio
#: still spans 0.36 to 2.44. The optimum *parameter* varies only 0.65-1.00, but
#: the ratio is steep in `amount` near 0.9, so a narrow parameter band still
#: leaves a wide spread in the result. That spread is the honest open item.
DEFAULT_AMOUNT = 0.90


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
#: Swept jointly with `DEFAULT_LEVELS`, because the two interact: the guide
#: carries luma noise too, so wherever its local variance is comparable to eps
#: the filter treats that noise as structure and keeps the chroma noise
#: correlated with it. Against Edit on three frames the grid gives, as a multiple
#: of Edit's chroma-to-luma ratio:
#:
#:     eps      levels 4   levels 5
#:     1e-4       1.35x      1.22x
#:     4e-4       0.77x      0.49x
#:     2e-3       0.65x      0.28x
#:     6e-3       0.61x      0.25x
#:
#: Every larger eps overshoots. Judged on per-frame agreement rather than the
#: median -- geometric mean of the per-frame ratios, 1.19 here against 0.74 at
#: 4e-4, with a slightly tighter spread too -- this corner is the best of the
#: grid. The true optimum sits somewhere between 1e-4 and 4e-4 and three frames
#: cannot resolve it more finely than that.
#:
#: A synthetic flat field is this filter's degenerate case: the guide is nothing
#: but noise, so it removes far less there than the unguided split does. That is
#: why the tests assert ten-times on `guide=False` and only two-times here.
GUIDE_EPS = 1e-4


def _box(plane: np.ndarray, radius: int) -> np.ndarray:
    """Box mean with edge padding, via a summed-area table."""
    k = 2 * radius + 1
    p = np.pad(plane.astype(np.float64), radius, mode="edge")
    cs = np.pad(p.cumsum(0).cumsum(1), ((1, 0), (1, 0)))
    total = cs[k:, k:] - cs[:-k, k:] - cs[k:, :-k] + cs[:-k, :-k]
    return (total / (k * k)).astype(np.float32)


def _box_downsample(plane: np.ndarray, shape: tuple[int, int]) -> np.ndarray:
    """Mean of each block, matching the shader's 8x8 box in CHROMA_MOMENT_SHADER."""
    h = shape[0] * (plane.shape[0] // shape[0])
    w = shape[1] * (plane.shape[1] // shape[1])
    p = plane[:h, :w]
    return p.reshape(shape[0], h // shape[0], shape[1], w // shape[1]).mean(axis=(1, 3))


def _bilinear_to(plane: np.ndarray, shape: tuple[int, int]) -> np.ndarray:
    """Bilinear resample onto `shape`, sampling at pixel centres."""
    h, w = plane.shape
    ys = (np.arange(shape[0], dtype=np.float32) + 0.5) * (h / shape[0]) - 0.5
    xs = (np.arange(shape[1], dtype=np.float32) + 0.5) * (w / shape[1]) - 0.5
    y0 = np.clip(np.floor(ys), 0, h - 1).astype(np.int32)
    x0 = np.clip(np.floor(xs), 0, w - 1).astype(np.int32)
    y1 = np.clip(y0 + 1, 0, h - 1)
    x1 = np.clip(x0 + 1, 0, w - 1)
    fy = np.clip(ys - y0, 0.0, 1.0).astype(np.float32)[:, None]
    fx = np.clip(xs - x0, 0.0, 1.0).astype(np.float32)[None, :]
    top = plane[np.ix_(y0, x0)] * (1 - fx) + plane[np.ix_(y0, x1)] * fx
    bot = plane[np.ix_(y1, x0)] * (1 - fx) + plane[np.ix_(y1, x1)] * fx
    return (top * (1 - fy) + bot * fy).astype(np.float32)


def guided_by_luma_fast(chroma: np.ndarray, luma: np.ndarray, radius: int,
                        eps: float = GUIDE_EPS, subsample: int = 8) -> np.ndarray:
    """The guided filter with its coefficients computed at reduced resolution.

    Exists because the exact form cannot be a GPU pass: a radius-8 box is 17x17
    taps per pixel and there are four moments to gather. The standard fast form
    computes the moments on a decimated pair and bilinearly upsamples ``a`` and
    ``b``, which is cheap enough to be three or four passes -- and, importantly,
    is the form that would actually ship, so it is the one that has to carry the
    calibration rather than the exact one.

    The second box over ``a`` and ``b`` in the exact form is dropped here; the
    bilinear upsample stands in for it, as it does in the literature.
    """
    if subsample < 2:
        return guided_by_luma(chroma, luma, radius, eps)
    small = (max(1, luma.shape[0] // subsample), max(1, luma.shape[1] // subsample))
    # Box-average the blocks, not bilinear-resample them. Resampling reads four
    # neighbours and lets everything between them alias into the moments, which
    # is visible: against the GLSL port, which box-averages, the worst pixel
    # disagreed by 3.4e-2 where the operator's own excursion was 0.164. The box
    # is both the better choice and the one that ships, so the reference follows
    # it rather than the other way round.
    i_s = _box_downsample(luma, small)
    p_s = _box_downsample(chroma, small)
    ii_s = _box_downsample(luma * luma, small)
    ip_s = _box_downsample(luma * chroma, small)
    r = max(1, radius // subsample)
    mean_i = _box(i_s, r)
    mean_p = _box(p_s, r)
    cov = _box(ip_s, r) - mean_i * mean_p
    var_i = _box(ii_s, r) - mean_i * mean_i
    a = cov / (var_i + np.float32(eps))
    b = mean_p - a * mean_i
    return _bilinear_to(a, luma.shape) * luma + _bilinear_to(b, luma.shape)


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
                    guide: bool = True, eps: float = GUIDE_EPS,
                    subsample: int = 0,
                    amount: float = DEFAULT_AMOUNT) -> np.ndarray:
    """Remove the fine chroma band from `rgb`, leaving luma untouched.

    `rgb` is float32 (h, w, 3) in any consistent scale. The luma plane is carried
    through unchanged -- not approximately, exactly -- which is why this converts
    to chroma differences rather than smoothing the channels: the engine moves
    luma by about 0.3% and every bit of that belongs to its Clarity branch, not to
    the chroma cleanup.

    `amount` blends the filtered differences back over the originals, matching
    CHROMA_COMPOSE_SHADER's `u_amount` (same domain, same clamp). It is the only
    axis that actually varies the strength of the shipped configuration: `levels`
    reaches the fast path as ``max(1, radius // subsample)``, so at the shipped
    subsample of 8 every value up to 4 collapses onto the same 3x3 coefficient
    box, and `eps` moves the output by far less than its own range -- 13x of eps
    bought 1.6-3.4x of output on the two frames it was probed on. See §2.11.
    """
    if rgb.ndim != 3 or rgb.shape[-1] != 3:
        raise ValueError(f"expected an (h, w, 3) image, got {rgb.shape}")
    amount = float(np.clip(amount, 0.0, 1.0))
    if levels <= 0 or amount == 0.0:
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
        if subsample >= 2:
            cr = guided_by_luma_fast(cr, y, radius, eps, subsample)
            cb = guided_by_luma_fast(cb, y, radius, eps, subsample)
        else:
            cr = guided_by_luma(cr, y, radius, eps)
            cb = guided_by_luma(cb, y, radius, eps)
    else:
        cr = coarse_only(cr, levels)
        cb = coarse_only(cb, levels)
    if amount < 1.0:
        # In the difference domain and before the rebuild, so luma stays exact
        # for every amount -- the same place CHROMA_COMPOSE_SHADER blends.
        w = np.float32(amount)
        cr = cr * w + ((a[..., 0] - y) / np.float32(_CR_TO_R)) * (1 - w)
        cb = cb * w + ((a[..., 2] - y) / np.float32(_CB_TO_B)) * (1 - w)
    out = np.empty_like(a)
    out[..., 0] = y + np.float32(_CR_TO_R) * cr
    out[..., 1] = y - np.float32(_CB_TO_G) * cb - np.float32(_CR_TO_G) * cr
    out[..., 2] = y + np.float32(_CB_TO_B) * cb
    return out
