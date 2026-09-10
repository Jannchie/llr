"""Read Sony's LinearMatrix16 calibration table straight out of an ARW.

Imaging Edge's colour matrix is not computed from the shot — it is calibration
data the body writes into every frame. `Edit.exe +0x179390` is a pure unpacking
routine: it reads 276 bytes and bit-unpacks them into the whole LinearMatrix16
parameter region without arithmetic. Those 276 bytes come verbatim from tag
0x780f of the ARW's *encrypted* SR2SubIFD.

Addressing:

    IFD0 tag 0xc634 (DNGPrivateData, an inline uint32 = SR2Private IFD offset)
      └─ SR2Private:  0x7200 offset / 0x7201 length / 0x7221 key
      └─ decrypted SR2SubIFD:  tag 0x780f = the 276-byte bitstream

The reverse engineering lives in ../../../sony_repro (PIPELINE.md); its test
suite verifies this decode against frida dumps of the engine's own memory, all
65 sample frames bit-exact.
"""

from __future__ import annotations

import struct
from collections.abc import Sequence
from dataclasses import dataclass
from functools import lru_cache, partial
from pathlib import Path

import numpy as np

from ..creative_style import normalize_style

SR2_PARAM_TAG = 0x780F
PARAM_BLOCK_SIZE = 276
_Q = np.float32(1.0 / 1024.0)  # the fixed-point unit, hard-coded at Edit.exe +0x4DEB78

LOOK_INDEX_TAG = 0x74C0  # uint32[10]: offsets of the per-look SR2DataIFDs
CURVE_X_TAG = 0x7805     # tone curve control points, x (not evenly spaced)
CURVE_Y_TAG = 0x7806     # ...and y
LOOK_NAME_TAG = 0x7770   # the look's own name, e.g. "Standard" / "FL" / "VV2"

# RGB2YCC's eight signed shorts: a base plus four illuminant deltas (chroma.py).
CHROMA_BASE_TAG = 0x7842
CHROMA_ILLUMINANT_TAGS = (0x7843, 0x7844, 0x7845, 0x7846)
_CHROMA_TAGS = (CHROMA_BASE_TAG, *CHROMA_ILLUMINANT_TAGS)

# How much of each illuminant to mix in — int16[4] summing to 1024, a property
# of the shot's white balance rather than of any look, so it lives at the top
# level and applies to all ten. The engine reads it at calibration block +0xe44,
# which is where this tag lands; 0x7847 (+0xe3c) has held the same four values
# in every frame measured, but +0xe44 is what the code indexes.
CHROMA_WEIGHT_TAG = 0x7848
# The camera's own already-blended eight shorts, for the look that was selected
# when the shutter fired. Edit.exe takes this as a shortcut instead of blending
# (0x14036dce0, the `calib+0x1c != 0` branch, reading +0xddc).
CHROMA_FINAL_TAG = 0x7841

# YGamma's two terms, ten entries each, indexed by the in-camera Fade setting.
# Fade is a contrast pull toward a fixed pivot applied to luma alone, and these
# are the tables it reads (Edit.exe 0x14017b480 / 0x14017b5e0, which index them
# at Fade*10 and interpolate in tenths). Top-level, like the illuminant weights.
LUMA_PIVOT_TAG = 0x780B     # uint16[10], on the engine's 0..16383 luma scale
LUMA_CONTRAST_TAG = 0x780E  # uint16[10], 16384 = x1.0
FADE_STEPS = 10             # Fade 0..9, one table entry each
LUMA_CONTRAST_UNIT = 16384

