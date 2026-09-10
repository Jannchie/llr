"""Sony's highlight chroma rolloff, as `ZcTaskChromaSuppres` runs it.

The engine's YCC section is seven stages, and this is the third:

    RGB2YCC -> AreaCompSIMD -> **ChromaSuppres** -> YGamma
            -> SIMDSharpness -> SIMDSpica -> YCC2RGB

It runs unconditionally — with Noise Reduction off as well as on — and it is
*not* an identity anywhere, which is what this module's predecessor comment in
profile.py got wrong for a long time. Per pixel, with `Y` the int16 luma on the
engine's 0..16383 scale **before** YGamma and Cr/Cb the two chroma planes
centred on 32768:

    f = 255
    if Y < loY:  f = 255 - (((loY - Y) * slopeLo) >> 12)
    if Y > hiY:  f = 255 - (((Y - hiY) * slopeHi) >> 12)
    f = clamp(f, 0, 255)
    Y  = max(Y, 0)
    Cr = trunc_toward_zero((Cr - 32768) * f / 256) + 32768
    Cb = same

So the mid-tones are multiplied by 255/256 — a 0.39% chroma loss, not identity —
and above `hiY` the chroma fades out linearly, reaching zero `4096*255/slopeHi`
luma units past `hiY` (about 2040 units for slope 512, roughly 12% of full
scale). The rounding is truncation toward zero on the *signed* difference, which
in integers is `v = (C - 32768) * f; (v + (v < 0 ? 255 : 0)) >> 8`.

Where the parameters come from: four root-level tags of the already-decrypted
SR2SubIFD, the same block sr2.py reads everything else out of. 0x787e is three
A anchors, 0x787f three B anchors, 0x7880 `slopeLo` and 0x7881 the lo/hi
`ratio`. On the ILCE-7CM2 (and the four other frames checked) those read
A = [512, 112, 128], B = [15360, 15360, 15360], slopeLo = 512, ratio = 0.

The derivation is transcribed from the engine at RVA 0x36e920; see
chroma_suppres_terms for it. `x` is what the static read-through took for an
ISO axis in stops (`lv[0xc] / 300.0`) — but it reads 0.0 at ISO 100, 1250, 2000
and 4000 alike, across three body codes, so whatever it is, it is not ISO and
the interpolation below never engages. `sc` is `lv[0x18ee] / 64`, measured
64/64 = 1.0. Both default to their measured values and the anchors alone decide
the terms on every frame seen so far (sony_repro/notes/measured-chroma-gap.md
2.24.1).

**The one approximation.** The engine does not use B directly: it sends B
through four LUTs (two pre-LUTs, then MainGamma's tone LUT, then YGamma's) and
takes `hiY` from the far end. Measured on the real LUTs that chain is a round
trip — chain(B)/B = 0.9997 over 1024..15360, so B = 15360 comes back as 15356 —
and it only saturates (at 15584) for B above about 15600. This module uses
`hiY = B` and accepts the gap of at most four luma units: it moves `f` by at
most one 1/256 step, and only on the sliver of pixels within four units of the
knee.

Verified with hiY = 15356, loY = 0, slopeHi = slopeLo = 512: the integer form
below reproduces the engine's own output planes **100.0000% bit-exactly** on
twelve full-resolution tiles captured at export across four frames (ISO 100,
1250, 2000 and 4000; Noise Reduction off and on Auto — Y, Cr and Cb all exact).
tests/fixtures/chromasuppres_tile.npz is a crop of one.
"""

from __future__ import annotations

import struct
from collections.abc import Sequence
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import numpy as np

from .sr2 import read_sr2_arrays

# The four SR2SubIFD tags, all top-level: they describe the body's rolloff, not
# any one Creative Look, so none of them lives in a per-look SR2DataIFD.
CS_A_TAG = 0x787E      # SHORT[3], the A anchors -> slopeHi
CS_B_TAG = 0x787F      # SHORT[3], the B anchors -> hiY
CS_SLOPE_TAG = 0x7880  # SHORT, slopeLo
CS_RATIO_TAG = 0x7881  # SHORT, the lo/hi ratio -> loY
CS_TAGS = (CS_A_TAG, CS_B_TAG, CS_SLOPE_TAG, CS_RATIO_TAG)

Y_FULL_SCALE = 16383   # the engine's luma range, the scale hiY/loY are on
CHROMA_CENTRE = 32768  # both chroma planes are unsigned, centred here

