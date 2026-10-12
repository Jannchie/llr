import json
import os

import numpy as np

from llr_worker import erase
from llr_worker.cli import _write_erased


class FakeEngine:
    """Fills with flat mid-grey and counts calls, standing in for the ONNX model."""

    def __init__(self) -> None:
        self.calls = 0

    def fill(self, image: np.ndarray, mask: np.ndarray, seed: int) -> np.ndarray:
        assert image.shape == (erase.MODEL_SIZE, erase.MODEL_SIZE, 3)
        assert mask.shape == (erase.MODEL_SIZE, erase.MODEL_SIZE) and mask.dtype == bool
        self.calls += 1
        return np.full_like(image, 0.5)


def frame(w: int = 600, h: int = 400) -> np.ndarray:
    rng = np.random.default_rng(1)
    return (rng.uniform(0.02, 0.6, (h, w, 3))).astype(np.float32)


def test_tone_map_round_trips_scene_values():
    x = np.linspace(0.0, 3.0, 300, dtype=np.float32)
    lin = np.stack([x, x * 0.8, x * 0.5], -1)[None]
    k = 0.18 / 0.2
    back = erase.from_display(erase.to_display(lin, k), k)
    # sRGB primaries are narrower than ProPhoto, so only in-gamut colours survive
    # exactly; these greys-ish ramps are inside it.
    assert np.allclose(back, lin, rtol=2e-3, atol=2e-4)


def test_parse_strokes_drops_malformed_input():
    good = {"points": [[0.1, 0.2], [0.3, 0.4]], "radius": 0.02}
    strokes = erase.parse_strokes([good, {"points": [], "radius": 0.1}, {"points": [[0, 0]], "radius": 0},
                                   {"points": [[float("nan"), 0]], "radius": 0.1}, "junk", {"radius": 0.1}])
    assert strokes == [erase.Stroke(((0.1, 0.2), (0.3, 0.4)), 0.02)]
    assert erase.parse_strokes(None) == []


def test_region_holds_the_stroke_and_stays_in_frame():
    s = erase.Stroke(((0.95, 0.1), (0.98, 0.15)), 0.01)
    x0, y0, x1, y1 = erase.region_for(s, 600, 400)
    assert 0 <= x0 < x1 <= 600 and 0 <= y0 < y1 <= 400
    assert x0 <= 0.95 * 600 - 6 and x1 >= 0.98 * 600 + 6
    assert y0 <= 0.1 * 400 - 6 and y1 >= 0.15 * 400 + 6


def test_fill_lands_on_the_stroke_only(tmp_path):
    f = frame()
    s = erase.Stroke(((0.5, 0.5),), 0.03)
    eng = FakeEngine()
    out = erase.apply_erase(f, [s], ("photo", 1), tmp_path, engine=eng)
    assert eng.calls == 1
    assert out is not f and np.array_equal(f, frame())  # input untouched
    k = erase.exposure_scale(f)
    grey = erase.from_display(np.full((1, 1, 3), 0.5, np.float32), k)[0, 0]
    assert np.allclose(out[200, 300], grey, rtol=0.05)  # centre of the stroke
    assert np.array_equal(out[20, 20], f[20, 20])        # far outside it
    assert np.array_equal(out[200, 380], f[200, 380])    # outside the stroke, inside the region


def test_fills_are_cached_and_survive_a_restart(tmp_path):
    s = [erase.Stroke(((0.3, 0.3),), 0.02), erase.Stroke(((0.7, 0.6),), 0.02)]
    eng = FakeEngine()
    first = erase.apply_erase(frame(), s, ("photo", 2), tmp_path, engine=eng)
    assert eng.calls == 2
    again = erase.apply_erase(frame(), s, ("photo", 2), tmp_path, engine=eng)
    assert eng.calls == 2 and np.array_equal(first, again)
    erase._PATCHES.clear()  # a new worker process: only the disk tier remains
    erase.apply_erase(frame(), s, ("photo", 2), tmp_path, engine=eng)
    assert eng.calls == 2
    # Another decode of the photo (say denoise toggled) is another set of fills.
    erase.apply_erase(frame(), s, ("photo", 3), tmp_path, engine=eng)
    assert eng.calls == 4