# YGamma's *third* input: the 32768-entry table it indexes Y through before the
# pivot/contrast line (calib+0x318fc). Which table a look gets is decided by
# these two SHORTs, and unlike the Fade tables they are per-look — the ten
# SR2DataIFDs disagree on them within one file. Only two pairs have ever been
# seen, and they select two tables:
#
#   (4096, 14848)  Standard and Neutral — a highlight knee: identity to Y=8192,
#                  then slope 0.90625, so lut[12288] = 11904 and lut[16383] =
#                  15614. Identical on ILCE-7CM2 and ILCE-7M5.
#   (1024, 16384)  the other eight looks — near identity (lut[16383] = 16382).
#                  Identical on the FL and VV2 frames dumped.
#
# The build rule (a 16-step coarse table interpolated linearly, plus a -16 toe
# below Y=4096 that both families share) was not fully reverse-engineered, so
# the two tables ship as data keyed by this pair — see sony/data/ygamma_luts.npz
# and chroma.luma_lut.
LUMA_LUT_C_TAG = 0x780C     # SHORT, per-look
LUMA_LUT_D_TAG = 0x780D     # SHORT, per-look
LUMA_LUT_KNEE = (4096, 14848)   # Standard / Neutral
LUMA_LUT_FLAT = (1024, 16384)   # the other eight
#: Codes whose look wears the knee table, for the name-only fallback below.
LUMA_LUT_KNEE_STYLES = ("ST", "NT")


def luma_lut_key_for(name: str) -> tuple[int, int]:
    """The family a look's *name* implies, for a calibration carrying no tags.

    Only the donor path needs this (profile._borrowed_calibration): the donor
    table was dumped before these two tags were known, so a borrowed look has
    to be placed by name. Every file measured agrees that the split is exactly
    Standard and Neutral against the other eight, so the name is enough.
    """
    return LUMA_LUT_KNEE if normalize_style(name) in LUMA_LUT_KNEE_STYLES else LUMA_LUT_FLAT

# DRO's tone curve, as a piecewise cubic Bezier in log2 luma. The camera decides
# the Auto level and writes the *result* here, so nothing about Sony's grading
# logic has to be re-derived to know what DRO did to a shot (Edit.exe
# 0x14018ad50, reached from 0x140188ae9 with calib+0x10f2 / +0x1106).
DRO_KNOTS_TAG = 0x781B   # uint16[10], /1024 -> log2 luma, the segment boundaries
DRO_CTRL_TAG = 0x781C    # uint16[28] = 9 segments x 3 + 1 control points
DRO_CURVE_POINTS = 104   # the engine builds this many samples
DRO_SEGMENTS = 9
_DRO_UNIT = 1024.0
# vatr[0x70], the log-domain ceiling the engine clamps the curve to. It computes
# it as log2(vatr[0x54] * 8191) with vatr[0x54] = 1.0; measured 12.99982 on all
# 16 frames checked against the engine's own buffer.
DRO_LOG_CEILING = 12.999823410347818

# TIFF field type -> bytes per unit
_TYPE_SIZE = {1: 1, 2: 1, 3: 2, 4: 4, 5: 8, 6: 1, 7: 1, 8: 2, 9: 4, 10: 8, 11: 4, 12: 8}


@dataclass(frozen=True)
class LookCalibration:
    """One Creative Look's factory calibration, straight out of the RAW.

    `param_block` holds the *same* 276 bytes for all ten looks: the colour
    matrix belongs to the body, not to the look. Each SR2DataIFD does carry its
    own 0x780f, but the engine never reads it — it memcpys the SR2SubIFD's
    top-level one into the calibration block and unpacks that. Confirmed
    against the buffer the engine actually unpacks, byte for byte.
    """

    name: str
    param_block: bytes        # 276 bytes -> LinearMatrix16 knot coefficients
    curve_x: np.ndarray       # 128 control points, in units of LUT index * 128
    curve_y: np.ndarray       # ...and their outputs, in units of full scale / 16384
    chroma_base: np.ndarray   # int16[8] -> RGB2YCC cross terms and gains
    chroma_deltas: np.ndarray  # int16[4, 8], the per-illuminant deltas
    chroma_weights: np.ndarray  # int16[4], shared by all ten looks, sums to 1024
    luma_pivot: np.ndarray      # uint16[10], YGamma's pivot per Fade setting
    luma_contrast: np.ndarray   # uint16[10], YGamma's contrast per Fade setting
    # The camera's own blend, present only for the look the shot was taken on
    # (the top-level block is that look's — its 0x7842 matches, and 0x7770 names
    # it). None for the other nine, which have to be blended.
    chroma_final: np.ndarray | None
    # (0x780c, 0x780d), the pair that picks YGamma's LUT. Per-look, so it does
    # not travel with the Fade tables above even though the same stage reads it.
    # Defaulted to the near-identity family: that is the eight-look majority and
    # the one a file that never wrote these tags renders closest to.
    luma_lut_key: tuple[int, int] = LUMA_LUT_FLAT


