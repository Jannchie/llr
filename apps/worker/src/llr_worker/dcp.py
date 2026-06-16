from __future__ import annotations

import struct
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np


DCP_TAGS = {
    50721: "color_matrix_1",
    50722: "color_matrix_2",
    50937: "hue_sat_map_dims",
    50938: "hue_sat_map_data_1",
    50939: "hue_sat_map_data_2",
    50936: "profile_name",
    50940: "profile_tone_curve",
    50964: "forward_matrix_1",
    50965: "forward_matrix_2",
    50981: "look_table_dims",
    50982: "look_table_data",
    51107: "hue_sat_map_encoding",
    51108: "look_table_encoding",
    52537: "hue_sat_map_data_3",
}

TIFF_TYPE_SIZES = {
    1: 1,
    2: 1,
    3: 2,
    4: 4,
    5: 8,
    7: 1,
    8: 2,
    9: 4,
    10: 8,
    11: 4,
    12: 8,
}

D50_TO_D65 = np.array(
    [
        [0.9555766, -0.0230393, 0.0631636],
        [-0.0282895, 1.0099416, 0.0210077],
        [0.0122982, -0.0204830, 1.3299098],
    ],
    dtype=np.float32,
)

XYZ_D65_TO_SRGB = np.array(
    [
        [3.2404542, -1.5371385, -0.4985314],
        [-0.9692660, 1.8760108, 0.0415560],
        [0.0556434, -0.2040259, 1.0572252],
    ],
    dtype=np.float32,
)

PROPHOTO_TO_XYZ_D50 = np.array(
    [
        [0.7976749, 0.1351917, 0.0313534],
        [0.2880402, 0.7118741, 0.0000857],
        [0.0000000, 0.0000000, 0.8252100],
    ],
    dtype=np.float32,
)

XYZ_D50_TO_PROPHOTO = np.linalg.inv(PROPHOTO_TO_XYZ_D50).astype(np.float32)

ENCODING_LINEAR = 0
ENCODING_SRGB = 1
HSV_TABLE_CHUNK_PIXELS = 500_000


@dataclass(frozen=True)
class DcpHueSatMap:
    dimensions: tuple[int, int, int]
    data: np.ndarray


@dataclass(frozen=True)
class DcpProfile:
    path: Path
    name: str | None
    color_matrix_1: np.ndarray | None
    color_matrix_2: np.ndarray | None
    forward_matrix_1: np.ndarray | None
    forward_matrix_2: np.ndarray | None
    tone_curve: np.ndarray | None
    hue_sat_map_1: DcpHueSatMap | None
    hue_sat_map_2: DcpHueSatMap | None
    hue_sat_map_3: DcpHueSatMap | None
    hue_sat_map_encoding: int
    look_table: DcpHueSatMap | None
    look_table_encoding: int


@dataclass(frozen=True)
class DcpRenderInfo:
    path: str
    name: str | None
    matrix: str
    tone_curve_samples: int
    hue_sat_map: dict[str, Any] | None
    look_table: dict[str, Any] | None
    limitations: list[str]
    working_space: str = "linear-prophoto-d50"
    profile_tone_curve: list[list[float]] | None = None

    def to_json(self) -> dict[str, Any]:
        return {
            "kind": "dcp",
            "path": self.path,
            "name": self.name,
            "matrix": self.matrix,
            "toneCurveSamples": self.tone_curve_samples,
            "profileHueSatMap": self.hue_sat_map,
            "profileLookTable": self.look_table,
            "limitations": self.limitations,
            "workingSpace": self.working_space,
            "profileToneCurve": self.profile_tone_curve,
        }


# Cache loaded DCP profiles keyed by (path, size, mtime) so switching
# DCP codes doesn't re-parse the TIFF structure every time.
_DCP_PROFILE_CACHE: OrderedDict[tuple[Any, ...], DcpProfile] = OrderedDict()
_DCP_PROFILE_CACHE_MAX = 16


