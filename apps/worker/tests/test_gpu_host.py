"""The GPU host: idle policy, and a real child process round trip (no model needed)."""

import pytest

from llr_worker import gpu_host


def test_idle_limit_follows_the_heaviest_engine_loaded(monkeypatch):
    monkeypatch.setattr(gpu_host, "_IDLE_OVERRIDE", None)
    assert gpu_host.idle_limit(set()) == 60.0
    assert gpu_host.idle_limit({"moge"}) == 60.0
    # Moebius's 6.6 GB is given back sooner, whatever else is loaded.
    assert gpu_host.idle_limit({"moge", "moebius"}) == 10.0
    monkeypatch.setattr(gpu_host, "_IDLE_OVERRIDE", "3")
    assert gpu_host.idle_limit({"moge"}) == 3.0


def test_child_reports_errors_and_stops_cleanly():
    host = gpu_host._Host()
    try:
        # An unknown engine fails in the child; the error comes back as a RuntimeError
        # and the child stays up for the next call.
        with pytest.raises(RuntimeError, match="unknown GPU engine"):
            host.call("nope", "run")
        assert host.running
        with pytest.raises(RuntimeError, match="unknown GPU engine"):
            host.call("nope", "run")
    finally:
        host.stop()
    assert not host.running


def test_gpu_off_means_no_gpu_providers(monkeypatch):
    monkeypatch.setenv("LLR_GPU", "0")
    assert gpu_host.gpu_providers() == []