def decrypt(data: bytes, start: int, length: int, key: int) -> bytes:
    """Decrypt a Sony SR2SubIFD block (equivalent to exiftool Sony.pm::Decrypt).

    The keystream is generated and XORed as big-endian uint32 regardless of the
    file's own byte order. Returns a copy of all of `data` with
    `[start, start+length)` decrypted in place.
    """
    words = length // 4
    pad = [0] * 0x80
    k = key
    for i in range(4):
        lo = (k & 0xFFFF) * 0x0EDD + 1
        hi = (k >> 16) * 0x0EDD + (k & 0xFFFF) * 0x02E9 + (lo >> 16)
        k = ((hi & 0xFFFF) << 16) + (lo & 0xFFFF)
        pad[i] = k
    pad[3] = ((pad[3] << 1) | ((pad[0] ^ pad[2]) >> 31)) & 0xFFFFFFFF
    for i in range(4, 0x7F):
        pad[i] = (((pad[i - 4] ^ pad[i - 2]) << 1) | ((pad[i - 3] ^ pad[i - 1]) >> 31)) & 0xFFFFFFFF

    vals = list(struct.unpack_from(f">{words}I", data, start))
    i = 0x7F
    for j in range(words):
        pad[i & 0x7F] = pad[(i + 1) & 0x7F] ^ pad[(i + 65) & 0x7F]
        vals[j] ^= pad[i & 0x7F]
        i += 1
    out = bytearray(data)
    struct.pack_into(f">{words}I", out, start, *vals)
    return bytes(out)


def _ifd_entries(buf: bytes, pos: int, endian: str):
    """Walk a TIFF IFD, yielding (tag, type, count, value_offset, size).

    `value_offset` points inside the entry for inline values (<= 4 bytes), so
    callers treat inline and out-of-line values alike.
    """
    (n,) = struct.unpack_from(endian + "H", buf, pos)
    for i in range(n):
        o = pos + 2 + i * 12
        tag, typ, cnt = struct.unpack_from(endian + "HHI", buf, o)
        size = _TYPE_SIZE.get(typ, 1) * cnt
        vpos = o + 8 if size <= 4 else struct.unpack_from(endian + "I", buf, o + 8)[0]
        yield tag, typ, cnt, vpos, size


def _find_tag(buf: bytes, pos: int, endian: str, tag: int):
    for t, typ, cnt, vpos, size in _ifd_entries(buf, pos, endian):
        if t == tag:
            return typ, cnt, vpos, size
    return None


def _top_array(
    dec: bytes, sub_pos: int, endian: str, tag: int, *,
    fmt: str, dtype: type, want: int | None = None,
) -> np.ndarray | None:
    """A top-level SR2SubIFD tag as an array, or None if it is absent or short.

    `want` pins the count the caller needs (the DRO tags are fixed-length and a
    shorter one is unusable); left off, the file's own count is read.
    """
    e = _find_tag(dec, sub_pos, endian, tag)
    if e is None:
        return None
    _, cnt, tpos, _ = e
    n = cnt if want is None else want
    if cnt < n:
        return None
    return np.array(struct.unpack_from(f"{endian}{n}{fmt}", dec, tpos), dtype=dtype)