# `f` is a numerator over 256 clamped to 0..255, so the *most* chroma any pixel
# keeps is 255/256 — the mid-tone loss this stage is easy to mistake for noise.
F_UNIT = 256
F_MAX = 255
# Both slopes are applied as `(distance * slope) >> 12`.
SLOPE_UNIT = 4096

# The engine's ISO axis is `lv[0xc] / 300.0`, in stops, and `sc` is
# `lv[0x18ee] / 64`. Named here because the derivation reads as nonsense
# without them; both are parameters rather than constants because no frame
# measured so far has moved either off its default.
ISO_STOP_UNIT = 300.0
SC_UNIT = 64.0
# The two ramp terms of the derivation: how far slopeHi rises and hiY falls per
# unit of `ramp` (which runs 0..1 over each of the two interpolated ISO bands).
RAMP_SLOPE_HI = 150.0
RAMP_HI_Y = -1200.0


@dataclass(frozen=True)
class ChromaSuppresTerms:
    """The four numbers the stage actually runs on, in the engine's own units.

    Everything upstream — the six anchors, the ISO axis, the scale — exists only
    to produce these, and the pixel loop reads nothing else.
    """

    hi_y: int
    lo_y: int
    slope_hi: int
    slope_lo: int

    def to_json(self) -> dict[str, Any]:
        """Wire form, for the shader. Engine units throughout: hiY/loY are on
        the 0..16383 luma scale and the slopes are over SLOPE_UNIT."""
        return {
            "hiY": int(self.hi_y),
            "loY": int(self.lo_y),
            "slopeHi": int(self.slope_hi),
            "slopeLo": int(self.slope_lo),
        }


def chroma_suppres_terms(anchors: Sequence[int], x: float = 0.0,
                         sc: float = 1.0) -> ChromaSuppresTerms:
    """Eight SR2 values -> the four terms, exactly as RVA 0x36e920 derives them.

    `anchors` is [A0, A1, A2, B0, B1, B2, slopeLo, ratio], the four tags read in
    tag order. `x` is the shot's position on the engine's ISO axis in stops and
    `sc` its scale term; see the module docstring for where both come from and
    why they default to what every measured frame holds.

    The A and B anchors are the ends of two interpolation bands, one stop wide
    each, below x = 0 — so a shot at or above the axis origin takes A0/B0 flat.
    `ramp` runs 0..1 over each band and is what lifts slopeHi while pulling hiY
    down; outside both bands it is zero and the ramp terms vanish.

    Truncation, not rounding, at every int(): these are C casts.
    """
    a0, a1, a2, b0, b1, b2, slope_lo, ratio = (int(v) for v in anchors)
    a: float = float(a0)
    b: float = float(b0)
    if -2.0 <= x < -1.0:
        a = (a1 - a2) * (x + 2.0) + a2
        b = (b1 - b2) * (x + 2.0) + b2
    if -1.0 <= x < 0.0:
        b = (b0 - b1) * (x + 1.0) + b1
        a = (a0 - a1) * (x + 1.0) + a1
    a_i, b_i = int(a), int(b)

    ramp = 0.0
    if -2.0 <= x <= -1.0:
        ramp = x + 2.0
    if -1.0 <= x <= 0.0:
        ramp = -x

    # `hi_y = b_i` is the approximation the module docstring covers: the engine
    # runs B through four LUTs here, and that chain is a round trip to within
    # four luma units over the whole range B is ever seen in.
    hi_y = b_i
    # Guarded because the engine guards it; a zero B would mean no rolloff at
    # all, and dividing by it is how the transcription would find out.
    lo_y = 0 if b_i == 0 else (hi_y * ratio) // b_i
    return ChromaSuppresTerms(
        hi_y=hi_y + int(ramp * RAMP_HI_Y),
        lo_y=lo_y,
        slope_hi=int(a_i / sc) + int(ramp * RAMP_SLOPE_HI),
        slope_lo=int(slope_lo / sc),
    )