def _dcp_cache_key(path: Path) -> tuple[Any, ...]:
    try:
        st = path.stat()
        return (str(path.resolve()), st.st_size, int(st.st_mtime_ns))
    except OSError:
        return (str(path.resolve()),)


def load_dcp_profile(path: Path) -> DcpProfile:
    key = _dcp_cache_key(path)
    if key in _DCP_PROFILE_CACHE:
        _DCP_PROFILE_CACHE.move_to_end(key)
        return _DCP_PROFILE_CACHE[key]

    profile = _parse_dcp_file(path)
    _DCP_PROFILE_CACHE[key] = profile
    while len(_DCP_PROFILE_CACHE) > _DCP_PROFILE_CACHE_MAX:
        _DCP_PROFILE_CACHE.popitem(last=False)
    return profile


def _parse_dcp_file(path: Path) -> DcpProfile:
    tags = read_tiff_tags(path.read_bytes())
    color_matrix_1 = matrix_from_tag(tags.get("color_matrix_1"), rows=None)
    color_matrix_2 = matrix_from_tag(tags.get("color_matrix_2"), rows=None)
    forward_matrix_1 = matrix_from_tag(tags.get("forward_matrix_1"), rows=3)
    forward_matrix_2 = matrix_from_tag(tags.get("forward_matrix_2"), rows=3)
    tone_curve = tone_curve_from_tag(tags.get("profile_tone_curve"))
    hue_sat_map_1 = hue_sat_map_from_tags(tags.get("hue_sat_map_dims"), tags.get("hue_sat_map_data_1"))
    hue_sat_map_2 = hue_sat_map_from_tags(tags.get("hue_sat_map_dims"), tags.get("hue_sat_map_data_2"))
    hue_sat_map_3 = hue_sat_map_from_tags(tags.get("hue_sat_map_dims"), tags.get("hue_sat_map_data_3"))
    look_table = hue_sat_map_from_tags(tags.get("look_table_dims"), tags.get("look_table_data"))
    return DcpProfile(
        path=path,
        name=string_from_tag(tags.get("profile_name")),
        color_matrix_1=color_matrix_1,
        color_matrix_2=color_matrix_2,
        forward_matrix_1=forward_matrix_1,
        forward_matrix_2=forward_matrix_2,
        tone_curve=tone_curve,
        hue_sat_map_1=hue_sat_map_1,
        hue_sat_map_2=hue_sat_map_2,
        hue_sat_map_3=hue_sat_map_3,
        hue_sat_map_encoding=encoding_from_tag(tags.get("hue_sat_map_encoding")),
        look_table=look_table,
        look_table_encoding=encoding_from_tag(tags.get("look_table_encoding")),
    )


def apply_dcp_profile(camera_rgb: np.ndarray, profile: DcpProfile) -> tuple[np.ndarray, DcpRenderInfo]:
    matrix_name, camera_to_xyz = camera_to_xyz_matrix(profile)
    xyz_d50 = camera_rgb @ camera_to_xyz.T
    linear_prophoto = np.clip(xyz_d50 @ XYZ_D50_TO_PROPHOTO.T, 0, None)

    hue_sat_map = select_hue_sat_map(profile)
    hue_sat_map_info: dict[str, Any] | None = None
    look_table_info: dict[str, Any] | None = None
    limitations: list[str] = []

    if hue_sat_map is not None:
        linear_prophoto = apply_hsv_table(linear_prophoto, hue_sat_map, profile.hue_sat_map_encoding)
        hue_sat_map_info = table_info(hue_sat_map, profile.hue_sat_map_encoding)
        if profile.hue_sat_map_2 is not None or profile.hue_sat_map_3 is not None:
            limitations.append("Only ProfileHueSatMapData1 is applied; white-balance interpolation is not implemented yet.")

    if profile.look_table is not None:
        linear_prophoto = apply_hsv_table(linear_prophoto, profile.look_table, profile.look_table_encoding)
        look_table_info = table_info(profile.look_table, profile.look_table_encoding)

    # Scene-referred pipeline: do NOT bake the profile tone curve here and do NOT
    # convert to display sRGB. Deliver linear ProPhoto (D50) so the browser edits
    # in a wide-gamut scene-linear space and applies its own view transform. The
    # profile tone curve is passed through for the front end to consume as part of
    # the view transform if desired.
    tone_curve_pts = (
        [[float(x), float(y)] for x, y in profile.tone_curve.tolist()]
        if profile.tone_curve is not None
        else None
    )

    return linear_prophoto, DcpRenderInfo(
        path=str(profile.path),
        name=profile.name,
        matrix=matrix_name,
        tone_curve_samples=0 if profile.tone_curve is None else int(profile.tone_curve.shape[0]),
        hue_sat_map=hue_sat_map_info,
        look_table=look_table_info,
        limitations=limitations,
        profile_tone_curve=tone_curve_pts,
    )


