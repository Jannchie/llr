"""XMP packet building, Extended-XMP overflow, and JPEG APP1 insertion."""

import hashlib
import json
import struct
import xml.etree.ElementTree as ET

import pytest

from llr_worker.cli import (
    MAX_APP1_PAYLOAD,
    XMP_EXT_HEADER,
    XMP_STD_HEADER,
    build_extended_xmp,
    build_xmp_segments,
    embed_xmp_app1,
)

RDF_NS = "{http://www.w3.org/1999/02/22-rdf-syntax-ns#}"
LLR_NS = "{http://ns.llr.app/xmp/1.0/}"

SMALL_SETTINGS = {
    "exposure": 0.5,
    "contrast": 12,
    "toneCurve": {"master": [[0, 0], [0.5, 0.6], [1, 1]]},
}


def huge_settings() -> dict:
    # Enough curve points to overflow the 64 KB APP1 limit.
    points = [[i / 40000, i / 40000] for i in range(40000)]
    return {**SMALL_SETTINGS, "toneCurve": {"master": points}}


def settings_from_packet(packet: bytes) -> dict:
    root = ET.fromstring(packet.decode("utf-8"))
    node = root.find(f".//{RDF_NS}Description/{LLR_NS}Settings")
    assert node is not None and node.text
    return json.loads(node.text)


def test_small_edit_fits_one_standard_segment() -> None:
    payloads, xmp_bytes = build_xmp_segments(SMALL_SETTINGS)
    assert len(payloads) == 1
    assert payloads[0].startswith(XMP_STD_HEADER)
    assert len(payloads[0]) <= MAX_APP1_PAYLOAD
    packet = payloads[0][len(XMP_STD_HEADER) :]
    assert len(packet) == xmp_bytes
    assert settings_from_packet(packet) == SMALL_SETTINGS


def test_huge_edit_spills_into_extended_xmp_chunks() -> None:
    settings = huge_settings()
    payloads, _ = build_xmp_segments(settings)
    assert len(payloads) >= 2
    assert all(len(p) <= MAX_APP1_PAYLOAD for p in payloads)
    assert payloads[0].startswith(XMP_STD_HEADER)
    assert all(p.startswith(XMP_EXT_HEADER) for p in payloads[1:])

    extended = build_extended_xmp(settings).encode("utf-8")
    guid = hashlib.md5(extended).hexdigest().upper()

    # The main packet must reference the extension GUID.
    main = payloads[0][len(XMP_STD_HEADER) :].decode("utf-8")
    assert f'xmpNote:HasExtendedXMP="{guid}"' in main

    # Chunks carry (guid, total, offset) headers and reassemble exactly.
    reassembled = bytearray(len(extended))
    for payload in payloads[1:]:
        body = payload[len(XMP_EXT_HEADER) :]
        assert body[:32].decode("ascii") == guid
        total, offset = struct.unpack(">II", body[32:40])
        assert total == len(extended)
        chunk = body[40:]
        reassembled[offset : offset + len(chunk)] = chunk
    assert bytes(reassembled) == extended
    assert settings_from_packet(bytes(reassembled)) == settings


def make_jpeg() -> bytes:
    jfif = b"JFIF\x00\x01\x02\x00\x00\x01\x00\x01\x00\x00"
    app0 = b"\xff\xe0" + struct.pack(">H", len(jfif) + 2) + jfif
    dqt = b"\xff\xdb" + struct.pack(">H", 4) + b"\x00\x00"
    return b"\xff\xd8" + app0 + dqt + b"\xff\xd9"


def test_embed_inserts_after_leading_app_segments() -> None:
    jpeg = make_jpeg()
    payloads, _ = build_xmp_segments(SMALL_SETTINGS)
    out = embed_xmp_app1(jpeg, payloads)

    app0_end = 2 + 2 + struct.unpack(">H", jpeg[4:6])[0]
    assert out[:app0_end] == jpeg[:app0_end]  # SOI + APP0 untouched
    assert out[app0_end : app0_end + 2] == b"\xff\xe1"
    seg_len = struct.unpack(">H", out[app0_end + 2 : app0_end + 4])[0]
    assert out[app0_end + 4 : app0_end + 4 + len(XMP_STD_HEADER)] == XMP_STD_HEADER
    assert out[app0_end + 2 + seg_len :] == jpeg[app0_end:]  # rest shifted, not altered


def test_embed_rejects_non_jpeg() -> None:
    with pytest.raises(ValueError, match="not a JPEG"):
        embed_xmp_app1(b"\x89PNG\r\n", [XMP_STD_HEADER + b"x"])


def test_embed_rejects_oversized_payload() -> None:
    with pytest.raises(ValueError, match="exceeds segment limit"):
        embed_xmp_app1(make_jpeg(), [b"\x00" * 0x10000])
