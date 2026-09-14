"""The browser's Creative Look rebuild against the worker's own.

A moved tweak or DRO strength no longer asks the worker for a profile: the
browser rebuilds it from `lookCalibration` (sony/profile.py
look_calibration_block) with rendering/sony-look.ts, a transcription of
tone.py / chroma.py / clarity.py / dro.py. Export still asks the worker, so the
two have to agree to the bit, and this fixture is how they are held together:

    tests/fixtures/look_rebuild.json

holds the block for the sample ARW and, for a handful of tweak/DRO settings,
what look_render_info makes of them. This module asserts the worker still
produces the fixture (regenerate with LLR_UPDATE_FIXTURES=1 when the engine's
construction changes, on purpose); apps/web/src/rendering/__tests__/
sony-look.spec.ts asserts the mirror produces it too.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from llr_worker.sony.dro import dro_gain_table
from llr_worker.sony.profile import (
    LookTweaks,
    calibration_for,
    chroma_terms,
    look_calibration_block,
    look_render_info,
)
from llr_worker.sony.tone import TUNE_GAIN_ROWS, TUNE_TABLE_LEN, _family

ROOT = Path(__file__).resolve().parents[3]
SAMPLE_FL = ROOT / "samples" / "DSC01157.ARW"  # ILCE-7CM2, Creative Look FL, DRO Auto
FIXTURE = Path(__file__).parent / "fixtures" / "look_rebuild.json"
FAMILY_ASSET = ROOT / "apps" / "web" / "public" / "sony-tone-family.bin"
BODY = "ILCE-7CM2"
STYLE = "FL"

requires_sample = pytest.mark.skipif(not SAMPLE_FL.exists(), reason="sample ARW not checked out")

# What the shot recorded, on the panel scale (DSC01157: Highlights -6,
# Shadows +1, Clarity +1 in camera). The rebuild merges overrides onto these.
# The cases reach every branch the mirror has: the family's two ends and past
# them (tone.py extrapolates), a Fade between entries and past the last one,
# 黑色/白色 both signs (the level arithmetic truncates toward zero), 色相 both
# slopes, 饱和度's zero, 清晰 between table entries, and DRO scaled, at unit
# strength, and off.
CASES: list[dict[str, Any]] = [
    {"name": "as shot", "look": {}, "dro": None, "level": -1},
    {"name": "tone tweaks inside the family", "look": {"contrast": 37, "highlights": -45, "shadows": 12},
     "dro": 0.7, "level": -1},
    {"name": "tone tweaks past both ends", "look": {"contrast": 100, "highlights": 100, "shadows": -100},
     "dro": 1.0, "level": -1},
    {"name": "tone tweaks past the low end", "look": {"contrast": -100, "highlights": -100, "shadows": 100},
     "dro": 0.0, "level": -1},
    {"name": "the other six", "look": {"fade": 55, "black": 33, "white": -47, "hue": -60, "saturation": 30, "clarity": 45},
     "dro": 1.6, "level": -1},
    {"name": "fade past the table, saturation zero, levels the other way",
     "look": {"fade": 100, "black": -31, "white": 26, "hue": 71, "saturation": -100, "clarity": 100},
     "dro": 2.0, "level": -1},
    {"name": "everything clamped", "look": {"contrast": 250, "highlights": -250, "shadows": 3.7, "fade": -5,
                                             "black": 1e9, "white": -1e9, "hue": 100.9, "saturation": 12.5, "clarity": -1},
     "dro": 0.3, "level": -1},
]

# The profile fields a tweak or the strength can move; everything else the
# mirror copies from the base and the fixture does not repeat.
REBUILT_FIELDS = (
    "profileToneCurve", "profileChromaGain", "profileChromaSaturation", "profileChromaHue",
    "profileLumaPivot", "profileLumaContrast", "profileLumaContrastAdvanced",
    "profileLumaBlack", "profileLumaScale", "profileDroGain", "droStrength", "droLevel",
    "profileClarity", "profileCameraMatch", "lookTweaks",
)


def build_fixture() -> dict[str, Any]:
    cal = calibration_for(SAMPLE_FL, STYLE)
    assert cal is not None
    dro_gain = dro_gain_table(SAMPLE_FL)
    assert dro_gain is not None, "the sample was shot with DRO Auto"
    as_shot = LookTweaks(highlights=-30, shadows=5, clarity=10)
    base = look_render_info(cal, STYLE, as_shot, as_shot, dro=True, dro_gain=dro_gain, body=BODY).to_json()
    cases = []
    for case in CASES:
        tweaks = as_shot.merged(case["look"])
        info = look_render_info(cal, STYLE, tweaks, as_shot, dro=True, dro_gain=dro_gain,
                                dro_strength=case["dro"], dro_level=case["level"], body=BODY)
        out = info.to_json()
        cases.append({"name": case["name"], "look": case["look"], "dro": case["dro"], "level": case["level"],
                      "expected": {k: out[k] for k in REBUILT_FIELDS}})
    return {
        "sample": SAMPLE_FL.name, "body": BODY, "style": STYLE,
        "base": {k: base[k] for k in ("kind", "creativeLook", "lookAsShot", "lookRanges", "droAsShot",
                                       "droStrength", "droLevel", "profileDroGain", "profileClarity",
                                       "lookCalibration")},
        "cases": cases,
    }


def _same(got: Any, want: Any, path: str) -> None:
    """Deep equality, with floats to 1e-12 — the mirror's pow() may differ from
    C's in the last bit; anything coarser is a construction that drifted."""
    if isinstance(want, dict):
        assert isinstance(got, dict) and set(got) == set(want), path
        for k in want:
            _same(got[k], want[k], f"{path}.{k}")
    elif isinstance(want, list):
        assert isinstance(got, list) and len(got) == len(want), path
        if want and all(isinstance(v, (int, float)) for v in want):
            assert np.allclose(np.asarray(got, float), np.asarray(want, float), rtol=0, atol=1e-12), path
        else:
            for i, (g, w) in enumerate(zip(got, want, strict=True)):
                _same(g, w, f"{path}[{i}]")
    elif isinstance(want, float):
        assert abs(float(got) - want) <= 1e-12, path
    else:
        assert got == want, path


