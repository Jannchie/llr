"""GPU inference in a child process that exits when idle.

DirectML keeps every byte of device memory a process ever allocated until the
process exits: releasing the ONNX session and collecting garbage gives nothing
back (measured: BiRefNet's 20.9 GB stayed at 20.9 GB after `del session`). A
model run inside the long-lived worker therefore pins its peak VRAM for the
worker's whole life, starving the browser's WebGL — which is what made the
editor stutter. So GPU models never run in the worker: they run here, in a
child started on demand and stopped after IDLE_SECONDS without a call, which
returns all of it.

Which models come here is decided by their measured peak (RTX 3090, DirectML):

    MoGe-2 ViT-S (relight geometry)   ~1.8 GB   GPU, here   0.2 s vs seconds on the CPU
    Moebius (generative erase)        ~6.6 GB   GPU, here   seconds vs ~4 min on the CPU
    ISNet (AI subject mask)           ~1.1 GB   CPU, in the worker (ai_masks.py): 0.5 s, once per photo
    BiRefNet-lite (subject, fallback) ~11-21 GB  CPU, in the worker: ~6 s, only when ISNet is unsure
    SegFormer-B3 (AI sky / people)       —      CPU: DirectML rejects the graph at run time

How long the child outlives its last call depends on what it holds: MoGe is
small and wanted again on every relight drag, so 60 s; once Moebius is loaded
its 6.6 GB is worth giving back quickly, so 10 s — the strokes of one request
run back to back, so that never reloads it mid-request.

`LLR_GPU=0` keeps everything on the CPU; `LLR_GPU_IDLE_SECONDS` overrides every
idle timeout. The protocol is length-prefixed pickles over the
child's stdin/stdout; the child's stderr is the worker's, so its log lines
land where the worker's do.
"""

from __future__ import annotations

import os
import pickle
import struct
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import IO, Any

# Seconds the child may sit idle while holding each engine; the shortest among those loaded applies.
IDLE_SECONDS = {"moge": 60.0, "moebius": 10.0}
_IDLE_OVERRIDE = os.environ.get("LLR_GPU_IDLE_SECONDS")


def idle_limit(loaded: set[str]) -> float:
    if _IDLE_OVERRIDE:
        return float(_IDLE_OVERRIDE)
    return min((IDLE_SECONDS.get(n, 60.0) for n in loaded), default=60.0)


def gpu_providers() -> list[str]:
    """The GPU execution providers to try, best first; empty when the GPU is off or absent."""
    if os.environ.get("LLR_GPU", "1") == "0":
        return []
    import onnxruntime as ort

    return [p for p in ("DmlExecutionProvider", "CUDAExecutionProvider", "CoreMLExecutionProvider")
            if p in ort.get_available_providers()]


def make_session(path: Path, gpu: bool = True) -> Any:
    """An ONNX session on the best GPU provider (when `gpu`), else the CPU.

    The runtime falls back to the CPU silently when the GPU provider rejects
    the graph. DirectML does that for some graphs once the optimiser has fused
    them (DmlGraphFusionHelper: E_INVALIDARG — Moebius's VAE encoder); the
    unfused graph runs, in ~55 ms against ~6.5 s on the CPU, so that is tried
    before settling for the CPU.
    """
    import onnxruntime as ort

    gpus = gpu_providers() if gpu else []
    providers = [*gpus, "CPUExecutionProvider"]
    opts = ort.SessionOptions()
    opts.log_severity_level = 3  # DirectML reports every CPU-placed shape op at warning level
    s = ort.InferenceSession(str(path), sess_options=opts, providers=providers)
    if gpus and s.get_providers()[0] != gpus[0]:
        opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_DISABLE_ALL
        s = ort.InferenceSession(str(path), sess_options=opts, providers=providers)
        if s.get_providers()[0] != gpus[0]:
            sys.stderr.write(f"gpu-host: {path.name} runs on the CPU, {gpus[0]} rejected it\n")
    return s


def _send(f: IO[bytes], obj: Any) -> None:
    data = pickle.dumps(obj, protocol=5)
    f.write(struct.pack("<Q", len(data)))
    f.write(data)
    f.flush()


