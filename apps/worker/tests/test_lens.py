"""Sony's per-shot lens correction tables, and the switches that gate them."""

from llr_worker.cli import sony_lens_corrections

# 10960725/DSC02976.ARW — FE 50-150mm F2 GM at 50mm, barrel.
DIST = "16 0 -3 -10 -22 -40 -61 -87 -118 -153 -192 -236 -285 -339 -396 -459 -527"
VIG = "16 0 96 320 640 1024 1440 1920 2432 3040 3936 4960 5984 7136 8480 10080 11456"
CA = ("32 0 0 0 128 128 128 128 256 256 256 256 256 256 256 256 128 "
      "512 512 512 384 384 384 384 384 256 256 256 256 384 384 384 512")

ON = {
    "DistortionCorrParams": DIST,
    "VignettingCorrParams": VIG,
    "ChromaticAberrationCorrParams": CA,
    "DistortionCorrection": "Auto",
    # What the body actually writes for this lens. Not "Off", so it reads as on
    # — see sony_lens_corrections on why that reading is unconfirmed here.
    "VignettingCorrection": "Unknown (3)",
    "ChromaticAberrationCorrection": "Auto",
}


def test_knots_start_at_the_centre_and_step_by_the_measured_span():
    """16 knots at i / 15.2, the first at r = 0 (where the value is always 0).

    Measured against in-camera JPEGs on 8 frames at 50..150 mm this spacing
    leaves <= 0.3 px radial residual; (i+0.5)/16 left up to 2.5 px at mid
    radius. See sony_lens_corrections.
    """
    out = sony_lens_corrections(ON)
    assert out is not None
    assert out["knots"] == [i / 15.2 for i in range(16)]
    assert out["knots"][0] == 0.0 and out["distortion"][0] == 1.0
    assert out["distortion"][-1] == -527 * 2**-14 + 1
    assert out["caR"] and out["caB"]


def test_distortion_off_leaves_the_frame_alone():
    """Parameters are written even when the body corrected nothing."""
    out = sony_lens_corrections({**ON, "DistortionCorrection": "Off"})
    assert out is not None
    assert out["distortion"] == [1.0] * 16
    assert out["vignetting"] != [1.0] * 16   # its own switch is still on


def test_each_correction_has_its_own_switch():
    off = sony_lens_corrections({
        **ON, "VignettingCorrection": "Off", "ChromaticAberrationCorrection": "Off",
    })
    assert off is not None
    assert off["vignetting"] == [1.0] * 16
    assert off["distortion"] != [1.0] * 16
    assert "caR" not in off


def test_missing_switch_reads_as_off():
    """A body that writes no switch wrote no correction either."""
    bare = sony_lens_corrections({"DistortionCorrParams": DIST, "VignettingCorrParams": VIG})
    assert bare is not None
    assert bare["distortion"] == [1.0] * 16
    assert bare["vignetting"] == [1.0] * 16


def test_no_params_at_all():
    assert sony_lens_corrections({}) is None
    assert sony_lens_corrections({"DistortionCorrParams": DIST}) is None