def _decrypted_sr2(path: str | Path) -> tuple[bytes, int, str]:
    """Decrypt the SR2SubIFD -> (whole buffer, its offset, byte order).

    Decryption covers the entire block at once, so callers wanting several tags
    do this once and then walk the result. Raises KeyError when the file is not
    a Sony RAW carrying an SR2Private IFD.
    """
    buf = Path(path).read_bytes()
    if buf[:2] == b"II":
        endian = "<"
    elif buf[:2] == b"MM":
        endian = ">"
    else:
        raise KeyError("not a TIFF container, cannot locate SR2Private")

    (ifd0,) = struct.unpack_from(endian + "I", buf, 4)
    priv = _find_tag(buf, ifd0, endian, 0xC634)  # DNGPrivateData
    if priv is None:
        raise KeyError("IFD0 has no tag 0xc634 (SR2Private)")
    # DNGPrivateData is a BYTE array per the spec, but Sony stores a single
    # uint32 in it: the absolute offset of the SR2Private IFD. count=4 makes it
    # inline, so the value position needs one more dereference.
    _, _, priv_val, _ = priv
    (priv_pos,) = struct.unpack_from(endian + "I", buf, priv_val)

    got = {}
    for t in (0x7200, 0x7201, 0x7221):  # offset / length / key
        e = _find_tag(buf, priv_pos, endian, t)
        if e is None:
            raise KeyError(f"SR2Private is missing tag 0x{t:04x}")
        _, _, vpos, _ = e
        got[t] = struct.unpack_from(endian + "I", buf, vpos)[0]

    return decrypt(buf, got[0x7200], got[0x7201], got[0x7221]), got[0x7200], endian


def read_sr2_tag(path: str | Path, tag: int = SR2_PARAM_TAG) -> bytes:
    """Raw bytes of one tag in the decrypted SR2SubIFD.

    Raises KeyError when the tag is absent or the file is not a Sony RAW with an
    SR2Private IFD — callers fall back to the DCP path on that.
    """
    dec, sub_pos, endian = _decrypted_sr2(path)
    e = _find_tag(dec, sub_pos, endian, tag)
    if e is None:
        raise KeyError(f"SR2SubIFD has no tag 0x{tag:04x}")
    _, _, vpos, size = e
    return dec[vpos:vpos + size]


#: TIFF type code -> struct format, for the types Sony uses for scalar tags.
_SCALAR_FMT = {1: "B", 3: "H", 4: "I", 6: "b", 8: "h", 9: "i"}


def read_sr2_scalars(path: str | Path, tags: Sequence[int]) -> dict[int, int]:
    """Several single-value SR2SubIFD tags, decrypting the block once.

    read_sr2_tag decrypts the whole SR2 block to reach one tag, so a caller
    wanting a group of them (a noise model, a calibration set) would pay for
    that decryption once per tag. This walks the decrypted IFD instead.

    Unlike read_sr2_tag this reports absence by omission rather than by raising:
    a group is normally read to be used together, and a caller that needs every
    member checks the keys it asked for. Values are decoded through the entry's
    own TIFF type and byte order rather than assumed to be little-endian SHORTs.
    """
    dec, sub_pos, endian = _decrypted_sr2(path)
    want = set(tags)
    out: dict[int, int] = {}
    # One walk collecting the whole group, rather than _find_tag's linear rescan
    # from the IFD start per tag.
    for tag, typ, cnt, vpos, _ in _ifd_entries(dec, sub_pos, endian):
        if tag not in want:
            continue
        fmt = _SCALAR_FMT.get(typ)
        if fmt is None or cnt < 1:
            continue
        out[tag] = int(struct.unpack_from(endian + fmt, dec, vpos)[0])
    return out


