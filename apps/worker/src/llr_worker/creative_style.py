"""Sony Creative Look / Creative Style codes.

Sony reports a Creative Look either as its two-letter code or as the full menu
name, depending on which tag the body wrote. The Adobe profiles are named by
code and the Sony tone curves are keyed by code, so everything is normalised to
the code.
"""

from __future__ import annotations

STYLE_ALIASES = {
    "STANDARD": "ST", "PORTRAIT": "PT", "NEUTRAL": "NT", "VIVID": "VV",
    "VIVID2": "VV2", "FILM": "FL", "INSTANT": "IN", "SOFTHIGHKEY": "SH",
    "BW": "BW", "B/W": "BW", "BLACK&WHITE": "BW", "SEPIA": "SE",
    "LANDSCAPE": "LD",
}


def normalize_style(value: str | None) -> str | None:
    if not value:
        return None
    key = value.strip().upper()
    return STYLE_ALIASES.get(key.replace(" ", "").replace("-", ""), key)
