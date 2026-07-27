"""The pixel caches are bounded by bytes, not entry count.

A full-resolution preview entry runs from ~290 MB (24 MP) to ~730 MB (61 MP),
so a fixed entry count would either hold almost nothing or pin several GB
depending on which body is in use.
"""

from collections import OrderedDict
from typing import Any

import numpy as np

from llr_worker.cli import _remember_pixels


def _entry(mib: int) -> tuple[np.ndarray, str]:
    """A cache value shaped like the real ones: pixels first, then metadata."""
    return (np.empty(mib * (1 << 20), dtype=np.uint8), f"meta-{mib}")


def test_evicts_oldest_until_the_retained_entries_fit() -> None:
    cache: OrderedDict[Any, Any] = OrderedDict()
    for i in range(4):
        _remember_pixels(cache, f"k{i}", _entry(10), 25 * (1 << 20))
    # 25 MiB holds two 10 MiB entries, not three.
    assert list(cache) == ["k2", "k3"]


def test_keeps_the_newest_entry_even_when_it_alone_is_over_budget() -> None:
    cache: OrderedDict[Any, Any] = OrderedDict()
    _remember_pixels(cache, "small", _entry(1), 4 * (1 << 20))
    _remember_pixels(cache, "huge", _entry(40), 4 * (1 << 20))
    # Dropping it on arrival would mean re-decoding the RAW on every DCP,
    # denoise or profile change for that shot.
    assert list(cache) == ["huge"]


def test_a_repeat_insert_refreshes_recency_without_double_counting() -> None:
    cache: OrderedDict[Any, Any] = OrderedDict()
    _remember_pixels(cache, "a", _entry(10), 25 * (1 << 20))
    _remember_pixels(cache, "b", _entry(10), 25 * (1 << 20))
    _remember_pixels(cache, "a", _entry(10), 25 * (1 << 20))
    _remember_pixels(cache, "c", _entry(10), 25 * (1 << 20))
    # "a" was re-inserted, so "b" is the oldest and the one that goes.
    assert list(cache) == ["a", "c"]
