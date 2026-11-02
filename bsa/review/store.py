"""A small locked JSON store for the review page: notes, decisions and versions.

Every change goes through update_json: take an exclusive <path>.lock, read, apply, write atomically. Threads and
processes can write at once without losing an update. A damaged file (empty, not JSON) is never written over:
reading it raises StoreDamaged."""

from __future__ import annotations

import contextlib
import json
import os
import time
from collections.abc import Callable, Iterator
from pathlib import Path

from bsa import jobstate, render_status

STALE_S = 10.0


class StoreDamaged(Exception):
    """A JSON store file can't be read; the message names the file and why (one plain line)."""


def read_json(path: Path, default):
    """The file's JSON; `default` when it is missing. Raises StoreDamaged when it is empty or not valid JSON."""
    path = Path(path)
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return default
    if not raw.strip(b"\0 \t\r\n"):
        raise StoreDamaged(f"{path} is damaged (empty): move it aside to start it over")
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise StoreDamaged(f"{path} is damaged (not valid JSON): move it aside to start it over") from None


def _holder(lock: Path) -> tuple[bytes | None, bool]:
    """The lock file's raw body and whether it is stale: older than STALE_S and its owner is not running."""
    try:
        raw = lock.read_bytes()
        age = time.time() - lock.stat().st_mtime
    except OSError:
        return None, False
    if age <= STALE_S:
        return raw, False
    try:
        pid = json.loads(raw.decode("utf-8")).get("pid")
    except (UnicodeDecodeError, ValueError, AttributeError):
        pid = None
    return raw, render_status.process_token(pid) is None


def _release(lock: Path, tries: int = 200) -> None:
    """Delete the lock. On Windows this fails while a waiting writer has the file open to look at it: retry."""
    for i in range(tries):
        try:
            lock.unlink(missing_ok=True)
            return
        except PermissionError:
            if i == tries - 1:
                raise
            time.sleep(0.005)


@contextlib.contextmanager
def locked(path: Path, timeout_s: float = 5.0) -> Iterator[None]:
    """Hold an exclusive <path>.lock (created with O_CREAT|O_EXCL). A lock older than 10 s whose pid is dead is
    broken. Raises TimeoutError when the lock stays held for timeout_s."""
    lock = Path(str(path) + ".lock")
    deadline = time.monotonic() + timeout_s
    while True:
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except (FileExistsError, PermissionError):  # PermissionError: Windows, while the lock is being deleted
            raw, stale = _holder(lock)
            if stale:
                with contextlib.suppress(OSError):
                    if lock.read_bytes() == raw:  # still the same dead holder's lock
                        _release(lock, tries=5)
                continue
            if time.monotonic() >= deadline:
                raise TimeoutError(f"{lock} is held by another writer; waited {timeout_s:g}s") from None
            time.sleep(0.005)
            continue
        break
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(json.dumps({"pid": os.getpid(), "at": time.time()}).encode("utf-8"))
        yield
    finally:
        _release(lock)


def update_json(path: Path, default, fn: Callable[[object], object]):
    """Lock, read (default when missing), apply fn, write atomically, and return the new value."""
    path = Path(path)
    with locked(path):
        new = fn(read_json(path, default))
        jobstate.write_atomic(path, json.dumps(new, indent=2, ensure_ascii=False).encode("utf-8"))
        return new
