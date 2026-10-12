"""Whole-frame copies and casts, split into row bands across threads.

A 33 MP float32 frame is 400 MB, and numpy copies it on one thread: turning a
portrait decode upright took 0.35 s, its camera crop another 0.07 s, and the
float16 cast for the browser 0.16 s — each one a pass that does nothing but
move bytes. numpy releases the GIL inside copyto, so the same pass split into
row bands runs on every core at once.
"""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor

import numpy as np

_WORKERS = min(12, os.cpu_count() or 4)
_POOL = ThreadPoolExecutor(max_workers=_WORKERS, thread_name_prefix="fastcopy")
# Below this a thread hand-off costs more than the copy it saves.
_MIN_BYTES = 8 << 20


def contiguous(view: np.ndarray, dtype: np.dtype | type | None = None) -> np.ndarray:
    """A C-contiguous copy of `view`, cast to `dtype` if given (unsafe casting, as astype)."""
    out = np.empty(view.shape, dtype or view.dtype)
    rows = view.shape[0] if view.ndim else 0
    if out.nbytes < _MIN_BYTES or rows < 2:
        np.copyto(out, view, casting="unsafe")
        return out
    parts = min(rows, 2 * _WORKERS)
    edges = np.linspace(0, rows, parts + 1).astype(int)

    def band(i: int) -> None:
        a, b = edges[i], edges[i + 1]
        np.copyto(out[a:b], view[a:b], casting="unsafe")

    list(_POOL.map(band, range(parts)))
    return out
