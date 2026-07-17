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

IDENTITY_CURVE = [{"x": 0, "y": 0}, {"x": 1, "y": 1}]


def default_settings() -> dict:
    """The app's captureSnapshot() (App.vue) with every value at its default."""
    return {
        "recipe": {
            "exposure": 0, "contrast": 0, "highlights": 0, "shadows": 0,
            "whites": 0, "blacks": 0, "vibrance": 0, "saturation": 0,
            "temperature": 6500, "tint": 0, "clarity": 0, "dehaze": 0,
        },
        "hslHue": [0] * 8,
        "hslSat": [0] * 8,
        "hslLum": [0] * 8,
        "grading": {
            "shH": 0, "shS": 0, "mdH": 0, "mdS": 0, "hlH": 0, "hlS": 0,
            "blend": 50, "balance": 0,
        },
        "curve": {
            "rgb": [dict(p) for p in IDENTITY_CURVE],
            "red": [dict(p) for p in IDENTITY_CURVE],
            "green": [dict(p) for p in IDENTITY_CURVE],
            "blue": [dict(p) for p in IDENTITY_CURVE],
            "parametric": {
                "highlights": 0, "lights": 0, "darks": 0, "shadows": 0,
                "shadowSplit": 25, "midtoneSplit": 50, "highlightSplit": 75,
            },
        },
        "crop": {
            "cx": 0.5, "cy": 0.5, "w": 1, "h": 1, "angle": 0,
            "flipH": False, "flipV": False, "orientation": 0,
        },
        "aspect": "free",
        "dcp": "",
        "denoise": {"enabled": False, "model": "wavelet", "amount": 100},
    }


def edited_settings() -> dict:
    """A snapshot with every family of llr:* attribute off its default.

    Grading hues are in the UI's units (degrees, -180..180); the packet carries
    them normalized to 0..360.
    """
    settings = default_settings()
    settings["recipe"] |= {
        "exposure": 0.5, "contrast": 12, "highlights": -30, "shadows": 25,
        "whites": 10, "blacks": -5, "vibrance": 15, "saturation": -10,
        "temperature": 5200, "tint": -8, "clarity": 20, "dehaze": 5,
    }
    settings["hslHue"] = [5, 0, -10, 0, 0, 0, 0, 0]
    settings["hslSat"] = [0, 20, 0, 0, 0, 0, 0, -30]
    settings["grading"] |= {
        "shH": -60, "shS": 20, "mdH": 180, "mdS": 10, "hlH": 45, "hlS": 30,
        "blend": 60, "balance": -20,
    }
    settings["curve"]["parametric"] |= {
        "shadows": -15, "darks": 5, "lights": 10, "highlights": 20,
    }
    settings["curve"]["rgb"] = [
        {"x": 0, "y": 0}, {"x": 0.25, "y": 0.2}, {"x": 0.75, "y": 0.8}, {"x": 1, "y": 1},
    ]
    settings["curve"]["blue"] = [{"x": 0, "y": 0}, {"x": 0.5, "y": 0.55}, {"x": 1, "y": 1}]
    return settings


SMALL_SETTINGS = edited_settings()


def huge_settings() -> dict:
    # Enough curve points to overflow the 64 KB APP1 limit.
    settings = edited_settings()
    settings["curve"]["rgb"] = [
        {"x": i / 6000, "y": (i / 6000) ** 0.9} for i in range(6001)
    ]
    return settings


def standard_packet(settings: dict) -> bytes:
    payloads, _ = build_xmp_segments(settings)
    return payloads[0][len(XMP_STD_HEADER) :]


def description(packet: bytes) -> ET.Element:
    root = ET.fromstring(packet.decode("utf-8"))
    node = root.find(f".//{RDF_NS}Description")
    assert node is not None
    return node


def settings_from_packet(packet: bytes) -> dict:
    node = description(packet).find(f"{LLR_NS}Settings")
    assert node is not None and node.text
    return json.loads(node.text)


