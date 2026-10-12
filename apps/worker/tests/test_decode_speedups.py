"""The decode path's shortcuts give what the long way gave."""

from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from llr_worker import cli, fastcopy
from llr_worker.sony import linear_matrix_numba


def test_banded_copies_match_numpy() -> None:
    rng = np.random.default_rng(0)
    frame = rng.random((1201, 997, 3), dtype=np.float32)   # past the threading threshold
    for view in (np.rot90(frame, 1), np.rot90(frame, -1), np.rot90(frame, 2), frame[7:-5, 3:-11]):
        out = fastcopy.contiguous(view)
        assert out.flags.c_contiguous and np.array_equal(out, view)
    half = fastcopy.contiguous(frame, np.float16)
    assert half.dtype == np.float16 and np.array_equal(half, frame.astype(np.float16))
    small = rng.random((4, 5, 3), dtype=np.float32)
    assert np.array_equal(fastcopy.contiguous(small[:, ::-1]), small[:, ::-1])


def test_segment_kernel_post_matrix_matches_the_separate_passes() -> None:
    rng = np.random.default_rng(1)
    frame = rng.random((64, 48, 3), dtype=np.float32) * 2 - 0.2
    table = (np.eye(3, dtype=np.float32) + rng.normal(0, 0.2, (16, 3, 3))).astype(np.float32)
    lut = np.linspace(0, 15, 64, dtype=np.float32)
    post = np.array([[0.53, 0.33, 0.14], [0.10, 0.85, 0.05], [0.02, 0.10, 0.88]], np.float32)
    plain = linear_matrix_numba.apply(frame, table, lut)
    fused = linear_matrix_numba.apply(frame, table, lut, post=post)
    assert np.allclose(fused, np.clip(plain @ post.T, 0, None), rtol=1e-6, atol=1e-7)
    assert fused.min() == 0.0   # the negatives were clipped


def test_mosaic_snapshot_does_not_see_later_writes() -> None:
    raw = SimpleNamespace(raw_image=np.arange(12, dtype=np.uint16).reshape(3, 4), sizes="sizes")
    snap = cli._MosaicSnapshot(raw)
    raw.raw_image[:] = 0   # what the in-place denoiser does next
    assert snap.raw_image.sum() == np.arange(12).sum()
    assert snap.sizes == "sizes"


def test_exif_is_read_once_per_file_across_restarts(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("LLR_CACHE_DIR", str(tmp_path))
    runs: list[Path] = []

    def run(path: Path) -> dict[str, str]:
        runs.append(path)
        return {"Model": "ILCE-7CM2"}

    monkeypatch.setattr(cli, "_run_exiftool", run)
    read = cli._read_exiftool_metadata_cached.__wrapped__   # past the in-memory tier: a fresh daemon
    assert read("a.arw", 1, 2) == {"Model": "ILCE-7CM2"}
    assert read("a.arw", 1, 2) == {"Model": "ILCE-7CM2"}
    assert len(runs) == 1
    read("a.arw", 1, 3)   # the file changed
    assert len(runs) == 2
