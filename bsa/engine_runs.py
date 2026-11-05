"""The render engine per short and per run.

The approved engine (the `engine` stage's info) renders into `frames/<preset>/` and `out/<slug>[-preview].mp4`. A run
with another engine, or with a tag (`--engine eevee`, `--tag NAME`), is a side run: it renders into
`frames/<preset>-<tag>/` and `out/<slug>-<preset>-<tag>.mp4` (the tag defaults to the engine's name), keeps its own
frame-check findings, and never touches the approved run's frames, video or the engine stage.

Each render records the seconds per frame it measured in `review/engine-runs.json`, and each edit its MP4, so the
engines can be compared on what they really took on this short.
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path

ENGINES = ("cycles", "eevee")
RESERVED_TAGS = ("preview", "final")  # out/<slug>-preview.mp4 is the approved preview
TAG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,31}$")
RUNS_FILE = "engine-runs.json"


@dataclass(frozen=True)
class EngineRun:
    """The engine a run renders with, and its tag (None: the approved run, in the usual folders)."""

    engine: str | None
    tag: str | None = None

    @property
    def suffix(self) -> str:
        return f"-{self.tag}" if self.tag else ""

    def flags(self) -> str:
        """The command-line options that select this run again ("" for the approved run)."""
        if not self.tag:
            return ""
        return (f" --engine {self.engine}" if self.engine else "") + f" --tag {self.tag}"


def short_engine(short_dir: Path) -> str | None:
    """The engine short.json asks for (`"render": {"engine": ...}`), or None. Read raw: never fails."""
    try:
        data = json.loads((Path(short_dir) / "source" / "short.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    render = data.get("render") if isinstance(data, dict) else None
    engine = render.get("engine") if isinstance(render, dict) else None
    return engine if engine in ENGINES else None


def pick(stage_engine, short_engine_, settings_engine) -> tuple[str | None, str | None]:
    """(engine, where it came from): the engine stage's approved one, else short.json's, else the saved setting."""
    for engine, source in ((stage_engine, "engine stage"), (short_engine_, "short.json"),
                           (settings_engine, "settings")):  # fmt: skip
        if engine in ENGINES:
            return engine, source
    return None, None


def check_tag(tag: str) -> str:
    tag = str(tag).strip().lower()
    if not TAG_RE.match(tag):
        raise ValueError(f"--tag {tag!r}: use 1-32 lowercase letters, digits and hyphens (for example eevee-test)")
    if tag in RESERVED_TAGS:
        raise ValueError(f"--tag {tag!r} is the name of a preset: pick another (for example eevee)")
    return tag


def resolve(approved: str | None, engine: str | None = None, tag: str | None = None) -> EngineRun:
    """The run for `--engine` / `--tag`: no options, or the approved engine without a tag, is the approved run;
    another engine without a tag is tagged with the engine's name."""
    if engine is not None and engine not in ENGINES:
        raise ValueError(f"--engine {engine!r}: choose from {', '.join(ENGINES)}")
    use = engine or approved
    if tag is not None:
        return EngineRun(use, check_tag(tag))
    if engine is not None and engine != approved:
        return EngineRun(use, engine)
    return EngineRun(use)


def frames_root(short_dir: Path, preset: str, run: EngineRun) -> Path:
    return Path(short_dir) / "frames" / f"{preset}{run.suffix}"


def mp4_path(short_dir: Path, slug: str, preset: str, run: EngineRun) -> Path:
    if run.tag:
        name = f"{slug}-{preset}-{run.tag}.mp4"
    else:
        name = f"{slug}.mp4" if preset == "final" else f"{slug}-{preset}.mp4"
    return Path(short_dir) / "out" / name


def status_key(preset: str, run: EngineRun | None) -> str:
    """The name a run's live status goes under (bsa.render_status: review/render-status-<key>.json and the
    thumbnails render-status/<key>-<shot>.jpg): the preset for the approved run, as always; `<preset>-<tag>` for a
    side run, so it never touches the approved run's status or thumbnails."""
    return f"{preset}{run.suffix if run is not None else ''}"


def frame_checks_path(short_dir: Path, preset: str, run: EngineRun) -> Path:
    return Path(short_dir) / "review" / f"frame-checks-{preset}{run.suffix}.json"


def frame_check_cache_path(short_dir: Path, preset: str, run: EngineRun) -> Path:
    return Path(short_dir) / "review" / f"frame-check-cache-{preset}{run.suffix}.json"


# --------------------------------------------------------------------------- what each run measured


def runs_path(short_dir: Path) -> Path:
    return Path(short_dir) / "review" / RUNS_FILE


def _key(preset: str, run: EngineRun) -> str:
    return f"{preset}{run.suffix}"


def load_runs(short_dir: Path) -> dict:
    try:
        data = json.loads(runs_path(short_dir).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _save(short_dir: Path, data: dict) -> None:
    path = runs_path(short_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _entry(data: dict, short_dir: Path, preset: str, run: EngineRun) -> dict:
    row = data.get(_key(preset, run))
    row = dict(row) if isinstance(row, dict) else {}
    row.update(preset=preset, engine=run.engine, tag=run.tag,
               frames_dir=str(frames_root(short_dir, preset, run)))  # fmt: skip
    data[_key(preset, run)] = row
    return row


def record_render(short_dir: Path, preset: str, run: EngineRun, *, frames: int, seconds: float,
                  clock=time.time) -> dict | None:  # fmt: skip
    """Note what a render measured: `frames` rendered fresh in `seconds` of rendering. A render that rendered no
    frame (all kept) measured nothing and leaves the last measurement as it was."""
    if frames <= 0 or seconds <= 0:
        return None
    data = load_runs(short_dir)
    row = _entry(data, short_dir, preset, run)
    row.update(s_per_frame=round(seconds / frames, 3), frames_measured=int(frames), measured_at=clock())
    _save(short_dir, data)
    return row


def record_mp4(short_dir: Path, preset: str, run: EngineRun, mp4: Path, edit: dict | None = None) -> None:
    """Note the run's MP4 and, when given, what that edit was cut from (see bsa_blender._edit_record)."""
    data = load_runs(short_dir)
    row = _entry(data, short_dir, preset, run)
    row["mp4"] = str(mp4)
    if edit is not None:
        row["edit"] = edit
    _save(short_dir, data)


def edit_info(short_dir: Path, key: str) -> dict | None:
    """The edit record of run `key` ("final", "preview", "preview-eevee", ...); None when never recorded or of the
    wrong shape."""
    row = load_runs(short_dir).get(key)
    rec = row.get("edit") if isinstance(row, dict) else None
    return rec if isinstance(rec, dict) else None


def compare(short_dir: Path, preset: str | None = None) -> list[dict]:
    """Every run on record (for `preset`, or all): engine, tag, seconds per frame and frames measured, and its MP4
    while that file exists. Only what the runs measured; a run that measured nothing has no s_per_frame."""
    rows = []
    for row in load_runs(short_dir).values():
        if not isinstance(row, dict) or (preset is not None and row.get("preset") != preset):
            continue
        out = {k: row.get(k) for k in ("preset", "engine", "tag", "s_per_frame", "frames_measured")}
        out["approved"] = not row.get("tag")
        mp4 = row.get("mp4")
        out["mp4"] = mp4 if mp4 and Path(mp4).is_file() else None
        rows.append(out)
    return sorted(rows, key=lambda r: (str(r["preset"]), not r["approved"], str(r["tag"] or "")))