def curve_seq(packet: bytes, tag: str) -> list[str] | None:
    node = description(packet).find(f"{LLR_NS}{tag}/{RDF_NS}Seq")
    if node is None:
        return None
    return [li.text or "" for li in node.findall(f"{RDF_NS}li")]


def test_small_edit_fits_one_standard_segment() -> None:
    payloads, xmp_bytes = build_xmp_segments(SMALL_SETTINGS)
    assert len(payloads) == 1
    assert payloads[0].startswith(XMP_STD_HEADER)
    assert len(payloads[0]) <= MAX_APP1_PAYLOAD
    packet = payloads[0][len(XMP_STD_HEADER) :]
    assert len(packet) == xmp_bytes
    assert settings_from_packet(packet) == SMALL_SETTINGS


def test_recipe_attrs_carry_the_basic_panel() -> None:
    attrs = description(standard_packet(SMALL_SETTINGS)).attrib
    assert attrs[f"{LLR_NS}Version"] == "1"
    assert attrs[f"{LLR_NS}Exposure"] == "+0.50"
    assert attrs[f"{LLR_NS}Contrast"] == "12"
    assert attrs[f"{LLR_NS}Highlights"] == "-30"
    assert attrs[f"{LLR_NS}Temperature"] == "5200"
    assert attrs[f"{LLR_NS}Tint"] == "-8"
    assert attrs[f"{LLR_NS}HslHue"] == "5,0,-10,0,0,0,0,0"
    assert attrs[f"{LLR_NS}HslSaturation"] == "0,20,0,0,0,0,0,-30"


def test_defaults_round_trip_as_defaults() -> None:
    attrs = description(standard_packet(default_settings())).attrib
    assert attrs[f"{LLR_NS}Exposure"] == "+0.00"
    assert attrs[f"{LLR_NS}Temperature"] == "6500"
    assert attrs[f"{LLR_NS}ParametricShadowSplit"] == "25"
    assert attrs[f"{LLR_NS}GradingBlend"] == "50"


def test_parametric_attrs_come_from_the_curve_block() -> None:
    attrs = description(standard_packet(SMALL_SETTINGS)).attrib
    assert attrs[f"{LLR_NS}ParametricShadows"] == "-15"
    assert attrs[f"{LLR_NS}ParametricDarks"] == "5"
    assert attrs[f"{LLR_NS}ParametricLights"] == "10"
    assert attrs[f"{LLR_NS}ParametricHighlights"] == "20"
    assert attrs[f"{LLR_NS}ParametricHighlightSplit"] == "75"


def test_grading_hues_are_normalized_to_a_full_turn() -> None:
    attrs = description(standard_packet(SMALL_SETTINGS)).attrib
    assert attrs[f"{LLR_NS}GradingShadowHue"] == "300"  # -60° in the UI
    assert attrs[f"{LLR_NS}GradingShadowSaturation"] == "20"
    assert attrs[f"{LLR_NS}GradingMidtoneHue"] == "180"
    assert attrs[f"{LLR_NS}GradingHighlightHue"] == "45"
    assert attrs[f"{LLR_NS}GradingBlend"] == "60"
    assert attrs[f"{LLR_NS}GradingBalance"] == "-20"


def test_dcp_and_denoise_are_written_only_when_set() -> None:
    attrs = description(standard_packet(SMALL_SETTINGS)).attrib
    assert f"{LLR_NS}Dcp" not in attrs
    assert f"{LLR_NS}DenoiseModel" not in attrs

    settings = edited_settings()
    settings["dcp"] = "canon_eos_r5"
    settings["denoise"] = {"enabled": True, "model": "wavelet", "amount": 60}
    attrs = description(standard_packet(settings)).attrib
    assert attrs[f"{LLR_NS}Dcp"] == "canon_eos_r5"
    assert attrs[f"{LLR_NS}DenoiseModel"] == "wavelet"
    assert attrs[f"{LLR_NS}DenoiseAmount"] == "60"


