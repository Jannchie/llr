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


class _FakeRaw:
    """Enough of a rawpy handle for _checkout_raw: a mutable mosaic and close()."""

    def __init__(self) -> None:
        self.raw_image = np.arange(12, dtype=np.uint16).reshape(3, 4)
        self.closed = False

    def close(self) -> None:
        self.closed = True


def test_a_checked_out_raw_handle_is_reused_with_its_mosaic_restored(tmp_path, monkeypatch) -> None:
    """Unpacking is the single most expensive step on a lossless-compressed
    frame, and the RAW-domain denoiser writes the mosaic in place: the cache
    must hand back the same unpacked handle and undo the previous request's
    writes before doing so."""
    from llr_worker import cli

    path = tmp_path / "a.ARW"
    path.write_bytes(b"x")
    opened: list[_FakeRaw] = []

    def fake_imread(_p: str) -> _FakeRaw:
        opened.append(_FakeRaw())
        return opened[-1]

    monkeypatch.setattr(cli.rawpy, "imread", fake_imread)
    monkeypatch.setattr(cli, "_RAW_HANDLE_CACHE", OrderedDict())
    first = cli._checkout_raw(path)
    first.raw_image[:] = 999                      # what denoise_raw_inplace does
    again = cli._checkout_raw(path)
    assert again is first and len(opened) == 1
    assert np.array_equal(again.raw_image, np.arange(12, dtype=np.uint16).reshape(3, 4))


def test_evicted_raw_handles_are_closed(tmp_path, monkeypatch) -> None:
    from llr_worker import cli

    opened: list[_FakeRaw] = []

    def fake_imread(_p: str) -> _FakeRaw:
        opened.append(_FakeRaw())
        return opened[-1]

    monkeypatch.setattr(cli.rawpy, "imread", fake_imread)
    monkeypatch.setattr(cli, "_RAW_HANDLE_CACHE", OrderedDict())
    monkeypatch.setattr(cli, "_RAW_HANDLE_CACHE_MAX", 2)
    for name in ("a", "b", "c"):
        p = tmp_path / f"{name}.ARW"
        p.write_bytes(b"x")
        cli._checkout_raw(p)
    assert [r.closed for r in opened] == [True, False, False]
