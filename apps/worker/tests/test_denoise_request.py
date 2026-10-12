"""How a render request's denoise block turns into decodes.

The browser cannot know a frame is Sony's until the first decode answers, so
that first request still carries the wavelet's `auto` switch. On a Sony frame
it must decode once, denoised, exactly as the manual panel at the same amount
would — not blend a noisy decode back in by the slider's amount.
"""

from pathlib import Path
from typing import Any

import pytest

from llr_worker import cli


class _Stop(Exception):
    pass


def _first_decode(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, model: str, denoise: dict[str, Any]) -> dict[str, Any]:
    source = tmp_path / "frame.arw"
    source.write_bytes(b"")
    monkeypatch.setattr(cli, "effective_model", lambda requested, path: model)
    monkeypatch.setattr(cli, "read_exiftool_metadata", lambda path: {"ISO": 3200})
    calls: list[dict[str, Any]] = []

    def prepare_linear(*args: Any, **kwargs: Any) -> Any:
        calls.append(kwargs)
        raise _Stop

    monkeypatch.setattr(cli, "prepare_linear", prepare_linear)
    out = tmp_path / "sessions" / "s"
    out.mkdir(parents=True, exist_ok=True)
    request = {"input": str(source), "output": str(out / "o.f16"), "profile": "sony",
               "halfSize": False, "maxSize": 0, "denoise": denoise}
    with pytest.raises(_Stop):
        cli.daemon_linear(request, tmp_path)
    calls[0].pop("raw_provider", None)  # a fresh closure per request
    return calls[0]


def test_sony_auto_decodes_once_like_the_manual_panel(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    base = {"enabled": True, "amount": 0.5, "edge": 50, "chroma": 50}
    auto = _first_decode(monkeypatch, tmp_path, "sony", {**base, "auto": True})
    manual = _first_decode(monkeypatch, tmp_path, "sony", {**base, "auto": False})
    # The first decode is already the denoised one: a noisy decode first is
    # what a blend of the two would start with.
    assert auto.get("denoise_model") == "sony"
    assert auto == manual


def test_wavelet_auto_still_blends_by_iso(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    first = _first_decode(monkeypatch, tmp_path, "wavelet",
                          {"enabled": True, "auto": True, "amount": 0.5, "edge": 50, "chroma": 50})
    # ISO 3200 is past 1600, so Auto is the full amount and the noisy decode is skipped.
    assert first.get("denoise_model") == "wavelet"