def test_identity_crop_writes_no_crop_attrs() -> None:
    attrs = description(standard_packet(SMALL_SETTINGS)).attrib
    assert f"{LLR_NS}HasCrop" not in attrs
    assert not [key for key in attrs if key.startswith(f"{LLR_NS}Crop")]


def test_recomposed_crop_writes_the_recompose_model() -> None:
    settings = edited_settings()
    settings["crop"] |= {
        "cx": 0.4, "cy": 0.55, "w": 0.5, "h": 0.75, "angle": -3.5,
        "flipH": True, "orientation": 90,
    }
    attrs = description(standard_packet(settings)).attrib
    assert attrs[f"{LLR_NS}HasCrop"] == "True"
    assert attrs[f"{LLR_NS}CropCenterX"] == "0.400000"
    assert attrs[f"{LLR_NS}CropCenterY"] == "0.550000"
    assert attrs[f"{LLR_NS}CropWidth"] == "0.500000"
    assert attrs[f"{LLR_NS}CropHeight"] == "0.750000"
    assert attrs[f"{LLR_NS}CropAngle"] == "-3.5000"
    assert attrs[f"{LLR_NS}CropFlipH"] == "True"
    assert f"{LLR_NS}CropFlipV" not in attrs
    assert attrs[f"{LLR_NS}CropOrientation"] == "90"


def test_tone_curves_emit_one_seq_per_edited_channel() -> None:
    packet = standard_packet(SMALL_SETTINGS)
    assert curve_seq(packet, "ToneCurveRgb") == [
        "0.0000, 0.0000", "0.2500, 0.2000", "0.7500, 0.8000", "1.0000, 1.0000",
    ]
    assert curve_seq(packet, "ToneCurveBlue") == [
        "0.0000, 0.0000", "0.5000, 0.5500", "1.0000, 1.0000",
    ]
    # Identity channels stay out of the packet.
    assert curve_seq(packet, "ToneCurveRed") is None
    assert curve_seq(packet, "ToneCurveGreen") is None


def test_identity_curve_emits_no_seq_at_all() -> None:
    packet = standard_packet(default_settings())
    for tag in ("ToneCurveRgb", "ToneCurveRed", "ToneCurveGreen", "ToneCurveBlue"):
        assert curve_seq(packet, tag) is None


def test_tone_curve_points_are_clamped_and_malformed_points_dropped() -> None:
    settings = edited_settings()
    settings["curve"]["red"] = [
        {"x": -0.2, "y": 0}, {"x": 0.5, "y": 1.4}, {"x": 0.6}, {"x": 1, "y": 1},
    ]
    assert curve_seq(standard_packet(settings), "ToneCurveRed") == [
        "0.0000, 0.0000", "0.5000, 1.0000", "1.0000, 1.0000",
    ]


def test_huge_edit_spills_into_extended_xmp_chunks() -> None:
    settings = huge_settings()
    payloads, _ = build_xmp_segments(settings)
    assert len(payloads) >= 2
    assert all(len(p) <= MAX_APP1_PAYLOAD for p in payloads)
    assert payloads[0].startswith(XMP_STD_HEADER)
    assert all(p.startswith(XMP_EXT_HEADER) for p in payloads[1:])

    extended = build_extended_xmp(settings).encode("utf-8")
    guid = hashlib.md5(extended).hexdigest().upper()

    # The main packet must reference the extension GUID, and still carry the
    # llr:* attributes even though the JSON blob moved out.
    main = payloads[0][len(XMP_STD_HEADER) :]
    assert f'xmpNote:HasExtendedXMP="{guid}"' in main.decode("utf-8")
    assert description(main).attrib[f"{LLR_NS}Exposure"] == "+0.50"
    # A 6001-point curve inflates the Seq blocks past the segment limit too, so
    # they are dropped; llr:Settings in the extended packet still has them.
    assert curve_seq(main, "ToneCurveRgb") is None

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
