"""One GPU job at a time per workspace: a lock file `<workspace>/.gpu.lock` that the heavy Blender commands (render,
the preview chain's render step, extremes, estimate) take, across sessions.

The file holds {"pid", "pid_token", "short", "command", "started"}. It is created atomically (O_CREAT | O_EXCL), so
two commands never both think they hold it. A second command waits, saying what it waits for ("waiting for
pendulum render (pid 1234, since 14:02:31)"), and takes over as soon as the holder ends or its process is gone (a
crashed or killed Blender leaves a stale lock, which is cleared). With `wait=False` it fails at once instead. The
same process may take the lock again while it holds it (the preview chain's render inside the chain).
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Callable
from contextlib import contextmanager
from pathlib import Path

NAME = ".gpu.lock"
POLL_S = 2.0
PARTIAL_S = 10.0  # an empty or unreadable lock file younger than this is being written: wait, don't clear it


class GpuBusy(Exception):
    """The GPU lock is held by another live process and the caller would not wait."""


def lock_path(workspace: Path) -> Path:
    return Path(workspace) / NAME


def read(path: Path) -> dict | None:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def describe_holder(holder: dict) -> str:
    """"pendulum render (pid 1234, since 14:02:31)"."""
    started = holder.get("started")
    since = (f", since {time.strftime('%H:%M:%S', time.localtime(started))}"
             if isinstance(started, (int, float)) else "")  # fmt: skip
    return f"{holder.get('short') or '?'} {holder.get('command') or '?'} (pid {holder.get('pid')}{since})"


def _alive(holder: dict) -> bool:
    from bsa.render_status import is_alive

    return is_alive(holder)


def _mine(holder: dict | None) -> bool:
    from bsa.render_status import process_token

    if not holder or holder.get("pid") != os.getpid():
        return False
    want, token = holder.get("pid_token"), process_token(os.getpid())
    return not (want and token and want != token)


def _create(path: Path, short: str, command: str, clock: Callable[[], float]) -> bool:
    from bsa.render_status import process_token

    try:
        fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
    except FileExistsError:
        return False
    body = {"pid": os.getpid(), "pid_token": process_token(os.getpid()), "short": short, "command": command,
            "started": clock()}  # fmt: skip
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(json.dumps(body))
    return True


def _clear_stale(path: Path, seen: dict | None) -> None:
    """Remove a lock judged stale, but only if it is still the one judged (another waiter may have replaced it)."""
    aside = path.with_name(f"{path.name}.stale-{os.getpid()}")
    try:
        os.replace(path, aside)
    except OSError:
        return
    if read(aside) != seen:  # someone else's fresh lock: put it back if the place is still free
        try:
            os.link(aside, path)
        except OSError:
            pass
    try:
        aside.unlink()
    except OSError:
        pass


def acquire(path: Path, *, short: str, command: str, wait: bool = True,
            on_wait: Callable[[str], object] | None = None, poll_s: float = POLL_S,
            sleep: Callable[[float], object] = time.sleep, clock: Callable[[], float] = time.time) -> bool:  # fmt: skip
    """Take the lock. Returns True when this call took it (release it after), False when this process already held
    it. Waits for a live holder, calling `on_wait("waiting for <holder>")` each time it looks (with `wait=False` it
    raises GpuBusy instead), and clears a lock whose process is gone."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    while True:
        if _create(path, short, command, clock):
            return True
        holder = read(path)
        if _mine(holder):
            return False
        if holder is None:
            try:
                young = time.time() - path.stat().st_mtime < PARTIAL_S
            except OSError:
                continue  # it was just released
            if not young:
                _clear_stale(path, None)
                continue
        elif not _alive(holder):
            _clear_stale(path, holder)
            continue
        text = f"waiting for {describe_holder(holder)}" if holder else "waiting for the GPU lock"
        if not wait:
            raise GpuBusy(f"the GPU is busy: {describe_holder(holder) if holder else 'another command'} holds "
                          f"{path}. Run it again when that ends, or without --no-wait to wait")  # fmt: skip
        if on_wait is not None:
            on_wait(text)
        sleep(poll_s)


def release(path: Path) -> None:
    """Remove the lock if this process holds it."""
    path = Path(path)
    if _mine(read(path)):
        try:
            path.unlink()
        except OSError:
            pass


@contextmanager
def held(path: Path, *, short: str, command: str, wait: bool = True,
         on_wait: Callable[[str], object] | None = None, **kw):  # fmt: skip
    """The lock for the duration of a block (re-entrant within one process)."""
    took = acquire(path, short=short, command=command, wait=wait, on_wait=on_wait, **kw)
    try:
        yield
    finally:
        if took:
            release(path)
