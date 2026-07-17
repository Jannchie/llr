"""DCP TIFF parsing and the HueSatMap/LookTable sampling math."""

import dataclasses
import struct
from pathlib import Path

import numpy as np
import pytest

from llr_worker.dcp import (
    D50_WHITE_XYZ,
    XYZ_D50_TO_PROPHOTO,
    DcpHueSatMap,
    apply_dcp_profile,
    apply_hsv_table,
    camera_to_xyz_matrix,
    chromatic_adaptation_matrix,
    hue_sat_map_from_tags,
    load_dcp_profile,
    read_tiff_tags,
    sample_hsv_table,
    tone_curve_from_tag,
)

VENDORED_DCP = (
    Path(__file__).resolve().parents[3]
    / "vendor/adobe-camera-profiles/Camera/Sony ILCE-7CM2/Sony ILCE-7CM2 Camera PT.dcp"
)

# ── Minimal DCP/TIFF writer (little- or big-endian, magic 0x4352) ──────────

TYPE_ASCII, TYPE_SHORT, TYPE_LONG, TYPE_FLOAT = 2, 3, 4, 11
TYPE_PACKS = {TYPE_SHORT: "H", TYPE_LONG: "I", TYPE_FLOAT: "f"}
TYPE_SIZES = {TYPE_ASCII: 1, TYPE_SHORT: 2, TYPE_LONG: 4, TYPE_FLOAT: 4}


def build_dcp(entries: list[tuple[int, int, list]], endian: str = "<") -> bytes:
    """Assemble a classic-TIFF container the way Adobe DCP files are laid out."""
    header = (b"II" if endian == "<" else b"MM") + struct.pack(endian + "HI", 0x4352, 8)
    ifd_size = 2 + len(entries) * 12 + 4
    data_area = bytearray()
    ifd = struct.pack(endian + "H", len(entries))
    for tag, field_type, values in sorted(entries):
        if field_type == TYPE_ASCII:
            raw = str(values[0]).encode("utf-8") + b"\x00"
            count = len(raw)
        else:
            count = len(values)
            raw = struct.pack(endian + f"{count}{TYPE_PACKS[field_type]}", *values)
        if len(raw) <= 4:
            value_offset = raw.ljust(4, b"\x00")
        else:
            offset = len(header) + ifd_size + len(data_area)
            value_offset = struct.pack(endian + "I", offset)
            data_area += raw
        ifd += struct.pack(endian + "HHI", tag, field_type, count) + value_offset
    ifd += struct.pack(endian + "I", 0)  # no next IFD
    return header + ifd + bytes(data_area)


IDENTITY9 = [1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0]
FORWARD9 = [0.6, 0.2, 0.2, 0.3, 0.6, 0.1, 0.0, 0.1, 0.7]


def sample_entries() -> list[tuple[int, int, list]]:
    dims = (2, 3, 1)
    table = [0.0, 1.0, 1.0] * (dims[0] * dims[1] * dims[2])
    return [
        (50936, TYPE_ASCII, ["Test Profile"]),
        (50721, TYPE_FLOAT, IDENTITY9),  # ColorMatrix1
        (50964, TYPE_FLOAT, FORWARD9),  # ForwardMatrix1
        (50940, TYPE_FLOAT, [0.0, 0.0, 0.5, 0.6, 1.0, 1.0]),  # ProfileToneCurve
        (50937, TYPE_LONG, list(dims)),  # ProfileHueSatMapDims
        (50938, TYPE_FLOAT, table),  # ProfileHueSatMapData1
        (51107, TYPE_LONG, [1]),  # HueSatMapEncoding = sRGB
    ]


@pytest.mark.parametrize("endian", ["<", ">"])
def test_parse_round_trips_matrices_curve_and_table(endian: str, tmp_path: Path) -> None:
    path = tmp_path / "test.dcp"
    path.write_bytes(build_dcp(sample_entries(), endian))
    profile = load_dcp_profile(path)

    assert profile.name == "Test Profile"
    assert profile.color_matrix_1 is not None
    assert np.allclose(profile.color_matrix_1, np.eye(3))
    assert profile.forward_matrix_1 is not None
    assert np.allclose(profile.forward_matrix_1, np.array(FORWARD9).reshape(3, 3), atol=1e-6)
    assert profile.tone_curve is not None
    assert np.allclose(profile.tone_curve, [[0.0, 0.0], [0.5, 0.6], [1.0, 1.0]], atol=1e-6)
    assert profile.hue_sat_map_1 is not None
    assert profile.hue_sat_map_1.dimensions == (2, 3, 1)
    assert profile.hue_sat_map_encoding == 1
    assert profile.hue_sat_map_2 is None and profile.look_table is None


