"""What the export tells exiftool about the file it wrote."""

from pathlib import Path

import pytest

from llr_worker import cli


def _exiftool_args(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, **kwargs: bool) -> list[str]:
    calls: list[list[str]] = []
    monkeypatch.setattr(cli, "detect_exiftool", lambda: "exiftool")
    monkeypatch.setattr(cli, "exiftool_env", lambda: {})
    monkeypatch.setattr(cli, "run_capture", lambda args, **_: calls.append(list(args)))
    assert cli.copy_exif_provenance(tmp_path / "source.arw", tmp_path / "export.jpg", **kwargs)
    return calls[0]


def test_an_srgb_export_is_tagged_srgb(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    assert "-ColorSpace#=1" in _exiftool_args(monkeypatch, tmp_path)


def test_a_p3_export_points_at_its_embedded_profile(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # EXIF has no value for Display P3. Tagging the file sRGB would contradict
    # the profile the encoder embedded; "uncalibrated" defers to it.
    args = _exiftool_args(monkeypatch, tmp_path, wide_gamut=True)
    assert "-ColorSpace#=65535" in args
    assert "-ColorSpace#=1" not in args
    # The RAW's own profile is still kept out: it describes the sensor, not the render.
    assert "--icc_profile:all" in args


def test_private_tags_are_dropped_only_when_asked(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    assert "--gps:all" not in _exiftool_args(monkeypatch, tmp_path)
    assert "--gps:all" in _exiftool_args(monkeypatch, tmp_path, strip_private=True)
