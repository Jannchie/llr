"""Relight geometry: camera recovery and the guided upsample, on synthetic scenes (no model)."""

import numpy as np

from llr_worker import relight


def _plane_points(width: int, height: int, focal: float, shift: float) -> np.ndarray:
    """A tilted plane seen by a pinhole of `focal` (view-plane units), depth then shifted by -shift."""
    sx, sy = relight.view_plane_span(width, height)
    u = ((np.arange(width) + 0.5) / width * 2 - 1) * sx
    v = ((np.arange(height) + 0.5) / height * 2 - 1) * sy
    uu, vv = np.meshgrid(u, v)
    z = 4.0 + 1.5 * vv + 0.5 * uu   # floor-like slope
    pts = np.stack([uu / focal * z, vv / focal * z, z - shift], -1)
    return pts.astype(np.float32)


def test_recover_focal_shift_finds_the_pinhole():
    pts = _plane_points(96, 64, focal=1.3, shift=2.0)
    focal, shift = relight.recover_focal_shift(pts, np.ones(pts.shape[:2], bool))
    assert abs(focal - 1.3) < 0.02
    assert abs(shift - 2.0) < 0.05


def test_estimate_maps_model_output_to_camera_space():
    class FakeEngine:
        def run(self, image):
            h, w = image.shape[:2]
            pts = _plane_points(w, h, focal=1.1, shift=1.0)
            normal = np.zeros((h, w, 3), np.float32)
            normal[..., 2] = -1
            return {"points": pts[None], "normal": normal[None], "mask": np.ones((1, h, w), np.float32),
                    "scale": np.array([2.0], np.float32)}

    frame = np.full((120, 180, 3), 0.18, np.float32)
    tex, meta = relight.estimate(frame, FakeEngine())
    assert tex.shape == (meta["height"], meta["width"], 4)
    # Depth is the shifted z times the metric scale; the median of 4 + slope is ~4 m, so ~8.
    assert 7.0 < float(np.median(tex[..., 3].astype(np.float32))) < 9.0
    # The ray slope across the frame is 2 * span / focal.
    sx, sy = relight.view_plane_span(meta["width"], meta["height"])
    assert np.allclose(meta["k"], [2 * sx / 1.1, 2 * sy / 1.1], rtol=0.03)
    assert np.allclose(tex[..., 2].astype(np.float32), -1, atol=1e-3)


def test_guided_upsample_snaps_to_the_guide_edge():
    guide = np.zeros((40, 40), np.float32)
    guide[:, 20:] = 1.0
    soft = np.clip((np.arange(40) - 14) / 12, 0, 1)[None].repeat(40, 0).astype(np.float32)
    out = relight.guided_upsample(guide, soft, r=4, eps=1e-4)
    # The ramp steepens toward a step at the guide's edge: the same span rises
    # further across the edge than it did in the input.
    assert out[20, 22] - out[20, 17] > 1.15 * (soft[20, 22] - soft[20, 17])