def test_unknown_tags_are_ignored_and_bad_magic_rejected(tmp_path: Path) -> None:
    data = build_dcp([(50936, TYPE_ASCII, ["X"]), (270, TYPE_ASCII, ["ImageDescription"])])
    assert read_tiff_tags(data) == {"profile_name": "X"}
    with pytest.raises(ValueError, match="byte-order marker"):
        read_tiff_tags(b"XX" + data[2:])
    with pytest.raises(ValueError, match="classic TIFF"):
        read_tiff_tags(b"II" + struct.pack("<HI", 99, 8))


def test_matrix_preference_order() -> None:
    path = Path("/x.dcp")
    base = {
        "path": path,
        "name": None,
        "color_matrix_1": np.eye(3, dtype=np.float32) * 2.0,
        "color_matrix_2": None,
        "forward_matrix_1": None,
        "forward_matrix_2": None,
        "tone_curve": None,
        "hue_sat_map_1": None,
        "hue_sat_map_2": None,
        "hue_sat_map_3": None,
        "hue_sat_map_encoding": 0,
        "look_table": None,
        "look_table_encoding": 0,
    }
    from llr_worker.dcp import DcpProfile

    name, matrix = camera_to_xyz_matrix(DcpProfile(**base))
    assert name == "inverse(ColorMatrix1)"
    # inverse() of a grey-scaled ColorMatrix puts the neutral on white E, so the
    # fallback's adaptation to D50 is what the matrix carries beyond the inverse.
    assert np.allclose(matrix, chromatic_adaptation_matrix(np.full(3, 0.5), D50_WHITE_XYZ) * 0.5)

    fwd = np.array(FORWARD9, dtype=np.float32).reshape(3, 3)
    name, matrix = camera_to_xyz_matrix(DcpProfile(**{**base, "forward_matrix_1": fwd}))
    assert name == "ForwardMatrix1"
    assert np.array_equal(matrix, fwd)

    with pytest.raises(ValueError, match="usable DCP color matrix"):
        camera_to_xyz_matrix(DcpProfile(**{**base, "color_matrix_1": None}))


@pytest.mark.skipif(not VENDORED_DCP.exists(), reason="vendored camera profiles are not checked out")
def test_color_matrix_fallback_keeps_a_neutral_neutral() -> None:
    """Every vendored profile carries a ForwardMatrix, so only a user-supplied
    profile dropped into vendor/adobe-camera-profiles/Camera/<model>/ reaches the
    fallback — which is exactly why it needs pinning here."""
    profile = load_dcp_profile(VENDORED_DCP)
    assert profile.forward_matrix_1 is not None and profile.color_matrix_1 is not None
    color_matrix_only = dataclasses.replace(profile, forward_matrix_1=None, forward_matrix_2=None)

    neutral = np.full((1, 1, 3), 0.5, dtype=np.float32)
    forward, _ = apply_dcp_profile(neutral, profile)
    fallback, info = apply_dcp_profile(neutral, color_matrix_only)
    assert info.matrix == "inverse(ColorMatrix1)"

    # inverse(ColorMatrix1) without the D50 adaptation lands on the calibration
    # illuminant's white: R/G ~ 2.25 and B/G ~ 2.0, a heavy magenta cast.
    unadapted = neutral @ np.linalg.inv(profile.color_matrix_1).T @ XYZ_D50_TO_PROPHOTO.T
    assert unadapted[0, 0, 0] / unadapted[0, 0, 1] > 2.0
    assert unadapted[0, 0, 2] / unadapted[0, 0, 1] > 1.9

    assert np.allclose(fallback, 0.5, atol=1e-4)
    # Both paths must land in the same space, or preview colour would depend on
    # which matrix a profile happens to ship.
    assert np.allclose(fallback, forward, atol=1e-3)


