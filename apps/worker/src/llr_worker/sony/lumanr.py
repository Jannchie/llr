"""Edit.exe's two luma noise-reduction stages, ``ZcTaskBSNR_Y`` and ``ZcTaskYNR``.

Both were read out of the engine instruction by instruction; the derivations and
the addresses live in ``sony_repro/notes/static-bsnr-y.md`` and
``static-ynr.md``. This module is the production form of the reference
implementations in ``sony_repro/tools/{bsnr,ynr}_ref.py``.

Both operate on a **linear** luma plane on the engine's 14-bit scale, not on a
tone-mapped one. That is not an assumption: BSNR_Y indexes its threshold with
the same tables RawNR uses, and those encode noise against *signal level*,
which is only a meaningful relation before the tone curve. Its output clamp is
0x3fff, which fixes the scale.

The two stages split the work by how much the user asked for. BSNR_Y is the
baseline and runs at full strength from UI 25 up; YNR is an extra median pass
whose weight is zero at the neutral UI 50 and only opens as the slider goes
past halfway. So Edit at its default settings applies BSNR_Y alone — which,
measured with a body's own parameters, removes about a seventh of the noise and
nothing at all of an isolated impulse.

Everything either stage needs is derivable from the RAW: the threshold table is
``NoiseModel.threshold`` (verified equal to a captured table at all 32768
entries on three frames), the interpolation weight is a closed form in the
slider, and the detail-restore pair comes from :class:`DetailRestore`.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .rawnr import ENGINE_FULL_SCALE, DetailRestore, NoiseModel

#: Fixed-point unit of the centre-vs-mean interpolation weight: the kernel's
#: ``>> 10``.
WEIGHT_UNIT = 1024

#: Rows per strip. The median needs nine mutable copies of its input, so a
#: 61 MP frame processed whole would want gigabytes; strips bound that while
#: staying far larger than the 5-row halo they overlap by.
STRIP_ROWS = 512

#: How far in from its input BSNR_Y produces valid output. Its 9x9 mean reaches
#: +/-4 and its neighbour test another +/-1, on top of a lowpass that reaches
#: +/-1 from the source. The engine does not clamp at the edges either; it
#: relies on the caller insetting the region of interest.
BSNR_MARGIN = 5

#: The furthest YNR reads from a pixel: +/-2 for the 5x5 median, +/-1 for the
#: Sobel mask.
YNR_HALO = 2

#: How far in from the border the pair together leave the plane untouched. It is
#: the *sum*, because YNR consumes BSNR_Y's output: reading only BSNR_MARGIN in
#: would feed YNR the ring BSNR_Y left invalid, which is both wrong and — since
#: the ring lands in different places depending on how the frame was split —
#: visible as a seam between strips.
MARGIN = BSNR_MARGIN + YNR_HALO

#: YNR's defaults, from the params constructor at 0x14014e580.
YNR_SOBEL_THRESHOLD = 0x1FFF
YNR_MEDIAN_SIZE = 3

_NEIGHBOURS = tuple((dy, dx) for dy in (-1, 0, 1) for dx in (-1, 0, 1) if (dy, dx) != (0, 0))


def weight_for_amount(ui: float) -> int:
    """The centre-vs-mean interpolation weight the Amount slider selects.

    ``trunc((t + 100) / 200 * 1024)`` where ``t = (ui - 50) * 2``, which is the
    engine's own table fill (notes/static-rawnr.md 7.3). It decides what the
    sigma filter compares a neighbour against: the 9x9 mean at UI 0, the plain
    average of the centre pixel and that mean at the neutral UI 50, and the
    centre pixel itself at UI 100. So the slider does not scale a strength — it
    moves the reference, and a reference closer to the centre pixel admits fewer
    neighbours.
    """
    t = (float(ui) - 50.0) * 2.0
    return int((t + 100.0) / 200.0 * WEIGHT_UNIT)


def fade_for_amount(ui: float) -> float:
    """How much of the *original* plane to keep, i.e. BSNR_Y's fade-out.

    Zero — full filtering — everywhere from UI 25 up; from there it ramps to 1.0
    (a complete no-op) at UI 0. The weight sits on the original rather than on
    the filtered result, which inverts what the slider appears to do and is the
    one thing about this stage that is easy to get backwards.
    """
    t = (float(ui) - 50.0) * 2.0
    if t >= -50.0:
        return 0.0
    return min(2.0 * (-50.0 - t) / 100.0, 1.0)


def binomial_lowpass(plane: np.ndarray) -> np.ndarray:
    """The 3x3 binomial blur BSNR_Y filters instead of the source plane.

    Kernel ``[[1,2,1],[2,4,2],[1,2,1]] / 16``. The engine accumulates the taps
    as integers and then multiplies by a float32 0.0625 and truncates rather
    than shifting right by four; for a non-negative sum the two agree, and the
    original form is kept so a signed input would not silently change meaning.

    The border ring is left at zero, matching the engine, which writes only
    ``[1, h-2] x [1, w-2]``.
    """
    a = plane.astype(np.int32, copy=False)
    inner = (2 * a[1:-1, 1:-1] + a[1:-1, :-2] + a[1:-1, 2:]
             + a[:-2, 1:-1] + a[2:, 1:-1])
    total = 2 * inner + a[:-2, :-2] + a[:-2, 2:] + a[2:, :-2] + a[2:, 2:]
    out = np.zeros_like(a)
    out[1:-1, 1:-1] = (total.astype(np.float32) * np.float32(0.0625)).astype(np.int32)
    return out


def _box_mean_9x9(plane: np.ndarray) -> np.ndarray:
    """9x9 box sum divided by 81, truncating — the engine's ``/ 81`` magic.

    Separable, as nine shifted adds per axis rather than an integral image: the
    sums peak at ``81 * 0x3fff``, which fits int32, while an integral image over
    a full frame would need int64 and twice the memory for no benefit. The same
    shifted-add idiom the wavelet denoiser's blur uses.
    """
    a = np.pad(plane.astype(np.int32, copy=False), 4, mode="symmetric")
    h, w = plane.shape
    rows = a[0:h, :]
    for i in range(1, 9):
        rows = rows + a[i:i + h, :]
    total = rows[:, 0:w]
    for i in range(1, 9):
        total = total + rows[:, i:i + w]
    return total // 81


def _median_3x3(plane: np.ndarray) -> np.ndarray:
    """Exact 3x3 median via a sorting network.

    The engine sorts all nine taps with ``std::sort`` and reads the middle one;
    a 19-comparator network reaches the same element without ordering the rest,
    which is what makes this affordable on a full frame. Each comparator is one
    ``minimum``/``maximum`` pair.
    """
    p = np.pad(plane.astype(np.int32, copy=False), 1, mode="edge")
    h, w = plane.shape
    v = [p[dy:dy + h, dx:dx + w].copy() for dy in range(3) for dx in range(3)]

    def swap(i: int, j: int) -> None:
        lo = np.minimum(v[i], v[j])
        np.maximum(v[i], v[j], out=v[j])
        v[i] = lo

    # Median-of-nine network (Paeth); only the comparators that can affect
    # element 4 are kept, which is why some indices never appear again.
    for i, j in ((0, 1), (3, 4), (6, 7), (1, 2), (4, 5), (7, 8),
                 (0, 1), (3, 4), (6, 7), (0, 3), (5, 8), (4, 7),
                 (3, 6), (1, 4), (2, 5), (4, 7), (4, 2), (6, 4), (4, 2)):
        swap(i, j)
    return v[4]


def _median_5x5(plane: np.ndarray) -> np.ndarray:
    """Exact 5x5 median. No network this size is worth hand-writing, so this
    partitions a stacked neighbourhood — which is why strips exist."""
    p = np.pad(plane.astype(np.int32, copy=False), 2, mode="edge")
    h, w = plane.shape
    taps = np.stack([p[dy:dy + h, dx:dx + w] for dy in range(5) for dx in range(5)])
    return np.partition(taps, 12, axis=0)[12]


def _bsnr_strip(plane: np.ndarray, threshold: np.ndarray, weight: int,
                gain: int, limit: int, fade: float) -> np.ndarray:
    low = binomial_lowpass(plane)
    # A 16-bit wrapping subtract read back signed. Both operands sit inside
    # [0, 0x3fff] so nothing actually wraps, but the width is what the engine
    # commits to and a wider input would behave differently.
    high = ((plane.astype(np.int32) - low) & 0xFFFF).astype(np.uint16).view(np.int16).astype(np.int32)

    mean = _box_mean_9x9(low)
    ref = (low * weight + (WEIGHT_UNIT - weight) * mean) >> 10
    thr = threshold[np.clip(low, 0, threshold.size - 1)]

    total = low.copy()
    count = np.ones_like(low)
    padded = np.pad(low, 1, mode="edge")
    h, w = low.shape
    for dy, dx in _NEIGHBOURS:
        n = padded[dy + 1:dy + 1 + h, dx + 1:dx + 1 + w]
        ok = np.abs(n - ref) < thr
        total += np.where(ok, n, 0)
        count += ok
    out = total // count

    # An arithmetic shift, so this floors for a negative high-pass rather than
    # truncating towards zero. The engine's ``sar`` does the same and the
    # difference shows up on every dark pixel.
    out = out + np.clip((high * gain) >> 8, -limit, limit)
    np.clip(out, 0, ENGINE_FULL_SCALE, out=out)

    if fade > 0.0:
        f = np.float32(fade)
        out = (plane.astype(np.float32) * f
               + out.astype(np.float32) * (np.float32(1.0) - f)).astype(np.int32)
    return out


def _ynr_strip(plane: np.ndarray, percent: int, sobel_threshold: int, size: int) -> np.ndarray:
    a = plane.astype(np.int64, copy=False)
    med = (_median_5x5(plane) if size == 5 else _median_3x3(plane)).astype(np.int64)

    # The mask is built on the *median* plane, not the source: the engine's
    # mask builder (0x14039d740) is handed the median it just made. Measured on
    # its own tiles at amount 100 (sony_repro/notes/highiso-denoise-gap.md
    # 9.4): Sobel on the source leaves 0.08-0.13% of the pixels wrong -- the
    # impulses the median removed still fire the mask and hand the source
    # back -- while Sobel on the median reproduces both tiles bit for bit.
    p = np.pad(med, 1, mode="edge")
    h, w = plane.shape
    up, mid, dn = p[0:h, :], p[1:1 + h, :], p[2:2 + h, :]
    gy = ((dn[:, 0:w] + 2 * dn[:, 1:1 + w] + dn[:, 2:2 + w])
          - (up[:, 0:w] + 2 * up[:, 1:1 + w] + up[:, 2:2 + w]))
    gx = ((up[:, 2:2 + w] + 2 * mid[:, 2:2 + w] + dn[:, 2:2 + w])
          - (up[:, 0:w] + 2 * mid[:, 0:w] + dn[:, 0:w]))
    # The test is on (gx^2 + gy^2) / 2 -- the root-mean-square of the two
    # components, not their magnitude. The factor of sqrt(2) matters: it puts
    # the protected gradient at threshold/sqrt(8), about 17.7% of full scale at
    # the default, so only strong edges are held back and everything else is
    # medianed. Steps survive regardless, because that is what a median does;
    # the mask is there for single-pixel lines.
    mag = (gx * gx + gy * gy) // 2
    edge = mag > sobel_threshold * sobel_threshold

    # Where the mask fires the weight is zero and the original passes through
    # bit for bit. A negative percent extrapolates away from the median, which
    # sharpens; that is the same field's behaviour below the neutral UI.
    w_blend = np.where(edge, 0, (0xFFFF * percent) // 100)
    return ((a * (0xFFFF - w_blend) + med * w_blend) // 0xFFFF).astype(np.int32)


@dataclass(frozen=True)
class LumaNRStats:
    """What ran, for the log. Both stages are conditional in different ways, so
    a frame that denoises unlike its neighbours is usually explained here."""

    weight: int
    fade: float
    gain: int
    limit: int
    ynr_percent: int
    ynr_size: int


def apply_luma_nr(
    plane: np.ndarray,
    model: NoiseModel,
    restore: DetailRestore,
    amount_ui: float,
    *,
    sobel_threshold: int = YNR_SOBEL_THRESHOLD,
    median_size: int = YNR_MEDIAN_SIZE,
) -> tuple[np.ndarray, LumaNRStats]:
    """Run BSNR_Y and then YNR over a 14-bit linear luma plane.

    ``plane`` is int-valued on 0..16383; the result has the same shape and dtype
    contract. ``amount_ui`` is Edit.exe's Noise Reduction Amount on its own
    0..100 scale with 50 neutral, and drives both stages the way the engine
    drives them from one recipe field: it moves BSNR_Y's reference, fades
    BSNR_Y out below UI 25, and opens YNR above UI 50.

    A ``MARGIN``-wide border is returned unchanged, as in the engine, so callers
    must not rely on it. The width does not depend on whether the median pass
    runs, so moving the slider never changes which pixels are written.
    """
    weight = weight_for_amount(amount_ui)
    fade = fade_for_amount(amount_ui)
    percent = int((float(amount_ui) - 50.0) * 2.0)
    threshold = model.threshold(np.arange(ENGINE_FULL_SCALE + 1)).astype(np.int32)

    src = np.ascontiguousarray(plane, dtype=np.int32)
    out = src.copy()
    h = src.shape[0]
    for top in range(MARGIN, h - MARGIN, STRIP_ROWS):
        bottom = min(top + STRIP_ROWS, h - MARGIN)
        # Grow the strip by the halo both stages read *together*, then keep only
        # the rows this iteration owns -- otherwise every strip boundary would
        # show the border ring the kernels leave behind.
        lo, hi = max(0, top - MARGIN), min(h, bottom + MARGIN)
        strip = src[lo:hi]
        done = _bsnr_strip(strip, threshold, weight, restore.gain, restore.limit, fade)
        if percent > 0:
            done = _ynr_strip(done, percent, sobel_threshold, median_size)
        out[top:bottom, MARGIN:-MARGIN] = done[top - lo:bottom - lo, MARGIN:-MARGIN]

    stats = LumaNRStats(
        weight=weight, fade=fade, gain=restore.gain, limit=restore.limit,
        ynr_percent=percent, ynr_size=median_size,
    )
    return out, stats
