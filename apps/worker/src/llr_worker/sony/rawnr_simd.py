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
"""

from __future__ import annotations

import numpy as np

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

#: ISO interpolation on the strength, at `0x39fb4c..0x39fbbe`. This is why the
#: stage does almost nothing at base ISO: at ISO 100 it runs at four tenths.
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


def blend_table_value(ui: float) -> int:
    """`tbl3`'s constant for a Manual Noise Reduction slider position.

    ``trunc((t + 100) / 200 * 1024)`` with ``t = (UI - 50) * 2``, which lands on
    the measured 512 at UI 50, 1024 at UI 100 and 0 at UI 0.
    """
    t = (float(ui) - 50.0) * 2.0
    return int(np.clip((t + 100.0) / 200.0 * BLEND_UNIT, 0.0, BLEND_UNIT))


def _shifted(a: np.ndarray, dy: int, dx: int, r: int) -> np.ndarray:
    """`a` translated by (dy, dx), read from its interior so no padding is
    invented. `r` is the margin held back on every side."""
    h, w = a.shape
    return a[r + dy:h - r + dy, r + dx:w - r + dx]


def analysis_rb(plane: np.ndarray, offset: float) -> tuple[np.ndarray, np.ndarray]:
    """`0x3a2aa0`: the red/blue analysis. Loses one pixel on each side.

    The 3x3 ring with weights (1,2,1 / 2,.,2 / 1,2,1) sums to 12, so
    ``d = (12c - sum) / 16`` is the centre minus a binomial low-pass, and `ref`
    is that low-pass itself.
    """
    # copy=False: the frame path already hands these in as float32, and nothing
    # here writes through `a` -- `_shifted` only slices.
    a = plane.astype(np.float32, copy=False)
    c = _shifted(a, 0, 0, 1)
    n4 = (_shifted(a, -1, 0, 1) + _shifted(a, 1, 0, 1)
          + _shifted(a, 0, -1, 1) + _shifted(a, 0, 1, 1))
    diag = (_shifted(a, -1, -1, 1) + _shifted(a, -1, 1, 1)
            + _shifted(a, 1, -1, 1) + _shifted(a, 1, 1, 1))
    d = (np.float32(12.0) * c - np.float32(2.0) * n4 - diag) * np.float32(1.0 / 16.0)
    ref = np.clip(c - d + np.float32(offset), 0.0, 32767.0)
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
    a = plane.astype(np.float32, copy=False)
    b = other.astype(np.float32, copy=False)
    c = _shifted(a, 0, 0, 1)
    n4 = (_shifted(a, -1, 0, 1) + _shifted(a, 1, 0, 1)
          + _shifted(a, 0, -1, 1) + _shifted(a, 0, 1, 1))
    diag = (_shifted(a, -1, -1, 1) + _shifted(a, -1, 1, 1)
            + _shifted(a, 1, -1, 1) + _shifted(a, 1, 1, 1))
    c0, c1, c2, c3 = (_shifted(b, dy, dx, 1) for dy, dx in CROSS_GREEN[phase])
    cross = c0 + c1 + c2 + c3
    total = np.float32(2.0) * n4 + diag + np.float32(3.0) * cross
    d = (np.float32(24.0) * c - total) * np.float32(1.0 / 28.0)
    ref = np.clip(c - d + np.float32(offset), 0.0, 32767.0)
    return d, ref


def filt(d: np.ndarray, ref: np.ndarray, thresholds: np.ndarray,
         blend: int = BLEND_NEUTRAL, gain: int = DETAIL_GAIN,
         limit: int = DETAIL_LIMIT, offset: float = OFFSET_RB, *,
         other: np.ndarray | None = None,
         base_own: tuple[tuple[int, int], ...] = BASE_RB,
         base_other: tuple[tuple[int, int], ...] = ()) -> np.ndarray:
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
    """
    r = FILT_MARGIN
    # Clamp to the *table*, not to LEVEL_MAX. The engine's own ceiling here is
    # 262143 (read from the constant its `vminps` broadcasts, at RVA 0x4697FC),
    # i.e. far above anything `analysis_*` can produce -- those already clamp ref
    # to [0, 32767], so the engine never truncates the lookup at all. Clamping to
    # 16383 instead would quietly fold every ref above it onto one table entry;
    # it happens to change nothing on the frames measured so far (their ref tops
    # out around 2.7k) and would be wrong on a brighter one.
    idx = np.clip(ref, 0, thresholds.shape[0] - 1).astype(np.int32)
    thr_c = _shifted(thresholds[idx].astype(np.float32, copy=False), 0, 0, r)

    # Accumulate straight through, own then cross -- NOT `sum(own) + sum(cross)`.
    # float32 addition is not associative, so summing the two groups separately
    # and adding the totals lands on a different last bit than the engine's
    # single chain of `vaddps`. Checked point by point against the engine with
    # float32 *scalar* arithmetic: 8 of green's 11 differing points come out
    # exactly right this way and wrong the other way
    # (sony_repro/tools/exact_point_check.py).
    # Own first, then cross. Reversing it fixed all four points that differed
    # from the engine at the time and broke more elsewhere: green fell from
    # 99.9978% to 99.9946% over the whole frame. Judging an order by the handful
    # of points it currently gets wrong is overfitting -- score it on the whole
    # frame instead (sony_repro/tools/base_order_search.py scores cross-first at
    # 4/4 on those points, and it is still the worse choice).
    taps = [_shifted(ref, dy, dx, r) for dy, dx in base_own]
    if base_other:
        if other is None:
            raise ValueError("base_other needs the other green phase's ref")
        taps += [_shifted(other, dy, dx, r) for dy, dx in base_other]
    # In place: the same float32 additions in the same order, without a fresh
    # full-plane temporary per member.
    members = taps[0].copy()
    for v in taps[1:]:
        members += v
    n = len(taps)

    # The engine's exact order (0x3a13d8..0x3a13f0), constants read from
    # 0x4DF260 / 0x4DEA7C:
    #     base = [(1024 - blend) * mean + blend * centre] * (1/1024)
    # `blend` enters as the raw table integer (512), not as 512/1024, and the
    # normalisation is a multiply by 1/n (`vmulps ymm11`), not a divide. All of
    # that is algebraically the same as `w*centre + (1-w)*mean` and different in
    # the last bit, which is what decides a tap sitting on the threshold.
    centre = _shifted(ref, 0, 0, r)
    mean_members = members * np.float32(1.0 / n)
    base = ((np.float32(BLEND_UNIT - blend) * mean_members
             + np.float32(blend) * centre) * np.float32(1.0 / BLEND_UNIT))

    # The centre tap goes in unconditionally; only the other 24 are thresholded.
    # The engine's disassembly has exactly 24 `vandnps`/`vmovmskps` pairs against
    # 25 taps, and the missing one is this.
    #
    # It is worth far more than "one tap in 25" suggests, because `base` is
    # centre-dominated: |base - centre| = (1-w)|m25 - centre| exceeds the
    # threshold precisely at a hard edge, so thresholding the centre would drop
    # it exactly where the neighbourhood has already been rejected -- which is
    # what made `count == 0` reachable at all. With this, all four phase planes
    # reproduce the engine bit for bit; without it one frame's green sat at 89.6%
    # (sony_repro/tools/thr_source_test.py).
    # ⚠️ The centre must be accumulated **in its place in the scan**, not hoisted
    # out as the running total's seed: float32 addition is not associative, and
    # green reproduces the engine on 89.3% instead of 100% if the order changes.
    # Hence the branch inside the loop rather than a tidier structure.
    #
    # Masked in-place adds rather than `total += ok * v` with a 0/1 float mask:
    # adding `v` only where accepted is the same float32 addition chain (adding
    # `0.0 * v` was a no-op), so bit-identity holds, and it drops the five
    # full-plane temporaries per tap that the mask arithmetic allocated.
    s = TAP_SPACING
    total = np.zeros_like(base)
    count = np.zeros_like(base)
    diff = np.empty_like(base)
    ok = np.empty(base.shape, dtype=bool)
    offs = [k * s for k in range(-TAPS_PER_SIDE, TAPS_PER_SIDE + 1)]
    for dy in offs:
        for dx in offs:
            v = _shifted(ref, dy, dx, r)
            if dy == 0 and dx == 0:
                total += v
                count += np.float32(1.0)
                continue
            np.subtract(base, v, out=diff)
            np.abs(diff, out=diff)
            np.less(diff, thr_c, out=ok)
            np.add(total, v, out=total, where=ok)
            np.add(count, np.float32(1.0), out=count, where=ok)

    # `count` is at least 1 everywhere because the centre tap is unconditional,
    # so this never divides by zero. That matters: it used to be reachable, and
    # writing 0 there put raw level 0 -- blacker than black -- into 0.45% of an
    # ISO 2000 frame (sony_repro/tools/sony_nr_audit.py). The engine has no
    # branch here either; its captures show the centre kept where every other
    # tap is rejected (sony_repro/tools/count_zero_probe.py), which is exactly
    # what an untested centre tap produces.
    total /= count
    boost = np.clip(_shifted(d, 0, 0, r) * np.float32(gain / 256.0), -limit, limit)
    return np.clip(total + boost - np.float32(offset), 0.0, OUTPUT_CEILING)


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