@requires_sample
def test_the_fixture_is_what_the_worker_builds_now() -> None:
    fresh = build_fixture()
    if os.environ.get("LLR_UPDATE_FIXTURES"):
        FIXTURE.write_text(json.dumps(fresh, separators=(",", ":")) + "\n", encoding="utf-8")
    assert FIXTURE.exists(), "run once with LLR_UPDATE_FIXTURES=1"
    _same(fresh, json.loads(FIXTURE.read_text(encoding="utf-8")), "fixture")


@requires_sample
def test_the_calibration_block_is_the_look_s_own_inputs() -> None:
    cal = calibration_for(SAMPLE_FL, STYLE)
    assert cal is not None
    gain = chroma_terms(cal)[1]
    block = look_calibration_block(cal, STYLE, BODY, gain, dro=True, dro_gain=dro_gain_table(SAMPLE_FL))
    assert block["curveX"] == [int(v) for v in cal.curve_x]
    assert block["curveY"] == [int(v) for v in cal.curve_y]
    # The mirror's interp assumes the control points' x ascend strictly.
    assert np.all(np.diff(cal.curve_x.astype(np.int64)) > 0)
    assert len(block["lumaPivot"]) == len(block["lumaContrast"]) == cal.luma_pivot.size
    assert len(block["chromaGain"]) == 4
    assert block["droGainAsShot"] is not None and block["droGainNoCurve"] is None
    assert block["cameraMatch"] != block["cameraMatchFade"]
    # A shot that used DRO but wrote no curve gets the preset to scale instead.
    no_curve = look_calibration_block(cal, STYLE, BODY, gain, dro=True, dro_gain=None)
    assert no_curve["droGainAsShot"] is None and len(no_curve["droGainNoCurve"]) == 512
    # ...and one without DRO at all gets neither: nothing to scale.
    off = look_calibration_block(cal, STYLE, BODY, gain, dro=False, dro_gain=None)
    assert off["droGainAsShot"] is None and off["droGainNoCurve"] is None


@requires_sample
def test_the_profile_carries_the_block_and_saturation_minus_100_still_builds() -> None:
    cal = calibration_for(SAMPLE_FL, STYLE)
    assert cal is not None
    profile = look_render_info(cal, STYLE, body=BODY).to_json()
    assert profile["lookCalibration"]["curveX"] == [int(v) for v in cal.curve_x]
    # Saturation -100 used to divide the gains by zero; the shader zeroes the
    # chroma by its multiply-back, so the gains simply go out undivided.
    zero = look_render_info(cal, STYLE, LookTweaks(saturation=-100)).to_json()
    assert zero["profileChromaSaturation"] == 0.0
    assert zero["profileChromaGain"] == profile["lookCalibration"]["chromaGain"]


def test_the_web_asset_is_the_family() -> None:
    """public/sony-tone-family.bin is what make_tone_family_asset.py wrote from
    data/tone_family.npz; a retaken family has to be re-exported."""
    assert FAMILY_ASSET.exists(), "run sony_repro/tools/make_tone_family_asset.py"
    back = np.frombuffer(FAMILY_ASSET.read_bytes(), "<u2").reshape(TUNE_GAIN_ROWS, TUNE_TABLE_LEN)
    assert np.array_equal(back, _family())
