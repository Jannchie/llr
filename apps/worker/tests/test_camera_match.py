"""Camera match: the fitted residual between this chain's finished frame and
the body's own JPEG (sony/profile.py camera_match_table, data/camera_match.json).

Two things are pinned here. Which table a shot gets — by body first, then by
look, with "*" the pooled fallback and no table at all for a body the fit never
saw. And what the correction *does* to a Lab point, taken from the reference
implementation itself (sony_repro/tools/camera_match_fit.py apply()) so the
browser's mirror (apps/web/src/rendering/__tests__/camera-match.spec.ts) can
assert the same numbers against the same shipped table.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from llr_worker.cli import camera_body_from_exif, daemon_look_profile, detect_exiftool
from llr_worker.sony.profile import (
    CAMERA_MATCH_C_AXIS,
    CAMERA_MATCH_C_STEPS,
    CAMERA_MATCH_DATA,
    CAMERA_MATCH_H_AXIS,
    CAMERA_MATCH_H_SECTORS,
    CAMERA_MATCH_L_AXIS,
    CAMERA_MATCH_L_STEPS,
    calibration_for,
    camera_match_table,
    look_render_info,
)

SAMPLES = Path(__file__).resolve().parents[3] / "samples"
SAMPLE_FL = SAMPLES / "DSC01157.ARW"  # ILCE-7CM2, Creative Look FL
REFERENCE = Path(__file__).resolve().parents[3] / "sony_repro" / "tools" / "camera_match_fit.py"

BODY = "ILCE-7CM2"

requires_sample = pytest.mark.skipif(not SAMPLE_FL.exists(), reason="sample ARW not checked out")
requires_exiftool = pytest.mark.skipif(detect_exiftool() is None, reason="exiftool not installed")
requires_reference = pytest.mark.skipif(not REFERENCE.exists(), reason="sony_repro not checked out")

# Lab points through the shipped FL table, as camera_match_fit.apply() gives
# them (rounded to 1e-5; tmp/nas/agents/cm2_ref.py prints them). Chosen to hit
# both ends of the L* axis (clamped), a near-neutral where the chroma ratio has
# almost nothing to scale, a C* past the grid's last column (held), and a hue
# past the last sector centre (periodic wrap).
LAB_POINTS = [
    [50.0, 20.0, 10.0],
    [12.0, -30.0, 25.0],
    [97.0, 5.0, -40.0],
    [3.0, 0.5, -0.2],
    [60.0, -25.0, -25.0],
    [85.0, 40.0, 60.0],
    [55.0, 80.0, 60.0],
    [50.0, 30.0, -3.0],
]
FL_EXPECTED = [
    [48.57621, 18.86754, 9.93076],
    [11.74036, -29.05936, 25.01010],
    [94.35281, 2.72001, -39.98884],
    [2.81830, 0.44942, -0.18345],
    [58.30801, -25.54183, -23.41175],
    [83.68644, 40.44031, 58.41276],
    [54.77280, 80.65485, 61.22404],
    [48.57830, 29.14034, -2.41781],
]
# The first point through the pooled table and through IN, so a mirror that
# picked the wrong table cannot pass by accident.
POOLED_EXPECTED_FIRST = [48.25873, 18.95516, 9.65470]
IN_EXPECTED_FIRST = [49.47538, 18.57796, 9.36519]


def test_the_shipped_table_is_the_fit_s_own_shape() -> None:
    data = json.loads(CAMERA_MATCH_DATA.read_text(encoding="utf-8"))
    assert BODY in data
    assert "*" in data[BODY]
    # The axes the fit wrote are the ones the shader's step sizes assume.
    assert data[BODY]["lAxis"] == CAMERA_MATCH_L_AXIS
    assert data[BODY]["cAxis"] == CAMERA_MATCH_C_AXIS
    assert data[BODY]["hAxis"] == CAMERA_MATCH_H_AXIS
    for look, table in data[BODY].items():
        if not isinstance(table, dict):
            continue
        assert len(table["dL"]) == CAMERA_MATCH_L_STEPS, look
        assert len(table["cr"]) == CAMERA_MATCH_L_STEPS, look
        assert all(len(row) == CAMERA_MATCH_C_STEPS for row in table["dL"]), look
        assert all(len(row) == CAMERA_MATCH_C_STEPS for row in table["cr"]), look
        assert len(table["hs"]) == CAMERA_MATCH_H_SECTORS, look


def test_a_fitted_look_gets_its_own_table_and_an_unknown_look_the_pooled_one() -> None:
    data = json.loads(CAMERA_MATCH_DATA.read_text(encoding="utf-8"))[BODY]
    fl = camera_match_table(BODY, "FL")
    assert fl is not None
    assert fl == {k: data["FL"][k] for k in ("dL", "cr", "hs")}
    # Only the three tables travel; the frame count and the axes are the fit's
    # business (the browser has its own copy of the axes).
    assert set(fl) == {"dL", "cr", "hs"}
    # A look the fit had too few frames of falls to the pooled table.
    assert camera_match_table(BODY, "SE") == {k: data["*"][k] for k in ("dL", "cr", "hs")}
    assert camera_match_table(BODY, None) == camera_match_table(BODY, "SE")
    assert camera_match_table(BODY, "FL") != camera_match_table(BODY, "IN")


def test_an_unknown_body_gets_no_table_rather_than_another_body_s() -> None:
    assert camera_match_table("ILCE-7RM5", "FL") is None
    assert camera_match_table(None, "FL") is None
    assert camera_match_table("", "FL") is None


def test_the_body_key_is_the_exif_model_trimmed() -> None:
    assert camera_body_from_exif({"Model": " ILCE-7CM2 "}) == BODY
    assert camera_body_from_exif({}) is None
    assert camera_body_from_exif({"Model": ""}) is None


@requires_reference
def test_the_pinned_lab_numbers_are_the_reference_implementation_s() -> None:
    """The numbers the browser's mirror asserts, regenerated from the source of
    truth: if the fit's apply() or the shipped table changes, this fails first
    and the constants above (and the web spec's copy) are what to update."""
    ns: dict = {"__file__": str(REFERENCE)}
    exec(REFERENCE.read_text(encoding="utf-8").split("def main()")[0], ns)
    apply = ns["apply"]
    # The reference's own grid is the one the constants describe.
    assert ns["L_AXIS"].tolist() == CAMERA_MATCH_L_AXIS
    assert ns["C_AXIS"].tolist() == CAMERA_MATCH_C_AXIS
    assert ns["H_AXIS"].tolist() == CAMERA_MATCH_H_AXIS
    pts = np.asarray(LAB_POINTS, float)[None]
    got = apply(pts, camera_match_table(BODY, "FL"))[0]
    assert np.allclose(got, FL_EXPECTED, atol=1e-4)
    assert np.allclose(apply(pts, camera_match_table(BODY, "SE"))[0][0], POOLED_EXPECTED_FIRST, atol=1e-4)
    assert np.allclose(apply(pts, camera_match_table(BODY, "IN"))[0][0], IN_EXPECTED_FIRST, atol=1e-4)


@requires_sample
def test_the_profile_carries_the_table_for_its_body_and_look() -> None:
    cal = calibration_for(SAMPLE_FL, "FL")
    assert cal is not None
    with_body = look_render_info(cal, "FL", body=BODY).to_json()
    assert with_body["cameraBody"] == BODY
    assert with_body["profileCameraMatch"] == camera_match_table(BODY, "FL")
    # No exif, no body, no table — never a default one.
    without = look_render_info(cal, "FL").to_json()
    assert without["cameraBody"] is None
    assert without["profileCameraMatch"] is None


@requires_sample
@requires_exiftool
def test_the_look_profile_request_re_keys_the_table_for_the_look_it_asks_for() -> None:
    """Switching looks is a look-profile request, not a decode, so the table
    has to follow the style through that path or the panel would keep FL's
    correction on an IN render."""
    own = daemon_look_profile({"input": str(SAMPLE_FL)}, SAMPLES)["colorProfile"]
    assert own["creativeLook"] == "FL"
    assert own["cameraBody"] == BODY
    assert own["profileCameraMatch"] == camera_match_table(BODY, "FL")
    switched = daemon_look_profile({"input": str(SAMPLE_FL), "style": "IN"}, SAMPLES)["colorProfile"]
    assert switched["creativeLook"] == "IN"
    assert switched["profileCameraMatch"] == camera_match_table(BODY, "IN")