def _read_exact(f: IO[bytes], n: int) -> bytes:
    chunks, got = [], 0
    while got < n:
        b = f.read(n - got)
        if not b:
            raise EOFError("GPU host closed its pipe")
        chunks.append(b)
        got += len(b)
    return b"".join(chunks)


def _recv(f: IO[bytes]) -> Any:
    (n,) = struct.unpack("<Q", _read_exact(f, 8))
    return pickle.loads(_read_exact(f, n))


def _kill_tree(proc: subprocess.Popen[bytes]) -> None:
    """Kill the child and whatever it started. On Windows a venv's python.exe is a
    launcher that runs the real interpreter as its own child — the process that
    holds the GPU memory — so killing the launcher alone would orphan it."""
    if sys.platform == "win32":
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True, check=False)
    else:
        proc.kill()
    proc.wait(timeout=5)


class _Host:
    """The child process, started on the first call and stopped when idle. One call at a time."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._proc: subprocess.Popen[bytes] | None = None
        self._last = 0.0
        self._loaded: set[str] = set()
        threading.Thread(target=self._reap, name="gpu-host-reaper", daemon=True).start()

    @property
    def running(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def call(self, engine: str, method: str, *args: Any) -> Any:
        with self._lock:
            if not self.running:
                self._start()
            assert self._proc is not None and self._proc.stdin and self._proc.stdout
            try:
                _send(self._proc.stdin, (engine, method, args))
                ok, value = _recv(self._proc.stdout)
                self._loaded.add(engine)
            except (OSError, EOFError) as error:
                self._stop()
                raise RuntimeError(f"GPU host exited during {engine}.{method}: {error}") from error
            self._last = time.monotonic()
        if not ok:
            raise RuntimeError(value)
        return value

    def stop(self) -> None:
        with self._lock:
            self._stop()

    def _start(self) -> None:
        flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        self._proc = subprocess.Popen(
            [sys.executable, "-m", "llr_worker.gpu_host"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=None, creationflags=flags,
        )
        self._last = time.monotonic()
        sys.stderr.write(f"gpu-host: started (pid {self._proc.pid})\n")

    def _stop(self) -> None:
        proc, self._proc = self._proc, None
        self._loaded.clear()
        if proc is None:
            return
        try:
            if proc.stdin:
                _send(proc.stdin, None)
            proc.wait(timeout=5)
        except Exception:
            _kill_tree(proc)
        sys.stderr.write("gpu-host: stopped, GPU memory released\n")

    def _reap(self) -> None:
        while True:
            time.sleep(5)
            with self._lock:
                limit = idle_limit(self._loaded)
                if self.running and time.monotonic() - self._last > limit:
                    sys.stderr.write(f"gpu-host: idle for {limit:.0f}s\n")
                    self._stop()


HOST = _Host()


class Remote:
    """An engine living in the GPU host: the same methods, forwarded. Loading happens on construction."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.provider: str = HOST.call(name, "__load__")

    def __getattr__(self, attr: str) -> Any:
        if attr.startswith("_"):
            raise AttributeError(attr)
        return lambda *args: HOST.call(self.name, attr, *args)


def _factory(name: str) -> Any:
    if name == "moge":
        from . import relight

        return relight._Engine(relight.FETCHER.folder / relight.MODEL.name)
    if name == "moebius":
        from . import erase

        return erase._Engine(erase.FETCHER.folder)
    raise ValueError(f"unknown GPU engine {name!r}")


def _serve() -> None:
    inp, out = sys.stdin.buffer, sys.stdout.buffer
    sys.stdout = sys.stderr  # nothing may print onto the reply channel
    engines: dict[str, Any] = {}
    while True:
        try:
            msg = _recv(inp)
        except EOFError:
            break
        if msg is None:
            break
        name, method, args = msg
        try:
            if name not in engines:
                engines[name] = _factory(name)
                sys.stderr.write(f"gpu-host: {name} loaded on {engines[name].provider}\n")
            eng = engines[name]
            if method == "__load__":
                value = eng.provider
            else:
                attr = getattr(eng, method)
                value = attr(*args) if callable(attr) else attr
            _send(out, (True, value))
        except Exception as error:
            _send(out, (False, f"{type(error).__name__}: {error}"))


if __name__ == "__main__":
    _serve()