def chroma_suppres_gain(y: np.ndarray, terms: ChromaSuppresTerms) -> np.ndarray:
    """The chroma factor for normalised luma `y` in [0, 1], as a float in [0, 1].

    The float path, for a render that works on normalised values rather than on
    the engine's 14-bit planes: `y * Y_FULL_SCALE` puts the pixel back on the
    engine's scale, the two knees are evaluated there, and the result comes back
    as `f / 256`. `floor` stands in for the engine's `>> 12`, which is the same
    thing on the non-negative distances the branches guarantee.

    Never quite 1.0: the mid-tone value is 255/256. That is the stage, not a
    rounding artefact — see the module docstring.
    """
    y16 = np.asarray(y, dtype=np.float32) * np.float32(Y_FULL_SCALE)
    f = np.full(y16.shape, float(F_MAX), dtype=np.float32)
    # The two knees in the engine's order, so that the highlight one wins if a
    # degenerate loY above hiY ever arrives.
    f = np.where(y16 < terms.lo_y,
                 F_MAX - np.floor((terms.lo_y - y16) * terms.slope_lo / SLOPE_UNIT), f)
    f = np.where(y16 > terms.hi_y,
                 F_MAX - np.floor((y16 - terms.hi_y) * terms.slope_hi / SLOPE_UNIT), f)
    return (np.clip(f, 0.0, float(F_MAX)) / F_UNIT).astype(np.float32)


def apply_chroma_suppres_planes(tile: np.ndarray, terms: ChromaSuppresTerms) -> np.ndarray:
    """The exact integer stage on an engine tile: (h, w, 3) of (Y, Cr, Cb).

    Y arrives as the int16 luma reinterpreted into uint16 — the engine's own
    plane, which can be negative — and the two chroma planes are unsigned and
    centred on CHROMA_CENTRE. Kept beside the float path because it is what the
    bit-exactness claim in the module docstring is checked against, and what a
    research tool comparing against a captured tile needs.
    """
    y = tile[..., 0].astype(np.int16).astype(np.int64)
    f = np.full(y.shape, F_MAX, dtype=np.int64)
    f = np.where(y < terms.lo_y,
                 F_MAX - (((terms.lo_y - y) * terms.slope_lo) >> 12), f)
    f = np.where(y > terms.hi_y,
                 F_MAX - (((y - terms.hi_y) * terms.slope_hi) >> 12), f)
    f = np.clip(f, 0, F_MAX)

    def scale(plane: np.ndarray) -> np.ndarray:
        # Truncation toward zero, which for an arithmetic right shift means
        # nudging the negatives up by F_UNIT - 1 first. Round-to-nearest here
        # matches only about half the pixels.
        v = (plane.astype(np.int64) - CHROMA_CENTRE) * f
        return ((v + np.where(v < 0, F_UNIT - 1, 0)) >> 8) + CHROMA_CENTRE

    return np.stack([
        np.maximum(y, 0),
        scale(tile[..., 1]),
        scale(tile[..., 2]),
    ], axis=-1).astype(np.uint16)


def _read_terms(path: str | Path) -> ChromaSuppresTerms | None:
    try:
        got = read_sr2_arrays(path, CS_TAGS)
    except (KeyError, OSError, struct.error, IndexError, ValueError):
        # Anything that is not a Sony RAW can fail anywhere in the IFD walk, not
        # just at a missing tag — a truncated directory runs the entry loop off
        # the end of the buffer. The caller asked what this body suppresses, and
        # for such a file the answer is "nothing here to read".
        return None
    a, b = got.get(CS_A_TAG), got.get(CS_B_TAG)
    slope, ratio = got.get(CS_SLOPE_TAG), got.get(CS_RATIO_TAG)
    if a is None or b is None or slope is None or ratio is None:
        return None
    if len(a) < 3 or len(b) < 3 or not slope or not ratio:
        return None
    return chroma_suppres_terms([*a[:3], *b[:3], slope[0], ratio[0]])


@lru_cache(maxsize=8)
def _cached_terms(path: str, size: int, mtime: int) -> ChromaSuppresTerms | None:
    """The terms keyed on the file's identity, as profile._cached_calibrations is.

    Reaching the four tags means reading and decrypting the whole SR2 block, and
    the profile is rebuilt on every slider tick — without this a Creative Look
    change would re-read tens of megabytes for four shorts that cannot have
    moved.
    """
    return _read_terms(path)


def chroma_suppres_from_file(path: str | Path) -> ChromaSuppresTerms | None:
    """This shot's ChromaSuppres terms, or None if the file carries none.

    None means a non-Sony body, an unreadable file, or a Sony RAW predating the
    four tags — all of which render with the stage off rather than with invented
    anchors, because a wrong `hiY` would fade highlight colour that the engine
    leaves alone.
    """
    try:
        st = Path(path).stat()
    except OSError:
        return None
    return _cached_terms(str(path), st.st_size, int(st.st_mtime_ns))
