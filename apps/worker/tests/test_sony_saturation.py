"""Saturation normalization shared by worker exports and browser profiles."""
import json
from pathlib import Path

import numpy as np
import pytest

from llr_worker.sony.chroma import rgb_to_ycc, saturation_factor
from llr_worker.sony.profile import LookTweaks, apply_look_overrides, chroma_terms, look_render_info
from llr_worker.sony.sr2 import LookCalibration


def calibration() -> LookCalibration:
    block = json.loads((Path(__file__).parent / "fixtures/look_rebuild.json").read_text())["base"]["lookCalibration"]
    return LookCalibration(
        name="Standard", param_block=bytes(276),
        curve_x=np.array(block["curveX"]), curve_y=np.array(block["curveY"]),
        chroma_base=np.array([0, 0, 0, 0, 1024, 1024, 1024, 1024], dtype=np.int16),
        chroma_deltas=np.zeros((4, 8), dtype=np.int16),
        chroma_weights=np.array([1024, 0, 0, 0], dtype=np.int16),
        luma_pivot=np.array(block["lumaPivot"]), luma_contrast=np.array(block["lumaContrast"]),
        chroma_final=None,
    )


@pytest.mark.parametrize("capture", [0, 25, -55, 55])
def test_worker_rebuild_preserves_capture_normalization(capture: int, monkeypatch) -> None:
    cal = calibration()
    shot = LookTweaks(saturation=capture)
    initial = look_render_info(cal, "ST", shot).to_json()
    _, gain = chroma_terms(cal)
    expected = gain / saturation_factor(capture)
    assert np.allclose(initial["profileChromaGain"], expected)
    monkeypatch.setattr("llr_worker.sony.profile.calibration_for", lambda *_: cal)
    for value in (-100, -90, -50, -20, 0, 20, 25, 50, 100, capture):
        edited = look_render_info(cal, "ST", LookTweaks(saturation=value), shot).to_json()
        rebuilt = apply_look_overrides(initial, Path("unused.ARW"), {"saturation": value})
        assert edited["profileChromaGain"] == initial["profileChromaGain"]
        assert rebuilt["profileChromaGain"] == edited["profileChromaGain"]
        assert rebuilt["profileChromaSaturation"] == saturation_factor(value)
        assert rebuilt["lookAsShot"] == initial["lookAsShot"]


@pytest.mark.parametrize("filename,style,capture", [("DSC07512.ARW", "SH", 25), ("DSC03277.ARW", "VV", 0)])
def test_local_raw_profile_and_reference_agree(filename: str, style: str, capture: int) -> None:
    from llr_worker.sony.profile import calibration_for

    raw = Path(__file__).resolve().parents[3] / "photos" / filename
    if not raw.exists():
        pytest.skip("local Sony validation RAW not available")
    cal = calibration_for(raw, style)
    assert cal is not None
    cross, gain = chroma_terms(cal)
    shot = LookTweaks(saturation=capture)
    rgb = np.array([[[.45, .4, .35], [.38, .4, .42]]], dtype=np.float32)
    for value in (-100, -90, -50, -20, capture, 20, 50, 100):
        info = look_render_info(cal, style, LookTweaks(saturation=value), shot)
        # The shader receives normalized gains and needs no second division.
        shader = rgb_to_ycc(rgb, cross, np.array(info.chroma_gain), info.chroma_saturation)
        reference = rgb_to_ycc(rgb, cross, gain, saturation_factor(value), saturation_factor(capture))
        for a, b in zip(shader, reference, strict=True):
            assert np.allclose(a, b, atol=1e-7)