def read_sr2_arrays(path: str | Path, tags: Sequence[int]) -> dict[int, list[int]]:
    """Several SR2SubIFD tags as whole arrays, decrypting the block once.

    read_sr2_scalars' sibling, for tags whose count is the point: ChromaSuppres'
    anchors are three SHORTs each and taking only the first would silently drop
    the two the ISO interpolation needs. Same walk, same type dispatch, and the
    same "absent means omitted rather than raised" contract — only the unpack
    differs.
    """
    dec, sub_pos, endian = _decrypted_sr2(path)
    want = set(tags)
    out: dict[int, list[int]] = {}
    for tag, typ, cnt, vpos, _ in _ifd_entries(dec, sub_pos, endian):
        if tag not in want:
            continue
        fmt = _SCALAR_FMT.get(typ)
        if fmt is None or cnt < 1:
            continue
        out[tag] = [int(v) for v in struct.unpack_from(f"{endian}{cnt}{fmt}", dec, vpos)]
    return out


@lru_cache(maxsize=8)
def _cached_dro_curve(path: str, size: int, mtime: int) -> np.ndarray | None:
    """dro_curve keyed on the file's identity, as _cached_calibrations is.

    Reaching the curve means reading and decrypting the whole RAW, and both the
    strength and the gain table are asked for it on every profile rebuild — a
    DRO slider tick would otherwise re-read tens of megabytes twice. The array
    is handed to several callers at once, so it goes out read-only.
    """
    curve = _read_dro_curve(path)
    if curve is not None:
        curve.flags.writeable = False
    return curve


def dro_curve(path: str | Path) -> np.ndarray | None:
    """The DRO tone curve this shot was rendered with, or None if it has none.

    104 samples of output log2 luma against input log2 luma, evenly spaced over
    [0, DRO_LOG_CEILING). None means the RAW carries no curve, which for a shot
    whose exif says Auto sends the engine to its built-in level-5 preset instead
    — a different question, and one this cannot answer offline because those
    preset tables live in Edit.exe's .data rather than in the file.

    Rebuilt offline, this matched the engine's own buffer on all 16 frames
    checked, to within float32 noise (max 3e-6). See sony_repro/PIPELINE.md
    section 7.10.2 and tools/vatr_verify.py.

    The result is read-only: it is shared out of a cache.
    """
    try:
        st = Path(path).stat()
    except OSError:
        return None
    return _cached_dro_curve(str(path), st.st_size, int(st.st_mtime_ns))


def _read_dro_curve(path: str | Path) -> np.ndarray | None:
    try:
        dec, sub_pos, endian = _decrypted_sr2(path)
    except (KeyError, OSError, struct.error, IndexError, ValueError):
        # A file that is not a Sony RAW can fail anywhere in the walk, not just
        # at the missing-tag check: a truncated IFD runs the entry loop off the
        # end of the buffer. Callers asked what DRO did, and the answer for such
        # a file is "nothing here to read", not an exception.
        return None

    top = partial(_top_array, dec, sub_pos, endian, fmt="H", dtype=np.float64)
    knots = top(DRO_KNOTS_TAG, want=10)
    ctrl = top(DRO_CTRL_TAG, want=3 * DRO_SEGMENTS + 1)
    # All-zero knots are the engine's own "no curve here" flag (calib+0x10f0 is
    # set from exactly this test), not a curve that happens to be flat.
    if knots is None or ctrl is None or not knots.any():
        return None
    knots /= _DRO_UNIT
    ctrl /= _DRO_UNIT
    return sample_dro_curve(knots, ctrl)


def sample_dro_curve(knots: np.ndarray, ctrl: np.ndarray) -> np.ndarray:
    """The engine's piecewise cubic Bezier, sampled the way the engine samples it.

    Shared with the built-in level presets, which are the same nine segments in
    the same units — only their source differs (Edit.exe's own table rather than
    this file's tags), so the sampling must not diverge between the two.
    """
    step = DRO_LOG_CEILING / DRO_CURVE_POINTS
    out = np.empty(DRO_CURVE_POINTS, dtype=np.float64)
    for i in range(DRO_CURVE_POINTS):
        xx = i * step
        # The engine walks the segments in order and takes the first that ends
        # past xx. Its fallback indexes one segment past the last, which is out
        # of bounds; the top sample sits below the final knot on every frame
        # measured, so clamping here changes nothing that has been observed.
        j = next((t for t in range(DRO_SEGMENTS) if xx < knots[t + 1]), DRO_SEGMENTS - 1)
        width = knots[j + 1] - knots[j]
        u = 0.0 if width == 0 else (xx - knots[j]) / width
        b = 3 * j
        out[i] = min(
            (1 - u) ** 3 * ctrl[b] + 3 * (1 - u) ** 2 * u * ctrl[b + 1]
            + 3 * u**2 * (1 - u) * ctrl[b + 2] + u**3 * ctrl[b + 3],
            DRO_LOG_CEILING,
        )
    return out


