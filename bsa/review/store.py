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
MAX_HOLD_S = 60.0  # no critical section holds a lock this long: an older lock is left over from a crash


class StoreDamaged(Exception):
    """A JSON store file can't be read; the message names the file and why (one plain line)."""


def read_json(path: Path, default, expect: type | None = None):
    """The file's JSON; `default` when it is missing. Raises StoreDamaged when it is empty or not valid JSON, or
    (with `expect` list or dict) when it is valid JSON of another type, or a list holding anything but objects."""
    path = Path(path)
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return default
    if not raw.strip(b"\0 \t\r\n"):
        raise StoreDamaged(f"{path} is damaged (empty): move it aside to start it over")
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise StoreDamaged(f"{path} is damaged (not valid JSON): move it aside to start it over") from None
    if expect is not None and (
        not isinstance(data, expect) or (expect is list and not all(isinstance(x, dict) for x in data))
    ):
        what = "a list of objects" if expect is list else "an object"
        raise StoreDamaged(f"{path} is damaged (not {what}): move it aside to start it over")
    return data


def _holder(lock: Path) -> tuple[bytes | None, bool]:
    """The lock file's raw body and whether it is stale: older than MAX_HOLD_S; or its pid is now another process
    (the process token it wrote differs: a pid reused after a reboot); or older than STALE_S with its owner gone."""
    try:
        raw = lock.read_bytes()
        age = time.time() - lock.stat().st_mtime
    except OSError:
        return None, False
    if age > MAX_HOLD_S:
        return raw, True
    try:
        held = json.loads(raw.decode("utf-8"))
        pid, want = held.get("pid"), held.get("process_token")
    except (UnicodeDecodeError, ValueError, AttributeError):
        pid = want = None
    token = render_status.process_token(pid) if pid is not None else None
    if want and token and token != want:
        return raw, True
    if age <= STALE_S:
        return raw, False
    return raw, token is None


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
    """Hold an exclusive <path>.lock (created with O_CREAT|O_EXCL) naming this pid and its process token. A lock left
    by a crash is broken (see _holder). Raises TimeoutError when the lock stays held for timeout_s."""
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
            pid = os.getpid()
            body = {"pid": pid, "process_token": render_status.process_token(pid) or "", "at": time.time()}
            f.write(json.dumps(body).encode("utf-8"))
        yield
    finally:
        _release(lock)


def update_json(path: Path, default, fn: Callable[[object], object], expect: type | None = None):
    """Lock, read (default when missing; `expect` as for read_json), apply fn, write atomically, and return the new
    value."""
    path = Path(path)
    with locked(path):
        new = fn(read_json(path, default, expect))
        jobstate.write_atomic(path, json.dumps(new, indent=2, ensure_ascii=False).encode("utf-8"))
        return new
