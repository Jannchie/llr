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
from dataclasses import dataclass
from pathlib import Path

import numpy as np

SR2_PARAM_TAG = 0x780F
PARAM_BLOCK_SIZE = 276
_Q = np.float32(1.0 / 1024.0)  # the fixed-point unit, hard-coded at Edit.exe +0x4DEB78

LOOK_INDEX_TAG = 0x74C0  # uint32[10]: offsets of the per-look SR2DataIFDs
CURVE_X_TAG = 0x7805     # tone curve control points, x (not evenly spaced)
CURVE_Y_TAG = 0x7806     # ...and y
LOOK_NAME_TAG = 0x7770   # the look's own name, e.g. "Standard" / "FL" / "VV2"

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
    param_block: bytes      # 276 bytes -> LinearMatrix16 knot coefficients
    curve_x: np.ndarray     # 128 control points, in units of LUT index * 128
    curve_y: np.ndarray     # ...and their outputs, in units of full scale / 16384


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

    out = []
    for pos in offsets:
        got = {}
        for tag, _typ, cnt, tag_pos, size in _ifd_entries(dec, pos, endian):
            if tag in (CURVE_X_TAG, CURVE_Y_TAG):
                got[tag] = np.array(struct.unpack_from(f"{endian}{cnt}i", dec, tag_pos), dtype=np.int64)
            elif tag == LOOK_NAME_TAG:
                got[tag] = dec[tag_pos:tag_pos + size]
        missing = {CURVE_X_TAG, CURVE_Y_TAG} - set(got)
        if missing:
            raise KeyError(f"SR2DataIFD at 0x{pos:x} is missing {sorted(hex(t) for t in missing)}")
        out.append(LookCalibration(
            name=bytes(got.get(LOOK_NAME_TAG, b"")).split(b"\x00")[0].decode("ascii", "replace"),
            param_block=param_block,
            curve_x=got[CURVE_X_TAG],
            curve_y=got[CURVE_Y_TAG],
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
