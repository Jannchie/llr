"""The per-photo metadata the catalog keeps: the handful of EXIF fields a
library view sorts and labels by, mapped from exiftool's record into the
camelCase shape the API stores. Pure — the exiftool call lives in cli.py."""

from __future__ import annotations

import re
from typing import Any

# "2024:05:01 12:34:56" (EXIF) with an optional sub-second or zone already
# attached; the zone normally arrives separately as OffsetTimeOriginal.
_EXIF_DATETIME = re.compile(
    r"^(?P<y>\d{4}):(?P<m>\d{2}):(?P<d>\d{2})[ T](?P<H>\d{2}):(?P<M>\d{2}):(?P<S>\d{2})(?P<frac>\.\d+)?(?P<zone>[+-]\d{2}:\d{2}|Z)?$"
)


def parse_exif_datetime(value: Any, offset: Any = None) -> str | None:
    """EXIF's colon-separated date to ISO 8601, so it sorts as a string.

    The zone is kept only when the camera wrote one — a naive stamp is still a
    correct local time, and inventing UTC would put every shot hours off."""
    if not isinstance(value, str):
        return None
    match = _EXIF_DATETIME.match(value.strip())
    if not match:
        return None
    if match["y"] == "0000":  # some bodies write an all-zero stamp when the clock was never set
        return None
    zone = match["zone"] or (offset.strip() if isinstance(offset, str) and re.fullmatch(r"[+-]\d{2}:\d{2}", offset.strip()) else "")
    return f"{match['y']}-{match['m']}-{match['d']}T{match['H']}:{match['M']}:{match['S']}{match['frac'] or ''}{zone}"


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return None
    return None


def _text(value: Any) -> str | None:
    if isinstance(value, str):
        text = value.strip()
        return text or None
    return None


def catalog_meta_from_exif(record: dict[str, Any]) -> dict[str, Any]:
    """exiftool's `-j` record (numeric tags requested with `#`) → catalog fields.

    Missing tags come out as None rather than absent so the API's column
    update is the same shape for every file."""
    orientation = _number(record.get("Orientation"))
    iso = _number(record.get("ISO"))
    return {
        "orientation": int(orientation) if orientation is not None else None,
        "capturedAt": parse_exif_datetime(record.get("DateTimeOriginal"), record.get("OffsetTimeOriginal")),
        "make": _text(record.get("Make")),
        "model": _text(record.get("Model")),
        "lens": _text(record.get("LensModel")),
        "iso": int(iso) if iso is not None else None,
        "exposure": _number(record.get("ExposureTime")),
        "fnumber": _number(record.get("FNumber")),
        "focal": _number(record.get("FocalLength")),
    }