def dro_strength(path: str | Path) -> float | None:
    """How far this shot's DRO curve departs from identity, in stops.

    None when the RAW carries no curve. 0.0 means the camera chose Auto and then
    graded the shot at level zero — the stage runs and does nothing. Measured
    across 22 Auto frames this ranged 0.000 to 0.727, and three of them were
    exactly zero, so "exif says Auto" is not the same question.
    """
    curve = dro_curve(path)
    if curve is None:
        return None
    x = np.arange(DRO_CURVE_POINTS) * (DRO_LOG_CEILING / DRO_CURVE_POINTS)
    return float(np.abs(curve - x).max())


def look_calibrations(path: str | Path) -> list[LookCalibration]:
    """Every Creative Look's calibration, in Sony's own order.

    The body writes ten parallel SR2DataIFDs — one per look — into every frame,
    not just the one that was selected when shooting. Tag 0x74c0 of the
    SR2SubIFD is a uint32[10] of their offsets. So a shot taken on Film still
    carries what Vivid 2 would have looked like, and switching looks needs no
    extra capture.
    """
    dec, sub_pos, endian = _decrypted_sr2(path)
    e = _find_tag(dec, sub_pos, endian, LOOK_INDEX_TAG)
    if e is None:
        raise KeyError(f"SR2SubIFD has no tag 0x{LOOK_INDEX_TAG:04x} (look index)")
    _, count, vpos, _ = e
    offsets = struct.unpack_from(f"{endian}{count}I", dec, vpos)

    # The matrix comes from the SR2SubIFD itself, shared by every look — see
    # LookCalibration. The per-look 0x780f is deliberately ignored.
    shared = _find_tag(dec, sub_pos, endian, SR2_PARAM_TAG)
    if shared is None:
        raise KeyError(f"SR2SubIFD has no tag 0x{SR2_PARAM_TAG:04x} (colour matrix)")
    _, _, mpos, msize = shared
    param_block = dec[mpos:mpos + msize]

    _top_shorts = partial(_top_array, dec, sub_pos, endian, fmt="h", dtype=np.int64)

    # The illuminant weights and the as-shot look's finished parameters are both
    # top-level: they describe the frame, not a look.
    weights = _top_shorts(CHROMA_WEIGHT_TAG)
    if weights is None or weights.size != 4:
        weights = np.array([1024, 0, 0, 0], dtype=np.int64)
    final = _top_shorts(CHROMA_FINAL_TAG)
    shot_base = _top_shorts(CHROMA_BASE_TAG)

    # A file with no Fade tables renders as if Fade were 0, which is a pivot of
    # zero and a contrast of one — not "no YGamma", since YGamma still runs.
    pivots = _top_shorts(LUMA_PIVOT_TAG)
    if pivots is None or pivots.size != FADE_STEPS:
        pivots = np.zeros(FADE_STEPS, dtype=np.int64)
    contrasts = _top_shorts(LUMA_CONTRAST_TAG)
    if contrasts is None or contrasts.size != FADE_STEPS:
        contrasts = np.full(FADE_STEPS, LUMA_CONTRAST_UNIT, dtype=np.int64)

    out = []
    for pos in offsets:
        got = {}
        for tag, _typ, cnt, tag_pos, size in _ifd_entries(dec, pos, endian):
            if tag in (CURVE_X_TAG, CURVE_Y_TAG):
                got[tag] = np.array(struct.unpack_from(f"{endian}{cnt}i", dec, tag_pos), dtype=np.int64)
            elif tag in _CHROMA_TAGS:
                got[tag] = np.array(struct.unpack_from(f"{endian}{cnt}h", dec, tag_pos), dtype=np.int64)
            elif tag == LOOK_NAME_TAG:
                got[tag] = dec[tag_pos:tag_pos + size]
            elif tag in (LUMA_LUT_C_TAG, LUMA_LUT_D_TAG) and cnt >= 1:
                got[tag] = int(struct.unpack_from(endian + "H", dec, tag_pos)[0])
        missing = {CURVE_X_TAG, CURVE_Y_TAG, CHROMA_BASE_TAG} - set(got)
        if missing:
            raise KeyError(f"SR2DataIFD at 0x{pos:x} is missing {sorted(hex(t) for t in missing)}")
        zero = np.zeros(8, dtype=np.int64)
        name = bytes(got.get(LOOK_NAME_TAG, b"")).split(b"\x00")[0].decode("ascii", "replace")
        # A file writing only one of the two is not something any frame has
        # shown; treating the pair as all-or-nothing keeps a half-read pair from
        # selecting a table nobody measured.
        lut_key = ((got[LUMA_LUT_C_TAG], got[LUMA_LUT_D_TAG])
                   if LUMA_LUT_C_TAG in got and LUMA_LUT_D_TAG in got
                   else luma_lut_key_for(name))
        out.append(LookCalibration(
            name=name,
            param_block=param_block,
            curve_x=got[CURVE_X_TAG],
            curve_y=got[CURVE_Y_TAG],
            chroma_base=got[CHROMA_BASE_TAG],
            chroma_deltas=np.stack([got.get(t, zero) for t in CHROMA_ILLUMINANT_TAGS]),
            chroma_weights=weights,
            luma_pivot=pivots,
            luma_contrast=contrasts,
            # Matching on the base rather than the name: the name is what the
            # top-level block claims, the base is what it *is*.
            chroma_final=final if (
                final is not None and final.size == 8 and shot_base is not None
                and np.array_equal(shot_base, got[CHROMA_BASE_TAG])
            ) else None,
            luma_lut_key=lut_key,
        ))
    return out