def test_chromatic_adaptation_maps_source_white_onto_target() -> None:
    matrix = chromatic_adaptation_matrix(np.array([0.9, 1.0, 1.2]), D50_WHITE_XYZ)
    assert np.allclose(matrix @ np.array([0.9, 1.0, 1.2]), D50_WHITE_XYZ, atol=1e-5)
    assert np.allclose(chromatic_adaptation_matrix(D50_WHITE_XYZ, D50_WHITE_XYZ), np.eye(3), atol=1e-5)
    # D50 is the working space's own white: it must be exactly grey in ProPhoto.
    assert np.allclose(D50_WHITE_XYZ @ XYZ_D50_TO_PROPHOTO.T, 1.0, atol=1e-6)


def test_tone_curve_is_sorted_and_padded_to_endpoints() -> None:
    curve = tone_curve_from_tag([0.5, 0.6, 0.25, 0.3])
    assert curve is not None
    assert np.allclose(curve, [[0.0, 0.0], [0.25, 0.3], [0.5, 0.6], [1.0, 1.0]], atol=1e-6)
    assert tone_curve_from_tag([0.1, 0.2, 0.3]) is None  # odd count
    assert tone_curve_from_tag(None) is None


def test_hue_sat_map_accepts_skipped_zero_sat_layout() -> None:
    dims = [2, 3, 1]
    # Full layout: hue*sat*val*3 = 18 floats.
    full = hue_sat_map_from_tags(dims, [10.0, 0.5, 0.8] * 6)
    assert full is not None and full.data.shape == (1, 2, 3, 3)
    # sat==0 value scale is forced to identity.
    assert full.data[0, 0, 0, 2] == 1.0

    # Skipped layout omits the sat==0 column (hue*(sat-1)*val*3 = 12 floats).
    skipped = hue_sat_map_from_tags(dims, [10.0, 0.5, 0.8] * 4)
    assert skipped is not None
    assert skipped.data[0, 0, 0].tolist() == [10.0, 0.5, 1.0]  # copied hue/sat, identity value

    assert hue_sat_map_from_tags(dims, [1.0] * 7) is None  # wrong size
    assert hue_sat_map_from_tags([2, 1, 1], [1.0] * 6) is None  # sat_count < 2


def identity_table(hue: int = 6, sat: int = 4, val: int = 1) -> DcpHueSatMap:
    data = np.zeros((val, hue, sat, 3), dtype=np.float32)
    data[..., 1] = 1.0
    data[..., 2] = 1.0
    return DcpHueSatMap(dimensions=(hue, sat, val), data=data)


def test_identity_table_is_a_noop_and_keeps_headroom() -> None:
    rgb = np.array([[0.2, 0.4, 0.8], [1.0, 0.0, 0.0]], dtype=np.float32)
    out = apply_hsv_table(rgb, identity_table(), encoding=0)
    assert np.allclose(out, rgb, atol=1e-5)

    # Headroom restore scales the whole clamped pixel back up (hue/sat kept),
    # so a >1 highlight keeps its peak and its chroma ratios.
    bright = np.array([[2.5, 0.1, 0.1]], dtype=np.float32)
    out = apply_hsv_table(bright, identity_table(), encoding=0)
    assert out[0, 0] == pytest.approx(2.5, abs=1e-4)
    assert np.allclose(out[0], [2.5, 0.25, 0.25], atol=1e-4)


def test_sample_hsv_table_interpolates_and_wraps_hue() -> None:
    hue, sat, val = 4, 2, 1
    data = np.zeros((val, hue, sat, 3), dtype=np.float32)
    data[..., 1] = 1.0
    data[..., 2] = 1.0
    # Hue shifts of 0/10/20/30 degrees at hue bins 0..3, same for both sat rows.
    data[0, :, :, 0] = np.array([0.0, 10.0, 20.0, 30.0])[:, None]
    table = DcpHueSatMap(dimensions=(hue, sat, val), data=data)

    def shift_at(h: float) -> float:
        out = sample_hsv_table(
            np.array([h], dtype=np.float32),
            np.array([1.0], dtype=np.float32),
            np.array([0.5], dtype=np.float32),
            table,
        )
        return float(out[0, 0])

    assert shift_at(0.0) == pytest.approx(0.0)
    assert shift_at(0.25) == pytest.approx(10.0)
    assert shift_at(0.125) == pytest.approx(5.0)  # midway between bins 0 and 1
    assert shift_at(0.875) == pytest.approx(15.0)  # wraps: midway between 30 and 0