def test_a_fill_made_small_is_redone_for_a_larger_frame(tmp_path):
    s = [erase.Stroke(((0.5, 0.5),), 0.03)]
    eng = FakeEngine()
    erase.apply_erase(frame(300, 200), s, ("photo", 4), tmp_path, engine=eng)
    erase.apply_erase(frame(1200, 800), s, ("photo", 4), tmp_path, engine=eng)
    assert eng.calls == 2
    # Same size again: the larger fill serves.
    erase.apply_erase(frame(1200, 800), s, ("photo", 4), tmp_path, engine=eng)
    assert eng.calls == 2


def test_erased_response_never_writes_through_the_cache_link(tmp_path, monkeypatch):
    cached = tmp_path / "cache-abc.f16"
    base = frame(64, 48)
    base.astype(np.float16).tofile(cached)
    out = tmp_path / "linear-1.bin"
    os.link(cached, out)
    monkeypatch.setattr(erase, "_engine", lambda: FakeEngine())
    _, info = _write_erased(base, [erase.Stroke(((0.5, 0.5),), 0.1)], ("photo", 5), out)
    assert "error" not in info
    assert np.array_equal(np.fromfile(cached, np.float16), base.astype(np.float16).ravel())
    assert not np.array_equal(np.fromfile(out, np.float16), base.astype(np.float16).ravel())


def test_missing_model_leaves_the_frame_and_reports(tmp_path, monkeypatch):
    monkeypatch.setenv("LLR_ERASE_MODEL_DIR", str(tmp_path / "nowhere"))
    monkeypatch.setattr(erase, "_ENGINE", None)
    base = frame(64, 48)
    out = tmp_path / "linear-2.bin"
    _, info = _write_erased(base, [erase.Stroke(((0.5, 0.5),), 0.1)], ("photo", 6), out)
    assert "model files missing" in info["error"]
    assert np.array_equal(np.fromfile(out, np.float16), base.astype(np.float16).ravel())
    assert erase.status()["reason"] == "models-missing"


def _serve_models(monkeypatch, blobs: dict[str, bytes], published: dict[str, bytes] | None = None):
    """A local stand-in for the model host that honours Range, with the fetcher pinned to `published`."""
    import hashlib
    import http.server
    import threading

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            body = blobs[self.path.rsplit("/", 1)[-1]]
            start = int(self.headers["Range"][6:-1]) if self.headers.get("Range") else 0
            self.send_response(206 if start else 200)
            self.send_header("Content-Length", str(len(body) - start))
            self.end_headers()
            self.wfile.write(body[start:])

        def log_message(self, *args):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    monkeypatch.setenv("HF_ENDPOINT", f"http://127.0.0.1:{server.server_port}")
    truth = published or blobs
    from llr_worker.model_fetch import Fetcher, ModelFile

    files = tuple(ModelFile(n, "repo", "rev", n, len(b), hashlib.sha256(b).hexdigest()) for n, b in truth.items())
    monkeypatch.setattr(erase, "FETCHER", Fetcher("erase", erase.model_dir, files))
    return server


def _wait_download():
    import time

    for _ in range(500):
        if not erase.FETCHER.progress()["running"]:
            return
        time.sleep(0.01)
    raise AssertionError("download did not finish")


def test_download_fetches_missing_graphs_and_resumes(tmp_path, monkeypatch):
    blobs = {n: os.urandom(3_000_000 + i) for i, n in enumerate(erase.MODEL_FILES)}
    server = _serve_models(monkeypatch, blobs)
    folder = tmp_path / "models"
    monkeypatch.setenv("LLR_ERASE_MODEL_DIR", str(folder))
    monkeypatch.setattr(erase, "_ENGINE", None)
    folder.mkdir()
    (folder / "vae_decoder.onnx").write_bytes(blobs["vae_decoder.onnx"])            # already in place
    (folder / "vae_encoder.onnx.part").write_bytes(blobs["vae_encoder.onnx"][:1234])  # an interrupted fetch
    try:
        st = erase.start_download()
        assert st["reason"] == "downloading"
        assert st["total"] == len(blobs["moebius_unet.onnx"]) + len(blobs["vae_encoder.onnx"])
        _wait_download()
    finally:
        server.shutdown()
    for n, b in blobs.items():
        assert (folder / n).read_bytes() == b
    assert not list(folder.glob("*.part"))
    assert erase.status()["reason"] != "models-missing"