def _decode14(x: np.ndarray) -> np.ndarray:
    """14-bit fixed point -> integer values.

    Bit 13 is the sign. Negatives are encoded as `-((~x | 1) & 0x3fff)`, which is
    *not* two's complement — this mirrors the engine's and/not/and sequence
    literally. Do not "fix" it to `~x + 1`.
    """
    mag = np.where(x >> 13 & 1, -(((~x) | 1) & 0x3FFF).astype(np.int32), (x & 0x3FFF).astype(np.int32))
    return mag


def unpack_param_block(block: bytes) -> np.ndarray:
    """276-byte bitstream -> (6, 16) knot coefficients.

    The block is 69 little-endian uint32. `d[6+i]` packs two coefficients, the
    high 14 bits (>> 0x12) before the low ones (>> 2). The first six dwords are
    knot positions and flags, which the expansion in linear_matrix.py hard-codes.
    """
    if len(block) < PARAM_BLOCK_SIZE:
        raise ValueError(f"parameter block is {len(block)} bytes, need at least {PARAM_BLOCK_SIZE}")
    d = np.frombuffer(block, dtype="<u4", count=69)[6:54].astype(np.uint32)
    raw = np.empty(96, dtype=np.int32)
    raw[0::2] = _decode14(d >> np.uint32(0x12))
    raw[1::2] = _decode14(d >> np.uint32(2))
    return (raw.astype(np.float32) * _Q).reshape(6, 16)


def linear_matrix_coeff(path: str | Path) -> np.ndarray:
    """ARW -> (6, 16) knot coefficients, ready for SegmentedMatrix."""
    return unpack_param_block(read_sr2_tag(path))