def camera_to_xyz_matrix(profile: DcpProfile) -> tuple[str, np.ndarray]:
    if profile.forward_matrix_1 is not None:
        return "ForwardMatrix1", profile.forward_matrix_1.astype(np.float32)
    if profile.forward_matrix_2 is not None:
        return "ForwardMatrix2", profile.forward_matrix_2.astype(np.float32)

    if profile.color_matrix_1 is not None:
        return "inverse(ColorMatrix1)", np.linalg.inv(profile.color_matrix_1).astype(np.float32)
    if profile.color_matrix_2 is not None:
        return "inverse(ColorMatrix2)", np.linalg.inv(profile.color_matrix_2).astype(np.float32)

    raise ValueError(f"{profile.path} does not contain a usable DCP color matrix")


def select_hue_sat_map(profile: DcpProfile) -> DcpHueSatMap | None:
    return profile.hue_sat_map_1


def apply_hsv_table(rgb: np.ndarray, table: DcpHueSatMap, encoding: int) -> np.ndarray:
    flat = np.asarray(rgb, dtype=np.float32).reshape((-1, 3))
    output = np.empty_like(flat)
    for start in range(0, flat.shape[0], HSV_TABLE_CHUNK_PIXELS):
        end = min(start + HSV_TABLE_CHUNK_PIXELS, flat.shape[0])
        output[start:end] = apply_hsv_table_chunk(flat[start:end], table, encoding)
    return output.reshape(rgb.shape)


def apply_hsv_table_chunk(rgb: np.ndarray, table: DcpHueSatMap, encoding: int) -> np.ndarray:
    nonneg = np.maximum(np.nan_to_num(rgb, nan=0.0, posinf=1.0, neginf=0.0), 0.0)
    # Scene-linear values can exceed 1.0 (highlight headroom). The HueSatMap is
    # defined on [0, 1], so look it up on clamped values but restore the headroom
    # afterwards so highlights are not crushed to white.
    value_in = nonneg.max(axis=1)
    working = np.clip(nonneg, 0, 1)
    if encoding == ENCODING_SRGB:
        working = srgb_encode_float(working)

    hsv = rgb_to_hsv(working)
    delta = sample_hsv_table(hsv[:, 0], hsv[:, 1], hsv[:, 2], table)
    hsv[:, 0] = np.mod(hsv[:, 0] + delta[:, 0] / 360.0, 1.0)
    hsv[:, 1] = np.clip(hsv[:, 1] * delta[:, 1], 0, 1)
    hsv[:, 2] = np.clip(hsv[:, 2] * delta[:, 2], 0, 1)

    mapped = hsv_to_rgb(hsv)
    if encoding == ENCODING_SRGB:
        mapped = srgb_decode_float(mapped)
    # Pixels with value <= 1 are unchanged (scale == 1); only headroom is restored.
    scale = np.maximum(value_in, 1.0)
    mapped = mapped * scale[:, None]
    return mapped.astype(np.float32)