def test_download_rejects_a_file_that_does_not_match(tmp_path, monkeypatch):
    blobs = dict.fromkeys(erase.MODEL_FILES, b"x" * 1000)
    server = _serve_models(monkeypatch, blobs, published=dict.fromkeys(erase.MODEL_FILES, b"y" * 1000))
    folder = tmp_path / "models"
    monkeypatch.setenv("LLR_ERASE_MODEL_DIR", str(folder))
    monkeypatch.setattr(erase, "_ENGINE", None)
    try:
        erase.start_download()
        _wait_download()
    finally:
        server.shutdown()
    st = erase.status()
    assert st["reason"] == "models-missing" and "does not match" in st["detail"]
    assert not list(folder.iterdir())


def test_heal_copies_texture_and_matches_level():
    # A ramp with a bright blob in the middle: healing the blob must leave the
    # ramp's level there, with no model involved.
    h, w = 120, 160
    _, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    frame = np.repeat((0.1 + 0.002 * xx)[..., None], 3, -1).astype(np.float32)
    frame[55:65, 75:85] = 5.0
    stroke = erase.Stroke(((80 / w, 60 / h),), 9 / w, "heal")
    p = erase.make_heal_patch(frame, stroke)
    out = frame.copy()
    erase.apply_patch(out, p)
    assert out[60, 80, 0] < 0.5
    assert abs(out[60, 80, 0] - (0.1 + 0.002 * 80)) < 0.05


def test_heal_stroke_key_keeps_fill_keys():
    pts = ((0.5, 0.5),)
    assert "heal" not in erase.Stroke(pts, 0.01).key()
    assert erase.Stroke(pts, 0.01, "heal").key() != erase.Stroke(pts, 0.01).key()
    assert erase.parse_strokes([{"points": [[0.5, 0.5]], "radius": 0.01, "mode": "heal"}])[0].mode == "heal"


def test_stroke_with_more_lines_is_one_fill_over_all_of_them(tmp_path):
    s = erase.parse_strokes([{"points": [[0.3, 0.5]], "radius": 0.02,
                              "more": [{"points": [[0.7, 0.5]], "radius": 0.03}, {"points": [], "radius": 0.1}, "junk"]}])
    assert s == [erase.Stroke(((0.3, 0.5),), 0.02, "fill", ((((0.7, 0.5),), 0.03),))]
    x0, _, x1, _ = erase.region_for(s[0], 600, 400)
    assert x0 <= 0.3 * 600 - 12 and x1 >= 0.7 * 600 + 18
    mask = erase.stroke_mask(s[0], 600, 400, (0, 0, 600, 400))
    assert mask[200, 180] and mask[200, 420] and not mask[200, 300]
    eng = FakeEngine()
    erase.apply_erase(frame(), s, ("photo", 9), tmp_path, engine=eng)
    assert eng.calls == 1


def test_more_lines_change_the_key_but_a_single_line_keeps_its_own():
    pts = ((0.5, 0.5),)
    single = erase.Stroke(pts, 0.01)
    assert single.key() == json.dumps([[0.5, 0.5], 0.01])
    assert erase.Stroke(pts, 0.01, "fill", ((((0.6, 0.5),), 0.01),)).key() != single.key()


class TintedEngine(FakeEngine):
    """Gives back the model's input with a cast on it — a fill whose texture is
    right and whose colour is not, which is what the colour match is for."""

    def fill(self, image: np.ndarray, mask: np.ndarray, seed: int) -> np.ndarray:
        super().fill(image, mask, seed)
        return np.clip(image * np.array([1.25, 1.0, 0.8], np.float32), 0, 1)


