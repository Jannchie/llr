"""Shared pieces of the per-decode analyses (relight geometry, AI masks, erase fills).

Float resizing, the guided filter that moves a low-resolution model output onto
the photo's edges, and the two-tier cache (memory, then an .npz beside the
source) every analysis keeps its results in.
"""

from __future__ import annotations

import hashlib
import os
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any, Generic, TypeVar

import numpy as np
from PIL import Image

LUMA_709 = np.array([0.2126, 0.7152, 0.0722], np.float32)

T = TypeVar("T")


def resize(arr: np.ndarray, size: tuple[int, int], resample: int = Image.Resampling.BILINEAR) -> np.ndarray:
    """Float resize, channel by channel (Pillow's 'F' mode keeps the precision)."""
    if arr.ndim == 2:
        return np.asarray(Image.fromarray(arr.astype(np.float32), "F").resize(size, resample))
    return np.stack([resize(arr[..., c], size, resample) for c in range(arr.shape[2])], -1)


def fit(width: int, height: int, long: int) -> tuple[int, int]:
    """(width, height) scaled so the long edge is at most `long`."""
    f = min(1.0, long / max(width, height))
    return max(1, round(width * f)), max(1, round(height * f))


def _box(a: np.ndarray, r: int) -> np.ndarray:
    """Mean over a (2r+1)^2 window, edges clamped, via cumulative sums (any trailing channels)."""
    pad = np.pad(a, ((r + 1, r), (r + 1, r)) + ((0, 0),) * (a.ndim - 2), mode="edge")
    c = pad.cumsum(0).cumsum(1)
    n = 2 * r + 1
    return (c[n:, n:] - c[:-n, n:] - c[n:, :-n] + c[:-n, :-n]) / (n * n)


def guided_upsample(guide: np.ndarray, src: np.ndarray, r: int, eps: float) -> np.ndarray:
    """He et al.'s guided filter: `src` (already at the guide's size) refit to the guide's edges."""
    g = guide.astype(np.float64)
    p = src.astype(np.float64)
    g3 = g[..., None] if p.ndim == 3 else g
    mg = _box(g, r)
    vg = _box(g * g, r) - mg * mg
    mp = _box(p, r)
    mg3 = mg[..., None] if p.ndim == 3 else mg
    vg3 = vg[..., None] if p.ndim == 3 else vg
    cov = _box(g3 * p, r) - mg3 * mp
    a = cov / (vg3 + eps)
    b = mp - a * mg3
    return (_box(a, r) * g3 + _box(b, r)).astype(np.float32)


class AnalysisCache(Generic[T]):
    """Results by key: the last few in memory, all of them as `<prefix>-<digest>.npz` in the cache dir."""

    def __init__(self, prefix: str, pack: Callable[[T], dict[str, Any]], unpack: Callable[[Any], T],
                 size: int = 8) -> None:
        self.prefix, self.pack, self.unpack, self.size = prefix, pack, unpack, size
        self._mem: dict[str, T] = {}
        self._lock = threading.Lock()

    def get(self, key: str, compute: Callable[[], T], cache_dir: Path | None) -> T:
        digest = hashlib.sha1(key.encode()).hexdigest()[:24]
        with self._lock:
            hit = self._mem.get(digest)
        if hit is not None:
            return hit
        path = cache_dir / f"{self.prefix}-{digest}.npz" if cache_dir is not None else None
        if path is not None and path.is_file():
            try:
                with np.load(path, allow_pickle=False) as z:
                    hit = self.unpack(z)
            except (OSError, ValueError, KeyError):
                hit = None
        if hit is None:
            hit = compute()
            if path is not None:
                tmp = path.with_suffix(".tmp.npz")
                try:
                    np.savez(tmp, **self.pack(hit))
                    os.replace(tmp, path)
                except OSError:
                    tmp.unlink(missing_ok=True)
        with self._lock:
            self._mem[digest] = hit
            while len(self._mem) > self.size:
                self._mem.pop(next(iter(self._mem)))
        return hit
