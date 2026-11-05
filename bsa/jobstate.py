"""Per-short pipeline state, stored in <short>/state.json.

Every save is atomic (a synced temp file in the same folder replaces state.json) and keeps the copy it replaces as
state.json.bak. A damaged state.json (empty, zero bytes from a power cut, not JSON) is never reset or written over:
loading it raises StateDamaged, which names the backup and `CLI state SHORT restore`."""

from __future__ import annotations

import json
import os
import time
from datetime import UTC, datetime
from pathlib import Path

# Every stage a short can have. "camera" is optional: only a short that asks for it has it (wanted_stages).
STAGES = ["interview", "story", "storyboard", "camera", "engine", "narration", "build", "checks", "preview", "final",
          "post"]  # fmt: skip
APPROVALS = {"story", "storyboard", "camera", "engine"}
BACKUP = "state.json.bak"


class StateDamaged(Exception):
    """state.json can't be read as a state; the message says why and how to restore it (one plain line)."""


def _path(short_dir: Path) -> Path:
    return Path(short_dir) / "state.json"


def _check(stage: str) -> None:
    if stage not in STAGES:
        raise ValueError(f"unknown stage {stage!r}; expected one of {STAGES}")


def _parse(raw: bytes) -> tuple[dict | None, str | None]:
    """(state, None) for a usable state file's bytes, else (None, why it is damaged)."""
    if not raw:
        return None, "empty"
    if not raw.strip(b"\0"):
        return None, f"{len(raw)} zero bytes"
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None, "not valid JSON"
    if not isinstance(data, dict):
        return None, "not a JSON object"
    if not isinstance(data.get("stages"), dict):
        data["stages"] = {}
    return data, None


def _backup(short_dir: Path) -> tuple[dict, float] | None:
    """The backup's state and modification time, when it is usable."""
    path = Path(short_dir) / BACKUP
    try:
        raw, mtime = path.read_bytes(), path.stat().st_mtime
    except FileNotFoundError:
        return None
    data, why = _parse(raw)
    return None if why else (data, mtime)


def _damaged(short_dir: Path, why: str) -> StateDamaged:
    short_dir = Path(short_dir)
    path = _path(short_dir)
    bak = _backup(short_dir)
    if bak is None:
        return StateDamaged(f"{path} was damaged ({why}) and there is no backup ({BACKUP}): move it aside to start the "
                            f"short's stages over, then mark the stages already finished with CLI state "
                            f"{short_dir.name} done STAGE")  # fmt: skip
    data, mtime = bak
    when = time.strftime("%Y-%m-%d %H:%M", time.localtime(mtime))
    stages = ", ".join(data["stages"]) or "none"
    return StateDamaged(f"{path} was damaged ({why}); a backup from {when} exists ({BACKUP}, stages done: {stages}): "
                        f"restore it with CLI state {short_dir.name} restore")  # fmt: skip


def load_state(short_dir: Path) -> dict:
    """The short's state; {"stages": {}} for a new short. Raises StateDamaged when state.json is damaged (or gone
    while its backup is there)."""
    path = _path(short_dir)
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        if (Path(short_dir) / BACKUP).exists():
            raise _damaged(short_dir, "missing") from None
        return {"stages": {}}
    data, why = _parse(raw)
    if why:
        raise _damaged(short_dir, why)
    return data


def _fsync_dir(folder: Path) -> None:
    """Make the rename itself durable where the OS allows it (POSIX; Windows can't open a folder)."""
    if os.name == "nt":
        return
    try:
        fd = os.open(folder, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def write_atomic(path: Path, data: bytes, tries: int = 8) -> None:
    """Write data to a temp file next to path, fsync it, then replace path with it: a crash leaves the old file or
    the new one, never half of one. On Windows the replace fails while another program has path open: retry briefly."""
    path = Path(path)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with open(tmp, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        for i in range(tries):
            try:
                os.replace(tmp, path)
                break
            except PermissionError:
                if i == tries - 1:
                    raise
                time.sleep(0.01 * (i + 1))
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    _fsync_dir(path.parent)


def _save(short_dir: Path, state: dict) -> None:
    path = _path(short_dir)
    try:
        old = path.read_bytes()
    except FileNotFoundError:
        old = None
    if old is not None and _parse(old)[1] is None:  # the previous good copy
        write_atomic(path.with_name(BACKUP), old)
    write_atomic(path, json.dumps(state, indent=2, ensure_ascii=False, default=str).encode("utf-8"))


def restore(short_dir: Path) -> dict:
    """Put state.json.bak back as state.json; the damaged file is kept as state.damaged-<time>.json. Refuses when
    state.json is fine or there is no usable backup."""
    path = _path(short_dir)
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        raw = None
    if raw is not None and _parse(raw)[1] is None:
        raise StateDamaged(f"{path} is not damaged: nothing to restore (CLI state {Path(short_dir).name} reset STAGE "
                           "redoes stages)")  # fmt: skip
    bak = _backup(short_dir)
    if bak is None:
        raise _damaged(short_dir, "missing" if raw is None else _parse(raw)[1])
    if raw is not None:
        os.replace(path, path.with_name(f"state.damaged-{time.strftime('%Y%m%d-%H%M%S')}.json"))
    write_atomic(path, (Path(short_dir) / BACKUP).read_bytes())
    return load_state(short_dir)


def mark_done(short_dir: Path, stage: str, **info) -> None:
    _check(stage)
    state = load_state(short_dir)
    state["stages"][stage] = {"at": datetime.now(UTC).isoformat(timespec="seconds"), **info}
    _save(short_dir, state)


def wanted_stages(short_dir: Path) -> list[str]:
    """The stages of this short, in order: STAGES without "camera" unless source/short.json asks for the camera
    approval (a one-take short, or camera.approve). A short.json that can't be read has no camera stage."""
    from bsa.short import camera_stage_wanted, load_short

    try:
        wanted = camera_stage_wanted(load_short(Path(short_dir) / "source" / "short.json"))
    except Exception:  # noqa: BLE001 - missing, not JSON or of the wrong shape: the stages every short has
        wanted = False
    return [s for s in STAGES if wanted or s != "camera"]


def next_stage(short_dir: Path) -> str | None:
    done = load_state(short_dir)["stages"]
    return next((s for s in wanted_stages(short_dir) if s not in done), None)


def reset_from(short_dir: Path, stage: str) -> None:
    _check(stage)
    state = load_state(short_dir)
    for s in STAGES[STAGES.index(stage) :]:
        state["stages"].pop(s, None)
    _save(short_dir, state)