def test_grow_and_feather_parse_clamp_and_key():
    base = {"points": [[0.5, 0.5]], "radius": 0.01}
    legacy, soft, clamped, junk = erase.parse_strokes([
        base, {**base, "grow": 0.2, "feather": 0.4}, {**base, "grow": 9, "feather": -1}, {**base, "grow": "x"}])
    # A stroke without the two keeps its fixed edge and its key.
    assert legacy.grow is None and legacy.feather is None and not legacy.soft
    assert legacy.key() == json.dumps([[0.5, 0.5], 0.01])
    assert (soft.grow, soft.feather) == (0.2, 0.4) and soft.key() != legacy.key()
    assert (clamped.grow, clamped.feather) == (erase.MAX_SOFT, 0.0)
    assert not junk.soft
    # Grow and feather widen the region the fill may touch.
    lx0, _, lx1, _ = erase.region_for(legacy, 600, 400)
    sx0, _, sx1, _ = erase.region_for(soft, 600, 400)
    assert sx1 - sx0 > lx1 - lx0


def test_stroke_mask_extra_dilates_by_a_disc():
    s = erase.Stroke(((0.5, 0.5),), 0.02)   # 12 px radius on 600 x 400
    plain = erase.stroke_mask(s, 600, 400, (0, 0, 600, 400))
    grown = erase.stroke_mask(s, 600, 400, (0, 0, 600, 400), 6.0)
    assert plain[200, 311] and not plain[200, 314]
    assert grown[200, 317] and not grown[200, 320]
    assert grown[200 + 12, 300 + 12] and not plain[200 + 12, 300 + 12]   # round, not square


def test_blur_keeps_flat_fields_and_mass():
    flat = np.full((40, 50), 0.7, np.float32)
    assert np.allclose(erase._blur(flat, 3.0), 0.7)
    spike = np.zeros((61, 61), np.float32)
    spike[30, 30] = 1.0
    b = erase._blur(spike, 4.0)
    assert np.isclose(b.sum(), 1.0, atol=1e-5)
    ys = np.mgrid[:61, :61][0]
    assert np.isclose(np.sqrt((b * (ys - 30) ** 2).sum()), 4.0, rtol=0.1)   # sigma as asked


def test_match_surroundings_takes_a_cast_off_the_fill():
    rng = np.random.default_rng(3)
    original = rng.uniform(0.1, 0.5, (80, 80, 3)).astype(np.float32)
    hole = np.zeros((80, 80), bool)
    hole[25:55, 25:55] = True
    fill = original * np.array([1.4, 1.0, 0.7], np.float32)
    out = erase.match_surroundings(fill, original, hole, 10.0)
    ratio = (out[hole] / original[hole]).mean(0)
    assert np.allclose(ratio, 1.0, atol=0.03)
    # The texture inside is the fill's own, only its level moves.
    assert np.corrcoef(out[hole][:, 1], fill[hole][:, 1])[0, 1] > 0.999


def test_soft_fill_is_opaque_over_the_grown_stroke_and_fades_past_it():
    f = frame()
    r = 0.03 * 600   # 18 px: grow 9 px, feather 18 px
    s = erase.parse_strokes([{"points": [[0.5, 0.5]], "radius": 0.03, "grow": 0.5, "feather": 1.0}])
    patch = erase.make_patch(f, s[0], erase.exposure_scale(f), 1, FakeEngine())
    out = f.copy()
    erase.apply_patch(out, patch)
    assert patch.alpha.max() == 1.0
    # Within brush + grow (27 px) the fill replaces the frame...
    assert not np.allclose(out[200, 300 + int(r * 1.4)], f[200, 300 + int(r * 1.4)], rtol=0.02)
    # ...past grow + feather (45 px) the frame is untouched.
    assert np.array_equal(out[200, 300 + int(r * 2.6)], f[200, 300 + int(r * 2.6)])


def test_soft_fill_matches_the_surrounding_colour(tmp_path):
    f = np.full((400, 600, 3), 0.2, np.float32)
    legacy = erase.Stroke(((0.5, 0.5),), 0.03)
    soft = erase.Stroke(((0.5, 0.5),), 0.03, grow=0.2, feather=0.5)
    k = erase.exposure_scale(f)
    for stroke, tol in ((legacy, None), (soft, 0.02)):
        out = f.copy()
        erase.apply_patch(out, erase.make_patch(f, stroke, k, 1, TintedEngine()))
        centre = out[200, 300] / f[200, 300]
        if tol is None:
            assert centre[0] > 1.1   # the cast lands as is
        else:
            assert np.allclose(centre, 1.0, atol=tol)