def sample_hsv_table(hue: np.ndarray, saturation: np.ndarray, value: np.ndarray, table: DcpHueSatMap) -> np.ndarray:
    hue_count, sat_count, val_count = table.dimensions

    if hue_count > 1:
        hue_scaled = np.mod(hue, 1.0) * hue_count
        hue0 = np.floor(hue_scaled).astype(np.int32) % hue_count
        hue1 = (hue0 + 1) % hue_count
        hue_t = (hue_scaled - np.floor(hue_scaled)).astype(np.float32)
    else:
        hue0 = np.zeros_like(hue, dtype=np.int32)
        hue1 = hue0
        hue_t = np.zeros_like(hue, dtype=np.float32)

    sat_scaled = np.clip(saturation, 0, 1) * (sat_count - 1)
    sat0 = np.floor(sat_scaled).astype(np.int32)
    sat1 = np.clip(sat0 + 1, 0, sat_count - 1)
    sat_t = (sat_scaled - sat0).astype(np.float32)

    if val_count > 1:
        val_scaled = np.clip(value, 0, 1) * (val_count - 1)
        val0 = np.floor(val_scaled).astype(np.int32)
        val1 = np.clip(val0 + 1, 0, val_count - 1)
        val_t = (val_scaled - val0).astype(np.float32)
    else:
        val0 = np.zeros_like(value, dtype=np.int32)
        val1 = val0
        val_t = np.zeros_like(value, dtype=np.float32)

    def gather(val_index: np.ndarray, hue_index: np.ndarray, sat_index: np.ndarray) -> np.ndarray:
        return table.data[val_index, hue_index, sat_index]

    def lerp(left: np.ndarray, right: np.ndarray, amount: np.ndarray) -> np.ndarray:
        return left * (1 - amount[:, None]) + right * amount[:, None]

    val0_hue0 = lerp(gather(val0, hue0, sat0), gather(val0, hue0, sat1), sat_t)
    val0_hue1 = lerp(gather(val0, hue1, sat0), gather(val0, hue1, sat1), sat_t)
    val0_sample = lerp(val0_hue0, val0_hue1, hue_t)

    val1_hue0 = lerp(gather(val1, hue0, sat0), gather(val1, hue0, sat1), sat_t)
    val1_hue1 = lerp(gather(val1, hue1, sat0), gather(val1, hue1, sat1), sat_t)
    val1_sample = lerp(val1_hue0, val1_hue1, hue_t)

    return lerp(val0_sample, val1_sample, val_t).astype(np.float32)


def rgb_to_hsv(rgb: np.ndarray) -> np.ndarray:
    rgb = np.clip(rgb, 0, 1)
    red = rgb[:, 0]
    green = rgb[:, 1]
    blue = rgb[:, 2]
    max_channel = np.max(rgb, axis=1)
    min_channel = np.min(rgb, axis=1)
    delta = max_channel - min_channel
    hue = np.zeros_like(max_channel, dtype=np.float32)
    mask = delta > 1e-8
    max_index = np.argmax(rgb, axis=1)

    red_mask = mask & (max_index == 0)
    green_mask = mask & (max_index == 1)
    blue_mask = mask & (max_index == 2)
    hue[red_mask] = np.mod((green[red_mask] - blue[red_mask]) / delta[red_mask], 6.0)
    hue[green_mask] = ((blue[green_mask] - red[green_mask]) / delta[green_mask]) + 2.0
    hue[blue_mask] = ((red[blue_mask] - green[blue_mask]) / delta[blue_mask]) + 4.0
    hue = np.mod(hue / 6.0, 1.0)

    saturation = np.zeros_like(max_channel, dtype=np.float32)
    value_mask = max_channel > 1e-8
    saturation[value_mask] = delta[value_mask] / max_channel[value_mask]
    return np.stack([hue, saturation, max_channel], axis=1).astype(np.float32)


