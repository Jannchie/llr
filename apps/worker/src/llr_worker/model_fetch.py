"""Background download of pinned model files, resumable and stall-proof.

One file at a time into `<name>.part`, checked against its published size and
SHA-256 before it is renamed into place, so a file that exists is always the
one the code was run on. A connection that stops moving (the CDN does this
now and then) is dropped after STALL_SECONDS and resumed with a Range request,
up to RETRIES times — rather than hanging on a socket that will never deliver.
"""

from __future__ import annotations

import hashlib
import os
import sys
import threading
import time
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

STALL_SECONDS = 30
RETRIES = 8
_CHUNK = 1 << 20


@dataclass(frozen=True)
class ModelFile:
    name: str      # local file name
    repo: str      # Hugging Face repo
    revision: str  # pinned commit
    path: str      # path inside the repo
    size: int
    sha256: str
    url: str | None = None  # a pinned URL outside Hugging Face; repo/revision/path are then unused


@dataclass
class Fetcher:
    """Download state for one set of files, reported through `progress()`."""
    label: str
    locate: Callable[[], Path]  # the folder, looked up on each use (it follows the environment)
    files: tuple[ModelFile, ...]
    _state: dict[str, Any] = field(default_factory=lambda: {"running": False, "received": 0, "total": 0, "error": None})
    _lock: threading.Lock = field(default_factory=threading.Lock)

    @property
    def folder(self) -> Path:
        return self.locate()

    def missing(self) -> list[ModelFile]:
        return [f for f in self.files if not (self.folder / f.name).is_file()]

    def total(self) -> int:
        return sum(f.size for f in self.missing())

    def progress(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._state)

    def start(self) -> None:
        missing = self.missing()
        with self._lock:
            if not missing or self._state["running"]:
                return
            self._state.update(running=True, received=0, total=sum(f.size for f in missing), error=None)
        threading.Thread(target=self._run, args=(self.folder, missing), name=f"{self.label}-download", daemon=True).start()

    def _run(self, folder: Path, files: list[ModelFile]) -> None:
        error = None
        try:
            folder.mkdir(parents=True, exist_ok=True)
            for f in files:
                self._fetch(folder, f)
            sys.stderr.write(f"{self.label}: models downloaded to {folder}\n")
        except Exception as exc:
            sys.stderr.write(f"{self.label}: model download failed: {exc}\n")
            error = str(exc)
        with self._lock:
            self._state.update(running=False, error=error)

    def _add(self, n: int) -> None:
        with self._lock:
            self._state["received"] += n

    def _fetch(self, folder: Path, f: ModelFile) -> None:
        part = folder / f"{f.name}.part"
        endpoint = os.environ.get("HF_ENDPOINT", "https://huggingface.co").rstrip("/")
        url = f.url or f"{endpoint}/{f.repo}/resolve/{f.revision}/{f.path}"
        have = part.stat().st_size if part.is_file() else 0
        if have > f.size:
            part.unlink()
            have = 0
        self._add(have)
        last_error: Exception | None = None
        for attempt in range(RETRIES):
            if have >= f.size:
                break
            try:
                req = urllib.request.Request(url, headers={"Range": f"bytes={have}-"} if have else {})
                # The timeout bounds every socket read: a stalled transfer raises
                # instead of waiting forever, and the next attempt resumes.
                with urllib.request.urlopen(req, timeout=STALL_SECONDS) as res, part.open("ab" if have else "wb") as out:
                    if have and res.status != 206:   # range ignored: start over
                        out.truncate(0)
                        self._add(-have)
                        have = 0
                    while chunk := res.read(_CHUNK):
                        out.write(chunk)
                        have += len(chunk)
                        self._add(len(chunk))
            except (TimeoutError, OSError) as exc:
                last_error = exc
                sys.stderr.write(f"{self.label}: {f.name} stalled at {have} bytes ({exc}); resuming\n")
                time.sleep(min(2 ** attempt, 10))
        if have < f.size:
            raise RuntimeError(f"{f.name}: download incomplete ({have} of {f.size} bytes): {last_error}")
        digest = hashlib.sha256()
        with part.open("rb") as fh:
            while chunk := fh.read(_CHUNK):
                digest.update(chunk)
        if part.stat().st_size != f.size or digest.hexdigest() != f.sha256:
            part.unlink()
            raise RuntimeError(f"{f.name}: download does not match the published file")
        part.replace(folder / f.name)


def cache_root() -> Path:
    cache = os.environ.get("LLR_CACHE_DIR")
    return Path(cache) if cache else Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "llr"


def status(fetcher: Fetcher, loaded_provider: str | None, load_error: str | None) -> dict[str, Any]:
    """The erase-style status for a set of models: available, downloading, missing, or why not."""
    if loaded_provider:
        return {"available": True, "provider": loaded_provider}
    if fetcher.missing():
        p = fetcher.progress()
        if p["running"]:
            return {"available": False, "reason": "downloading", "modelDir": str(fetcher.folder),
                    "received": p["received"], "total": p["total"]}
        return {"available": False, "reason": "models-missing", "modelDir": str(fetcher.folder),
                "detail": p["error"], "total": fetcher.total()}
    try:
        import onnxruntime  # noqa: F401
    except ImportError:
        return {"available": False, "reason": "runtime-missing"}
    if load_error:
        return {"available": False, "reason": "load-failed", "detail": load_error}
    return {"available": True}
