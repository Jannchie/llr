"""Edit.exe's `ZcTaskRawNRSIMD`, the filter that consumes `rawnr.NoiseModel`.

`rawnr.py` reads the body's noise curve out of the ARW and hands it to llr's own
wavelet denoiser. This module is the other half: the filter Sony actually runs on
that curve, transcribed from the AVX2 kernels rather than approximated.

Why transcribe it rather than keep feeding llr's denoiser Sony's numbers. Twice
now, adopting an engine *parameter* on its own made the result worse -- the
detail-restore gain (see `rawnr.DETAIL_GAIN_UNIT`) and a `min(tag, 256)` clamp
that was measured and reverted. Both failed the same way: Sony's parameters are
tuned for a sigma filter over a 5x5 sparse neighbourhood, and llr's wavelet does
not remove the same detail to begin with, so a number that is right for one is
wrong for the other. Matching the engine's parameters is not matching its
result. Taking both together is the only self-consistent option.

The engine's own values, read out of the running process rather than assumed
(PIPELINE.md 7.13.3, notes/static-rawnr.md 5.3):

* `gain` is **256** on the SIMD path -- i.e. detail is passed through unchanged
  -- even though the `0x78cc` tag reads 480 and 268 on the two frames checked.
  The SIMD path does not amplify detail, so the tag is not its gain.
* `tbl3` (the blend weight) is the constant 512, i.e. an even mix, at the
  slider's neutral position. Off-neutral it is
  ``trunc((t + 100) / 200 * 1024)`` with ``t = (UI - 50) * 2``.
* `limit` is 1023.
* The offsets are 0 for both red/blue phases and -512 for green, derived at
  `0x3a0018..0x3a0041` as differences of calibration words.

The geometry is read from the disassembly, not fitted: row indices y-4, y-2, y,
y+2, y+4 at `0x3a1c87..0x3a1d13`, columns at the same spacing of 2, centre at
`-0x8`. Five by five is 25 taps, matching the 25 mask vectors the epilogue
accumulates.

Verified against captures of the running engine by
`sony_repro/tools/rawnr_e2e_verify.py`, on five frames from three bodies: from
the mosaic all the way through, every one of the four phase planes is **bit
identical**, error identically zero. Nothing is fitted -- the tap geometry,
the constants, the threshold table and the accumulation orders are all read out
of the disassembly or off the running process.

Getting the last handful of pixels there was entirely about float32 rounding.
Addition is not associative, so the order the 25 comparison-base members are
summed in decides which taps land either side of the threshold; the last two
differing pixels in ~920k came down to one tap each sitting within 2 ulp of it.
That is why the base tables below are written in the engine's order rather than
in scan order, and why rewriting the accumulation as `np.stack(...).sum(-1)` or
an `einsum` breaks it.

The operators run as the compiled kernels in `rawnr_numba`; this module holds
the constants, the tables, the dispatch and the per-plane schedule, and the
record of how each was decoded. A whole-array numpy transcription was the
version scored against the engine and `rawnr_numba` was checked `array_equal`
against it -- the same bits, not "close" -- before it was retired; the
accumulation orders below are the ones that comparison pinned.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor

import numpy as np

from . import rawnr_numba as _kernels

#: The engine's 14-bit working scale.
LEVEL_MAX = 16383

#: Entries in the threshold table the engine builds -- `filt` clamps its lookup
#: to this, not to LEVEL_MAX (see OUTPUT_CEILING).
TABLE_SIZE = 1 << 15

#: What the filter actually clamps its output to -- 2**18 - 1, read from the
#: constant its `vminps` broadcasts (RVA 0x4697FC), not the 14-bit ceiling.
#: The same constant guards the threshold lookup. It is far above anything the
#: measured frames reach (their outputs top out near 3.3k), so nothing observed
#: so far distinguishes it from LEVEL_MAX -- which is exactly why assuming the
#: 14-bit value went unnoticed. Assume nothing here: read the constant.
OUTPUT_CEILING = 262143

#: `tbl3`'s fixed-point unit -- the blend weight is `tbl3[...] / 1024`.
BLEND_UNIT = 1024.0

#: Blend weight at the slider's neutral position: an even mix of the centre
#: low-pass and its 3x3 mean. Captured as a flat table at two ISOs.
BLEND_NEUTRAL = 512

#: Detail gain on this path, 256 being unity. Not the `0x78cc` tag -- see the
#: module docstring; the SIMD path measured 256 while the tag read 480 and 268.
DETAIL_GAIN = 256

#: Halo clamp on the restored detail, in engine levels.
DETAIL_LIMIT = 1023

#: Tap spacing and count, both read off the disassembly. Not tunable.
TAP_SPACING = 2
TAPS_PER_SIDE = 2
FILT_MARGIN = TAP_SPACING * TAPS_PER_SIDE
#: The analysis step eats one more pixel on each side than the filter does.
PHASE_MARGIN = FILT_MARGIN + 1

#: Per-phase offsets, from `0x3a0018..0x3a0041`. Green sits half a level low so
#: its analysis can produce a signed detail without clipping at zero.
OFFSET_RB = 0
OFFSET_GREEN = -512

#: Members of the comparison base -- the value each tap is tested against, as
#: `blend/1024 * centre + (1 - blend/1024) * mean(members)`.
#:
#: Red and blue take their own 3x3 at the tap spacing. Green takes a 25-member
#: table that is nothing like it, measured a point at a time by feeding the
#: kernel controlled input (sony_repro/tools/green_base_probe.py): each member
#: was raised on its own while the tap ring swept, and the shift in the ring's
#: accept/reject transition reads that member's weight directly. Every member
#: came out at 0.0202 against a predicted (1 - 512/1024)/25 = 0.02, and the
#: centre at 0.525 against 0.52 (green_base_center.py).
#:
#: ⚠️ The green table is NOT symmetric and NOT the tap set. `(+2,+-1)` is a
#: member while `(-2,+-1)` is not; `(0,+-1)` is not a member at all; and none of
#: the 16 outer taps are. Twelve of the 25 sit on the *other* green phase, a
#: count read independently by raising that whole plane (k = 12.00). It looks
#: wrong and it is not -- straightening it into something symmetric drops the
#: reproduction from bit-identical to 56%.
BASE_RB = tuple((dy * TAP_SPACING, dx * TAP_SPACING)
                for dy in (-1, 0, 1) for dx in (-1, 0, 1))
#: ⚠️ The order here is not cosmetic and not a preference. `filt` accumulates
#: these straight through in float32, so a different order lands on a different
#: last bit and moves taps that sit exactly on the threshold.
#:
#: It is the engine's own accumulation order, read off the `vaddps` chain at
#: `0x3a1206..0x3a12c5`: the second entry and the last eight come straight from
#: the chain's operands (`[r14+r15-8]` is (-2,0), and so on -- the registers
#: hold row offsets in bytes, r15 points at centre+2 floats). The first, third,
#: fourth and fifth are preloaded into ymm7/ymm2/ymm9/ymm10 before the chain, so
#: their identities were settled by enumerating all 24 permutations and scoring
#: each over whole frames (sony_repro/tools/ymm_order_search.py).
#:
#: That enumeration was re-run once `BASE_GREEN_OTHER[0]` below was corrected,
#: since the two tables' rounding interacts. Two of the 24 tie at 100.0000% on
#: all five frames and both phases -- this one and the same with `(0,0)` and
#: `(0,-2)` swapped. Nothing measured separates them; the rest of the field
#: trails at 99.9999% or below.
#:
#: This order takes both green phases to 100.0000% bit-identical. Re-sorting into
#: scan order, which looks tidier, costs 0.001%.
BASE_GREEN_OWN = ((0, 0), (-2, 0), (0, -2), (0, 2), (2, 0),
                  (2, -1), (2, 1),
                  (-1, -1), (-1, 0), (-1, 1),
                  (1, -1), (1, 0), (1, 1))
#: Indexed by phase. The own-plane table is shared, but the cross-plane one is
#: not: the two green phases sit on opposite Bayer diagonals, and the kernel is
#: handed `flagA/flagB` swapped between the two calls (0,-1 then -1,0).
#:
#: Phase 0's order is read the same way, off the `vaddps` chain at
#: `0x3a1253..0x3a12be` (sony_repro/tools/other_order_probe.py hooks each one and
#: reads the base register, since seven of them go through `rax` and cannot be
#: told apart statically). Ten of the twelve decode directly; `(-2,0)` and `(2,0)`
#: sit on the two sites whose addressing has no displacement to solve, and fall
#: out by position. Note the row +2 group comes *before* row +1 -- the same shape
#: as the own table above, and worth 99.9985% -> 100.0000% on the three frames it
#: was scored on. The two frames still short after that came down to one tap each
#: sitting within 2 ulp of the threshold; re-running the own-table enumeration
#: above against the corrected order closed both.
BASE_GREEN_OTHER = (
    ((-2, 0), (-2, 1),
     (-1, -1), (-1, 0), (-1, 1), (-1, 2),
     (2, -1), (2, 0), (2, 1), (2, 2),
     (1, 0), (1, 1)),
    ((-1, -1), (-1, 0),
     (0, -2), (0, -1), (0, 0), (0, 1),
     (1, -2), (1, -1), (1, 0), (1, 1),
     (2, -1), (2, 0)),
)

#: ISO interpolation on the strength, at `0x39fb4c..0x39fbbe`, applied by
#: `apply_strength` when the exec writes its result back. This is why the stage
#: does almost nothing at base ISO: at ISO 100 only four tenths of the filter's
#: change survives.
ISO_STRENGTH_LO_ISO = 400.0
ISO_STRENGTH_HI_ISO = 1600.0
ISO_STRENGTH_LO = 0.4
ISO_STRENGTH_HI = 1.0


def iso_strength(iso: float) -> float:
    """The strength multiplier the engine applies for a shot's ISO.

    Flat at 0.4 up to ISO 400, flat at 1.0 from ISO 1600, linear in ISO between
    them. Linear in ISO itself rather than in its logarithm: that is how the
    interpolation at `0x39fb4c` reads.
    """
    if not np.isfinite(iso) or iso <= ISO_STRENGTH_LO_ISO:
        return ISO_STRENGTH_LO
    if iso >= ISO_STRENGTH_HI_ISO:
        return ISO_STRENGTH_HI
    t = (iso - ISO_STRENGTH_LO_ISO) / (ISO_STRENGTH_HI_ISO - ISO_STRENGTH_LO_ISO)
    return ISO_STRENGTH_LO + t * (ISO_STRENGTH_HI - ISO_STRENGTH_LO)


#: Where Edit's manual Noise Reduction panel meets Auto. Measured at export on
#: DSC03036 (ISO 2000): with the panel at its defaults -- 量 50 / 色彩降噪 5 /
#: 边缘降噪 50 -- the stage's output is bit-identical to Auto's, so the manual
#: default *is* Auto. Away from 50 the amount is not a write-back blend (that
#: is the ISO's, `apply_strength`, and it stays): the engine rebuilds the
#: threshold table from a scaled strength (`NoiseModel.for_amount`) and fills
#: the blend table from the slider (`blend_table_value`) -- verified bit for
#: bit at 75 and 100 on ISO 25600 tiles, and it is what makes UI 0 off (a zero
#: table admits no neighbour). Above 50 the engine also adds a median pass on
#: luma (ZcTaskYNR, lumanr.py), which lives after the tone chain and so on the
#: browser's side of the wire. sony_repro/notes/highiso-denoise-gap.md 3.
MANUAL_AMOUNT_AUTO = 50.0


def apply_strength(filtered: np.ndarray, original: np.ndarray, strength: float) -> np.ndarray:
    """What the exec (`0x39fab0`) writes back after the four kernels have run.

    The ISO strength is not in the thresholds and not in the kernels -- both
    reproduce the engine bit for bit at ISO 100 -- it is applied on the way
    *out*: the filtered plane is blended with the plane the stage was given,
    by `iso_strength(ISO)`, and truncated to integer levels (the output plane
    is uint16). Measured on the engine's own input/output planes
    (sony_repro/tools/rawnr_strength_probe.py): fl_test (ISO 1250, 0.825) and
    a7v_donor (ISO 100, 0.4) both 100.0000% with this float32 order; the
    `in + s*(f - in)` association loses a dozen pixels per frame.

    So at base ISO the kernels do their full job and six tenths of it is thrown
    away here -- which is why the stage was measured "doing almost nothing" at
    ISO 100 (static-rawnr.md), and why `amount` in llr must not also carry it.
    The truncation belongs to the engine even at full strength.
    """
    s = np.float32(strength)
    f = filtered.astype(np.float32, copy=False)
    o = original.astype(np.float32, copy=False)
    # The same three operations, in one traversal and on threads. Worth it
    # because this is not arithmetic-bound: as numpy it was five whole-array
    # passes over 132 MB each on the 33 MP frame, which measured 0.12 s
    # against this one's 0.025 s. Flat views, since the write-back is
    # elementwise and its callers are not all the same rank.
    f = np.ascontiguousarray(f)
    o = np.ascontiguousarray(o)
    if f.shape != o.shape:
        raise ValueError("filtered and original planes must be the same shape")
    out = np.empty(f.shape, dtype=np.float32)
    args = (f.reshape(-1), o.reshape(-1), out.reshape(-1), s, np.float32(1) - s)
    _run_rows(f.size, lambda i0, i1: _kernels.strength_rows(*args, i0, i1),
              ELEMENTWISE_STRIP, KERNEL_WORKERS)
    return out


def blend_table_value(ui: float) -> int:
    """`tbl3`'s constant for a Manual Noise Reduction slider position.

    ``trunc((t + 100) / 200 * 1024)`` with ``t = (UI - 50) * 2``, which lands on
    the measured 512 at UI 50, 1024 at UI 100 and 0 at UI 0.
    """
    t = (float(ui) - 50.0) * 2.0
    return int(np.clip((t + 100.0) / 200.0 * BLEND_UNIT, 0.0, BLEND_UNIT))


#: How the compiled kernels are cut up for threads. Every output pixel is a
#: function of its own neighbourhood and nothing else, so row strips change no
#: bit -- a load-balance knob, not a result knob. Small strips on every core,
#: because the kernel has no 25-temporary working set to keep resident: all
#: that is left to buy is a tail short enough not to decide the wall clock. Measured on one 8 MP green
#: plane: 32/24 0.024 s, 64/24 0.025 s, 128/24 0.026 s, 128/12 0.030 s,
#: 256/12 0.033 s. Single-threaded it is 0.20 s, i.e. this schedule is worth 8x.
KERNEL_STRIP_ROWS = 32
KERNEL_WORKERS = max(1, os.cpu_count() or 1)

#: The same schedule for the flat elementwise kernel (`apply_strength`), in
#: elements rather than rows: 1 MB of float32 a piece, so a 33 MP frame's plane
#: set is ~130 pieces over the workers and a test-sized plane is one.
ELEMENTWISE_STRIP = 1 << 18

#: How much of a row `filt_rows` carries through its 25 taps at once -- see its
#: docstring for why it works a segment at a time at all. Its five working
#: buffers are this many float32 each. Not a result knob: pixels are
#: independent and the arithmetic inside one is untouched.
#:
#: What it buys is having *a* segment rather than a pixel; the segment's length
#: barely matters. Measured on one 8 MP plane, single thread: 32 cols 0.177 s,
#: 64 0.163 s, 128 0.160 s, 256 0.161 s, 512 0.157 s, 4096 (i.e. the whole row,
#: 2344 px) 0.163 s -- against 0.52 s per pixel. So this is the middle of a
#: plateau, not a tuned peak, and only the shortest segment is measurably worse:
#: at 32 the per-chunk prologue starts to show. It is kept well inside L1 anyway
#: rather than left at the row length, because the row length is the caller's
#: image width and this should not become one.
KERNEL_CHUNK_COLS = 256


def _run_rows(height: int, work: Callable[[int, int], None],
              strip_rows: int, workers: int) -> None:
    """Call `work(y0, y1)` over row strips covering `0..height`, on threads.

    The kernels hold the GIL for none of their run (`nogil=True`), so this is
    real parallelism; a strip's inputs are read straight out of the whole plane,
    so no strip has to invent an edge and the split cannot change a value.
    """
    starts = range(0, height, strip_rows)
    n_workers = max(1, min(workers, len(starts)))
    if n_workers == 1:
        for a in starts:
            work(a, min(height, a + strip_rows))
        return

    def run(a: int) -> None:
        work(a, min(height, a + strip_rows))

    with ThreadPoolExecutor(max_workers=n_workers) as pool:
        list(pool.map(run, starts))


def _plane(a: np.ndarray) -> np.ndarray:
    """`a` as the kernels want it: a contiguous 2-D float32 plane.

    float32 is the contract the whole transcription is written in -- the engine
    works in float32 and the accumulation orders were pinned in it -- so a
    float64 caller is cast rather than computed in its own dtype.
    """
    if a.ndim != 2:
        raise ValueError("expected a 2-D phase plane")
    return np.ascontiguousarray(a, dtype=np.float32)


def analysis_rb(plane: np.ndarray, offset: float) -> tuple[np.ndarray, np.ndarray]:
    """`0x3a2aa0`: the red/blue analysis. Loses one pixel on each side.

    The 3x3 ring with weights (1,2,1 / 2,.,2 / 1,2,1) sums to 12, so
    ``d = (12c - sum) / 16`` is the centre minus a binomial low-pass, and `ref`
    is that low-pass itself.
    """
    src = _plane(plane)
    d = np.empty((src.shape[0] - 2, src.shape[1] - 2), dtype=np.float32)
    ref = np.empty_like(d)
    off = np.float32(offset)
    _run_rows(d.shape[0],
              lambda y0, y1: _kernels.analysis_rb_rows(src, off, d, ref, y0, y1),
              KERNEL_STRIP_ROWS, KERNEL_WORKERS)
    return d, ref


#: Where the green analysis reads the *other* green phase, by phase index.
#: The two phases sit on opposite Bayer diagonals, so each sees the other's four
#: nearest samples on the opposite side -- phase 1's set is phase 0's through a
#: point reflection. Getting the sign of `dx` wrong here still explains 94% of
#: the variance and still looks like a plausible image; it just leaves every
#: green pixel about 7 levels off, which is enough to pull the channels apart
#: into magenta/green fringing at a highlight edge.
CROSS_GREEN = (((-1, 0), (-1, 1), (0, 0), (0, 1)),
               ((1, 0), (1, -1), (0, 0), (0, -1)))


def analysis_green(plane: np.ndarray, other: np.ndarray, offset: float,
                   phase: int = 0) -> tuple[np.ndarray, np.ndarray]:
    """`0x3a26f0`: the green analysis, which reads *both* green phases.

    Twelve taps on its own plane plus four on the other green plane weighted 3,
    summing to 24, divided by 28. The two green phases of an RGGB mosaic are
    half a pixel apart diagonally, so the other plane's four nearest samples sit
    closer than this plane's own neighbours -- which is why they carry the
    heavier weight.

    Solved by least squares against a capture that holds this step's input (the
    mosaic) and its output together, which is possible because the analysis is
    purely linear -- no threshold, no branch (sony_repro/tools/
    green_analysis_solve.py). All thirteen coefficients land on exact 28ths and
    the residual is 1.4e-5, with the largest of the other 37 candidates at
    1.8e-10.

    Both planes must be the same shape and the same phase origin.
    """
    src, oth = _plane(plane), _plane(other)
    if src.shape != oth.shape:
        raise ValueError("both green phases must be the same shape")
    d = np.empty((src.shape[0] - 2, src.shape[1] - 2), dtype=np.float32)
    ref = np.empty_like(d)
    cross = np.array(CROSS_GREEN[phase], dtype=np.int64)
    off = np.float32(offset)
    _run_rows(d.shape[0],
              lambda y0, y1: _kernels.analysis_green_rows(
                  src, oth, cross, off, d, ref, y0, y1),
              KERNEL_STRIP_ROWS, KERNEL_WORKERS)
    return d, ref


#: The 5x5 tap set in scan order (dy outer, dx inner). Handed to the kernel
#: rather than hardcoded there, because the scan order decides where the
#: unconditional centre tap lands in the float32 accumulation, and with it the
#: last bit of every output pixel.
_FILT_TAPS = np.array([(dy, dx)
                       for dy in (k * TAP_SPACING
                                  for k in range(-TAPS_PER_SIDE, TAPS_PER_SIDE + 1))
                       for dx in (k * TAP_SPACING
                                  for k in range(-TAPS_PER_SIDE, TAPS_PER_SIDE + 1))],
                      dtype=np.int64)

#: Where the centre lands in that scan -- the one tap the engine never
#: thresholds, and the reason its *position* matters rather than just its
#: presence (see `filt`). Derived, not written down, so it follows
#: TAPS_PER_SIDE.
_FILT_CENTRE_TAP = int(np.flatnonzero((_FILT_TAPS == 0).all(axis=1))[0])


def _member_offsets(members: tuple[tuple[int, int], ...], stride: int) -> np.ndarray:
    """A neighbour table as flat element offsets, in the order it was written.

    The order is the whole point -- these tables are the engine's `vaddps`
    chain, not a set (see BASE_GREEN_OWN) -- so this only ever maps, never
    sorts or deduplicates.
    """
    if not members:
        return np.empty(0, dtype=np.int64)
    a = np.asarray(members, dtype=np.int64).reshape(-1, 2)
    return a[:, 0] * stride + a[:, 1]


def filt(d: np.ndarray, ref: np.ndarray, thresholds: np.ndarray,
         blend: int = BLEND_NEUTRAL, gain: int = DETAIL_GAIN,
         limit: int = DETAIL_LIMIT, offset: float = OFFSET_RB, *,
         other: np.ndarray | None = None,
         base_own: tuple[tuple[int, int], ...] = BASE_RB,
         base_other: tuple[tuple[int, int], ...] = (),
         strip_rows: int = KERNEL_STRIP_ROWS, workers: int = KERNEL_WORKERS) -> np.ndarray:
    """`0x3a1b00` (red/blue) and `0x3a0c30` (green): the sigma filter.

    Loses `FILT_MARGIN` on each side. A sigma filter whose comparison reference
    is not the centre pixel but a mix of the centre low-pass and a mean over
    `base_own`/`base_other`, and whose taps are read from `ref` rather than from
    the original plane. Both of those are what separate it from
    `ZcTaskRawNRHalf`, and both were what a parameter sweep over the Half
    operator's shape could not recover -- it explained 12.8% at best.

    The two kernels differ *only* in that base: red and blue compare against
    their own 3x3 at the tap spacing, green against a 25-member table spanning
    both green phases (`BASE_GREEN_OWN` / `BASE_GREEN_OTHER`). Tap set,
    threshold lookup, divisor and degenerate case are shared.

    The operator, as `rawnr_numba.filt_rows` computes it per pixel:

    * Threshold: ``thresholds[clip(ref, 0, len - 1)]``. Clamped to the *table*,
      not to LEVEL_MAX. The engine's own ceiling here is 262143 (read from the
      constant its `vminps` broadcasts, at RVA 0x4697FC), i.e. far above
      anything `analysis_*` can produce -- those already clamp ref to
      [0, 32767], so the engine never truncates the lookup at all. Clamping to
      16383 instead would quietly fold every ref above it onto one table entry;
      it happens to change nothing on the frames measured so far (their ref
      tops out around 2.7k) and would be wrong on a brighter one.
    * Members: accumulated straight through, own then cross -- NOT
      `sum(own) + sum(cross)`. float32 addition is not associative, so summing
      the two groups separately and adding the totals lands on a different last
      bit than the engine's single chain of `vaddps`. Checked point by point
      against the engine with float32 *scalar* arithmetic: 8 of green's 11
      differing points come out exactly right this way and wrong the other way
      (sony_repro/tools/exact_point_check.py). Own first, then cross: reversing
      it fixed all four points that differed from the engine at the time and
      broke more elsewhere -- green fell from 99.9978% to 99.9946% over the
      whole frame. Judging an order by the handful of points it currently gets
      wrong is overfitting; score it on the whole frame instead
      (sony_repro/tools/base_order_search.py scores cross-first at 4/4 on those
      points, and it is still the worse choice).
    * Base, in the engine's exact order (0x3a13d8..0x3a13f0, constants read
      from 0x4DF260 / 0x4DEA7C):
      ``base = [(1024 - blend) * mean + blend * centre] * (1/1024)`` with
      ``mean = members * (1/n)``. `blend` enters as the raw table integer
      (512), not as 512/1024, and the normalisation is a multiply by 1/n
      (`vmulps ymm11`), not a divide. All of that is algebraically the same as
      `w*centre + (1-w)*mean` and different in the last bit, which is what
      decides a tap sitting on the threshold.
    * Taps: the 5x5 set at spacing 2, each accepted where
      ``|base - tap| < threshold``; the centre tap goes in unconditionally and
      only the other 24 are thresholded. The engine's disassembly has exactly
      24 `vandnps`/`vmovmskps` pairs against 25 taps, and the missing one is
      this. It is worth far more than "one tap in 25" suggests, because `base`
      is centre-dominated: |base - centre| = (1-w)|m25 - centre| exceeds the
      threshold precisely at a hard edge, so thresholding the centre would drop
      it exactly where the neighbourhood has already been rejected -- which is
      what made `count == 0` reachable at all. With this, all four phase planes
      reproduce the engine bit for bit; without it one frame's green sat at
      89.6% (sony_repro/tools/thr_source_test.py). The centre must be
      accumulated **in its place in the scan**, not hoisted out as the running
      total's seed: green reproduces the engine on 89.3% instead of 100% if the
      order changes.
    * Output: ``clip(total / count + clip(d * gain/256, -limit, limit) - offset,
      0, OUTPUT_CEILING)``. `count` is at least 1 everywhere because the centre
      tap is unconditional, so this never divides by zero. That matters: it
      used to be reachable, and writing 0 there put raw level 0 -- blacker than
      black -- into 0.45% of an ISO 2000 frame (sony_repro/tools/
      sony_nr_audit.py). The engine has no branch here either; its captures
      show the centre kept where every other tap is rejected
      (sony_repro/tools/count_zero_probe.py), which is exactly what an untested
      centre tap produces.

    Everything float32 is computed here, once per plane, and handed over as
    float32 scalars: a Python float reaching the kernel would be a float64
    literal there and would promote the expression it lands in.

    `strip_rows` / `workers` are cache and core knobs, not result knobs: the
    output is bit-identical for any values (KERNEL_STRIP_ROWS above).
    """
    if base_other and other is None:
        # Falling back to own-plane-only would score 47.9% while looking
        # almost right.
        raise ValueError("base_other needs the other green phase's ref")
    src = _plane(d)
    centre = _plane(ref)
    if src.shape != centre.shape:
        raise ValueError("d and ref must be the same shape")
    r = FILT_MARGIN
    height, width = src.shape[0] - 2 * r, src.shape[1] - 2 * r
    if height <= 0 or width <= 0:
        raise ValueError(f"plane too small for the {FILT_MARGIN}-pixel margin")
    if thresholds.ndim != 1 or thresholds.shape[0] < 1:
        raise ValueError("thresholds must be a 1-D table")
    if base_other:
        cross = _plane(other)  # type: ignore[arg-type]  (checked above)
        if cross.shape != centre.shape:
            raise ValueError("the other green phase's ref must match ref's shape")
    else:
        # Unused, but numba wants an array rather than an optional.
        cross = centre

    # Flat element offsets, so a tap costs an add rather than a multiply. The
    # three planes are the same shape, so one stride serves all of them.
    stride = centre.shape[1]
    n = len(base_own) + len(base_other)
    tap_off = _FILT_TAPS[:, 0] * stride + _FILT_TAPS[:, 1]
    own_off = _member_offsets(base_own, stride)
    other_off = _member_offsets(base_other, stride)
    # The whole table up front rather than a gather per pixel: converting every
    # entry gives the same float32 as converting the ones actually looked up.
    table = np.ascontiguousarray(thresholds, dtype=np.float32)
    out = np.empty((height, width), dtype=np.float32)
    args = (np.float32(BLEND_UNIT - blend), np.float32(blend),
            np.float32(1.0 / BLEND_UNIT), np.float32(1.0 / n),
            np.float32(gain / 256.0), np.float32(limit), np.float32(-limit),
            np.float32(offset), np.float32(table.shape[0] - 1),
            np.float32(OUTPUT_CEILING))
    _run_rows(height,
              lambda y0, y1: _kernels.filt_rows(src, centre, cross, table, tap_off,
                                                _FILT_CENTRE_TAP, own_off, other_off, out,
                                                y0, y1, r, KERNEL_CHUNK_COLS, *args),
              strip_rows, workers)
    return out


def warmup() -> None:
    """Compile the kernels on a tiny plane, so the first frame does not.

    Cold, with an empty numba cache, the three kernels take 0.9 s of LLVM; warm
    they come back from the on-disk cache `cache=True` writes, in 0.08 s. It is
    that second figure that matters here -- the cache is per machine, not per
    process, so every run after the first pays it, and a worker started per
    request would otherwise pay it inside its first frame. The daemon calls this
    on a background thread at startup (cli.py `_warm_kernels`); the kernels are
    nogil, so it does not block.
    """
    n = 2 * PHASE_MARGIN + 2
    plane = np.full((n, n), 1000.0, dtype=np.float32)
    table = np.full(TABLE_SIZE, 50.0, dtype=np.float32)
    denoise_phase_rb(plane, table)
    denoise_greens(plane, plane.copy(), table)
    apply_strength(plane, plane, 1.0)


def denoise_phase_rb(plane: np.ndarray, thresholds: np.ndarray,
                     **kw: object) -> np.ndarray:
    """One red or blue phase plane in and out; loses `PHASE_MARGIN` a side."""
    d, ref = analysis_rb(plane, OFFSET_RB)
    return filt(d, ref, thresholds, offset=OFFSET_RB, **kw)  # type: ignore[arg-type]


def denoise_phase_green(plane: np.ndarray, other: np.ndarray,
                        thresholds: np.ndarray, phase: int = 0,
                        **kw: object) -> np.ndarray:
    """One green phase plane in and out, reading the other green phase too.

    `phase` picks which cross-plane base table to use -- 0 for the kernel's
    first call, 1 for its second. Getting it backwards is not a small error:
    swapping the two tables drops the reproduction to 56%.

    Green's filter (`0x3a0c30`) was decoded by **controlled input**, not by
    reading it. The disassembly stalls because the kernel precomputes (row base
    + column base) pairs into stack slots at `0x3a1001..0x3a10c3`, so the nine
    base registers in the accumulation chain are not nine rows. Instead the
    kernel was hooked and its `refOwn`/`refOther` overwritten
    (`sony_repro/tools/green_impulse.py`), which removes the spatial correlation
    that had been hiding everything:

    * **Tap set**: the kernel's own plane, 5x5 at spacing 2, equally weighted --
      the same geometry as red and blue. Regression over a 17x17 candidate pool
      across both planes puts exactly 25 coefficients above 1/50, every one at
      0.0400, and the largest of the other 553 at 0.000000. This refutes
      `notes/static-rawnr.md` 6's "the 25 taps must include the other green
      plane": they do not.
    * **Divisor**: the accepted-tap count, not a fixed 25 (a fixed 25 scores 0%
      bit-identical and explains negative variance). `0x3a12c5`'s `vmulps ymm11`
      -- the `1/25` the notes recorded -- is those taps' own average, feeding
      the `vmaxps/vminps/vcvttss2si` + table fetch right after it.
    * **Comparison base**: `(centre + mean of 25 members)/2`, and the members
      are neither the taps nor anything symmetric. See `BASE_GREEN_OWN` /
      `BASE_GREEN_OTHER`; each member's weight was measured on its own by
      raising that one position and watching where the tap ring's transition
      moved.

    Everything above is one experiment answering one question, which is why the
    earlier attempts failed: each input then moved the base and the tap
    selection together, so an impulse sweep said the base was the 25 taps'
    own mean while white noise said it was red/blue's 3x3, and neither was
    right. Both are still checked as controls in `green_base_verify.py` (40.1%
    and 33.9%) against the measured table's 100% -- on both green phases, and on
    real-image captures with detail restore live.

    One earlier conclusion had to be withdrawn: `green_other_role.py` reported
    that the other green plane "does not affect this path at all". It only ever
    tested the accumulation. Its noise amplitude was 3 against a threshold of
    24, so a base that tracked the other plane would still not have flipped a
    single tap's accept/reject. Twelve of the 25 base members live there.

    A convenience over `denoise_greens`, which is the production entry and the
    one place the green wiring lives; this filters both phases and keeps one,
    so it is for tests and tools, not the frame path.
    """
    if phase == 0:
        return denoise_greens(plane, other, thresholds, **kw)[0]
    return denoise_greens(other, plane, thresholds, **kw)[1]


def denoise_greens(plane0: np.ndarray, plane1: np.ndarray,
                   thresholds: np.ndarray,
                   **kw: object) -> tuple[np.ndarray, np.ndarray]:
    """Both green phase planes at once -- what a whole frame actually needs.

    Each phase needs *both* analyses (its own, and the other phase's, for the
    cross taps): the engine runs both green analyses before either filter, so
    the cross taps read the other phase's *analysed* plane, not its raw one.
    Doing the two analyses once here is half the analysis work of filtering the
    phases separately.
    """
    d0, ref0 = analysis_green(plane0, plane1, OFFSET_GREEN, 0)
    d1, ref1 = analysis_green(plane1, plane0, OFFSET_GREEN, 1)
    common = {"offset": OFFSET_GREEN, "base_own": BASE_GREEN_OWN, **kw}
    return (filt(d0, ref0, thresholds, other=ref1,  # type: ignore[arg-type]
                 base_other=BASE_GREEN_OTHER[0], **common),
            filt(d1, ref1, thresholds, other=ref0,  # type: ignore[arg-type]
                 base_other=BASE_GREEN_OTHER[1], **common))