def hsv_to_rgb(hsv: np.ndarray) -> np.ndarray:
    hue = np.mod(hsv[:, 0], 1.0) * 6.0
    saturation = np.clip(hsv[:, 1], 0, 1)
    value = np.clip(hsv[:, 2], 0, 1)
    sector = np.floor(hue).astype(np.int32) % 6
    fraction = hue - np.floor(hue)
    p = value * (1 - saturation)
    q = value * (1 - saturation * fraction)
    t = value * (1 - saturation * (1 - fraction))

    output = np.empty_like(hsv, dtype=np.float32)
    masks = [sector == index for index in range(6)]
    output[masks[0]] = np.stack([value[masks[0]], t[masks[0]], p[masks[0]]], axis=1)
    output[masks[1]] = np.stack([q[masks[1]], value[masks[1]], p[masks[1]]], axis=1)
    output[masks[2]] = np.stack([p[masks[2]], value[masks[2]], t[masks[2]]], axis=1)
    output[masks[3]] = np.stack([p[masks[3]], q[masks[3]], value[masks[3]]], axis=1)
    output[masks[4]] = np.stack([t[masks[4]], p[masks[4]], value[masks[4]]], axis=1)
    output[masks[5]] = np.stack([value[masks[5]], p[masks[5]], q[masks[5]]], axis=1)
    return output


def srgb_encode_float(linear: np.ndarray) -> np.ndarray:
    linear = np.clip(linear, 0, 1)
    return np.where(linear <= 0.0031308, linear * 12.92, 1.055 * np.power(linear, 1 / 2.4) - 0.055)


def srgb_decode_float(encoded: np.ndarray) -> np.ndarray:
    encoded = np.clip(encoded, 0, 1)
    return np.where(encoded <= 0.04045, encoded / 12.92, np.power((encoded + 0.055) / 1.055, 2.4))


def table_info(table: DcpHueSatMap, encoding: int) -> dict[str, Any]:
    return {"dimensions": list(table.dimensions), "encoding": encoding_name(encoding)}


def encoding_name(encoding: int) -> str:
    if encoding == ENCODING_SRGB:
        return "sRGB"
    return "Linear"


def read_tiff_tags(data: bytes) -> dict[str, Any]:
    endian = read_endian(data)
    first_ifd = read_u32(data, 4, endian)
    tags: dict[str, Any] = {}
    offset = first_ifd

    while offset:
        entry_count = read_u16(data, offset, endian)
        offset += 2
        for index in range(entry_count):
            entry_offset = offset + index * 12
            tag = read_u16(data, entry_offset, endian)
            if tag not in DCP_TAGS:
                continue
            field_type = read_u16(data, entry_offset + 2, endian)
            count = read_u32(data, entry_offset + 4, endian)
            value_offset = data[entry_offset + 8 : entry_offset + 12]
            tags[DCP_TAGS[tag]] = read_tiff_value(data, endian, field_type, count, value_offset)
        offset = read_u32(data, offset + entry_count * 12, endian)

    return tags


def read_endian(data: bytes) -> str:
    marker = data[:2]
    if marker == b"II":
        endian = "<"
    elif marker == b"MM":
        endian = ">"
    else:
        raise ValueError("DCP file is missing a TIFF byte-order marker")

    if read_u16(data, 2, endian) not in {42, 0x4352}:
        raise ValueError("DCP file is not a classic TIFF container")

    return endian


