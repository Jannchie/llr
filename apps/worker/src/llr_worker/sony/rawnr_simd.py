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
`sony_repro/tools/rawnr_simd.py --verify`: the analysis step is bit-identical,
the red/blue filter is 99.9677% bit-identical, and end to end it explains
100.00% of what the engine did. The 60 differing points out of 185481 come from
one tap falling either side of the threshold; float32 tracks the engine better
than float64 there (60 points against 3170).
"""

from __future__ import annotations

import numpy as np

#: The engine's 14-bit working scale.
LEVEL_MAX = 16383

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
    a = plane.astype(np.float32)
    c = _shifted(a, 0, 0, 1)
    n4 = (_shifted(a, -1, 0, 1) + _shifted(a, 1, 0, 1)
          + _shifted(a, 0, -1, 1) + _shifted(a, 0, 1, 1))
    diag = (_shifted(a, -1, -1, 1) + _shifted(a, -1, 1, 1)
            + _shifted(a, 1, -1, 1) + _shifted(a, 1, 1, 1))
    d = (np.float32(12.0) * c - np.float32(2.0) * n4 - diag) * np.float32(1.0 / 16.0)
    ref = np.clip(c - d + np.float32(offset), 0.0, 32767.0)
    return d, ref


def analysis_green(plane: np.ndarray, other: np.ndarray,
                   offset: float) -> tuple[np.ndarray, np.ndarray]:
    """`0x3a26f0`: the green analysis, which reads *both* green phases.

    Twelve taps on its own plane plus four on the other green plane weighted 3,
    summing to 24, divided by 28. The two green phases of an RGGB mosaic are
    half a pixel apart diagonally, so the other plane's four nearest samples sit
    closer than this plane's own neighbours -- which is why they carry the
    heavier weight.

    Both planes must be the same shape and the same phase origin.
    """
    a = plane.astype(np.float32)
    b = other.astype(np.float32)
    c = _shifted(a, 0, 0, 1)
    n4 = (_shifted(a, -1, 0, 1) + _shifted(a, 1, 0, 1)
          + _shifted(a, 0, -1, 1) + _shifted(a, 0, 1, 1))
    diag = (_shifted(a, -1, -1, 1) + _shifted(a, -1, 1, 1)
            + _shifted(a, 1, -1, 1) + _shifted(a, 1, 1, 1))
    cross = (_shifted(b, 0, 0, 1) + _shifted(b, -1, 0, 1)
             + _shifted(b, 0, -1, 1) + _shifted(b, -1, -1, 1))
    total = np.float32(2.0) * n4 + diag + np.float32(3.0) * cross
    d = (np.float32(24.0) * c - total) * np.float32(1.0 / 28.0)
    ref = np.clip(c - d + np.float32(offset), 0.0, 32767.0)
    return d, ref


def filt(d: np.ndarray, ref: np.ndarray, thresholds: np.ndarray,
         blend: int = BLEND_NEUTRAL, gain: int = DETAIL_GAIN,
         limit: int = DETAIL_LIMIT, offset: float = OFFSET_RB) -> np.ndarray:
    """`0x3a1b00`: the sigma filter. Loses `FILT_MARGIN` on each side.

    A sigma filter whose comparison reference is not the centre pixel but a mix
    of the centre low-pass and its own 3x3 mean, and whose taps are read from
    `ref` rather than from the original plane. Both of those are what separate
    it from `ZcTaskRawNRHalf`, and both were what a parameter sweep over the
    Half operator's shape could not recover -- it explained 12.8% at best.
    """
    r = FILT_MARGIN
    thr_c = _shifted(thresholds[np.clip(ref, 0, LEVEL_MAX).astype(np.int32)]
                     .astype(np.float32), 0, 0, r)

    s = TAP_SPACING
    m9 = sum(_shifted(ref, dy, dx, r) for dy in (-s, 0, s) for dx in (-s, 0, s))
    m9 = m9 * np.float32(1.0 / 9.0)

    centre = _shifted(ref, 0, 0, r)
    w = np.float32(blend / BLEND_UNIT)
    base = w * centre + (np.float32(1.0) - w) * m9

    total = np.zeros_like(base)
    count = np.zeros_like(base)
    offs = [k * s for k in range(-TAPS_PER_SIDE, TAPS_PER_SIDE + 1)]
    for dy in offs:
        for dx in offs:
            v = _shifted(ref, dy, dx, r)
            ok = (np.abs(base - v) < thr_c).astype(np.float32)
            total += ok * v
            count += ok

    # ⚠️ `count` can reach zero, and then this yields 0 rather than the pixel.
    # It happens where `base` lands between two populations further apart than
    # the threshold -- a hard step, where the mix of centre and neighbourhood
    # mean sits in the gap and no tap is within `thr` of it. Kept as the
    # reference has it, which agrees with the captured engine on 99.97% of
    # points, but the engine's own behaviour in this case is *not* established:
    # its `sum/count` is a SIMD divide and what it does at zero was not read
    # out. A synthetic 4000-level step with a threshold of 120 reproduces it.
    mean = total / np.maximum(count, 1.0)
    boost = np.clip(_shifted(d, 0, 0, r) * np.float32(gain / 256.0), -limit, limit)
    return np.clip(mean + boost - np.float32(offset), 0.0, LEVEL_MAX)


def denoise_phase_rb(plane: np.ndarray, thresholds: np.ndarray,
                     **kw: object) -> np.ndarray:
    """One red or blue phase plane in and out; loses `PHASE_MARGIN` a side."""
    d, ref = analysis_rb(plane, OFFSET_RB)
    return filt(d, ref, thresholds, offset=OFFSET_RB, **kw)  # type: ignore[arg-type]


def denoise_phase_green(plane: np.ndarray, other: np.ndarray,
                        thresholds: np.ndarray, **kw: object) -> np.ndarray:
    """One green phase plane in and out, reading the other green phase too.

    ⚠️ The filter kernel used here is the red/blue one (`0x3a1b00`). Green's
    own filter lives at `0x3a0c30` and has *not* been decoded -- only its
    analysis kernel has. The call order `3a2aa0 -> 3a26f0 x2 -> 3a2aa0 ->
    3a1b00 -> 3a0c30 x2 -> 3a1b00` shows they are distinct functions, so
    assuming they are the same operator is an assumption, not a finding. It is
    marked here and must be checked against a green capture before any claim
    that this path reproduces the engine on green.
    """
    d, ref = analysis_green(plane, other, OFFSET_GREEN)
    return filt(d, ref, thresholds, offset=OFFSET_GREEN, **kw)  # type: ignore[arg-type]
