"""Per-short pipeline state, stored in <short>/state.json."""

from __future__ import annotations

import json
import os
import time
from datetime import UTC, datetime
from pathlib import Path

STAGES = ["interview", "story", "storyboard", "engine", "narration", "build", "checks", "preview", "final", "post"]
APPROVALS = {"story", "storyboard", "engine"}


def _path(short_dir: Path) -> Path:
    return Path(short_dir) / "state.json"


def _check(stage: str) -> None:
    if stage not in STAGES:
        raise ValueError(f"unknown stage {stage!r}; expected one of {STAGES}")


def load_state(short_dir: Path) -> dict:
    path = _path(short_dir)
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {"stages": {}}
    try:
        data = json.loads(text)
        if not isinstance(data, dict):
            raise TypeError("state must be a JSON object")
    except (ValueError, TypeError):
        stamp = time.strftime("%Y%m%d-%H%M%S")
        os.replace(path, path.with_name(f"state.corrupt-{stamp}.json"))
        return {"stages": {}}
    if not isinstance(data.get("stages"), dict):
        data["stages"] = {}
    return data


def _save(short_dir: Path, state: dict) -> None:
    path = _path(short_dir)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(state, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    os.replace(tmp, path)


def mark_done(short_dir: Path, stage: str, **info) -> None:
    _check(stage)
    state = load_state(short_dir)
    state["stages"][stage] = {"at": datetime.now(UTC).isoformat(timespec="seconds"), **info}
    _save(short_dir, state)


def next_stage(short_dir: Path) -> str | None:
    done = load_state(short_dir)["stages"]
    return next((s for s in STAGES if s not in done), None)


def reset_from(short_dir: Path, stage: str) -> None:
    _check(stage)
    state = load_state(short_dir)
    for s in STAGES[STAGES.index(stage) :]:
        state["stages"].pop(s, None)
    _save(short_dir, state)