def read_tiff_value(data: bytes, endian: str, field_type: int, count: int, value_offset: bytes) -> Any:
    if field_type not in TIFF_TYPE_SIZES:
        return None

    byte_count = TIFF_TYPE_SIZES[field_type] * count
    raw = value_offset[:byte_count] if byte_count <= 4 else data[struct.unpack(endian + "I", value_offset)[0] :][:byte_count]

    if field_type == 2:
        return raw.rstrip(b"\x00").decode("utf-8", errors="replace")
    if field_type in {1, 7}:
        return list(raw)
    if field_type == 3:
        return list(struct.unpack(endian + f"{count}H", raw))
    if field_type == 4:
        return list(struct.unpack(endian + f"{count}I", raw))
    if field_type == 5:
        ints = struct.unpack(endian + f"{count * 2}I", raw)
        return [ints[index] / ints[index + 1] for index in range(0, len(ints), 2)]
    if field_type == 8:
        return list(struct.unpack(endian + f"{count}h", raw))
    if field_type == 9:
        return list(struct.unpack(endian + f"{count}i", raw))
    if field_type == 10:
        ints = struct.unpack(endian + f"{count * 2}i", raw)
        return [ints[index] / ints[index + 1] for index in range(0, len(ints), 2)]
    if field_type == 11:
        return list(struct.unpack(endian + f"{count}f", raw))
    if field_type == 12:
        return list(struct.unpack(endian + f"{count}d", raw))

    return None


def matrix_from_tag(value: Any, rows: int | None) -> np.ndarray | None:
    if not value:
        return None

    values = np.array(value, dtype=np.float32)
    row_count = rows if rows is not None else max(1, values.size // 3)
    if values.size != row_count * 3:
        return None

    matrix = values.reshape((row_count, 3))
    if matrix.shape != (3, 3):
        return None
    return matrix


def tone_curve_from_tag(value: Any) -> np.ndarray | None:
    if not value or len(value) < 4 or len(value) % 2 != 0:
        return None

    curve = np.array(value, dtype=np.float32).reshape((-1, 2))
    curve[:, 0] = np.clip(curve[:, 0], 0, 1)
    curve[:, 1] = np.clip(curve[:, 1], 0, 1)
    order = np.argsort(curve[:, 0])
    curve = curve[order]
    if curve[0, 0] > 0:
        curve = np.vstack([np.array([[0, 0]], dtype=np.float32), curve])
    if curve[-1, 0] < 1:
        curve = np.vstack([curve, np.array([[1, 1]], dtype=np.float32)])
    return curve


def hue_sat_map_from_tags(dimensions_value: Any, data_value: Any) -> DcpHueSatMap | None:
    if not dimensions_value or not data_value or len(dimensions_value) != 3:
        return None

    hue_count, sat_count, val_count = [int(value) for value in dimensions_value]
    if hue_count < 1 or sat_count < 2 or val_count < 1:
        return None

    values = np.array(data_value, dtype=np.float32)
    full_count = hue_count * sat_count * val_count * 3
    skipped_zero_sat_count = hue_count * (sat_count - 1) * val_count * 3
    if values.size == full_count:
        data = values.reshape((val_count, hue_count, sat_count, 3))
    elif values.size == skipped_zero_sat_count:
        data = np.zeros((val_count, hue_count, sat_count, 3), dtype=np.float32)
        data[:, :, 1:, :] = values.reshape((val_count, hue_count, sat_count - 1, 3))
        data[:, :, 0, 0] = data[:, :, 1, 0]
        data[:, :, 0, 1] = data[:, :, 1, 1]
        data[:, :, 0, 2] = 1.0
    else:
        return None

    data[:, :, 0, 2] = 1.0
    return DcpHueSatMap(dimensions=(hue_count, sat_count, val_count), data=data)


def encoding_from_tag(value: Any) -> int:
    if isinstance(value, list) and value:
        encoding = int(value[0])
    elif isinstance(value, int):
        encoding = value
    else:
        encoding = ENCODING_LINEAR

    if encoding not in {ENCODING_LINEAR, ENCODING_SRGB}:
        return ENCODING_LINEAR
    return encoding


def string_from_tag(value: Any) -> str | None:
    if isinstance(value, str):
        text = value.strip()
        return text or None
    return None


def read_u16(data: bytes, offset: int, endian: str) -> int:
    return struct.unpack_from(endian + "H", data, offset)[0]


def read_u32(data: bytes, offset: int, endian: str) -> int:
    return struct.unpack_from(endian + "I", data, offset)[0]
