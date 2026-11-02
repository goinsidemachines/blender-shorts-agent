"""The review page's timeline data for one run: the shots, captions, sounds, findings, holds and closing card of the
edited video, all in frames of that video (1-based, inclusive, as in bsa/timeline.py)."""

from __future__ import annotations

import json
import math
from pathlib import Path

from bsa import engine_runs
from bsa.audio import events
from bsa.review import pages
from bsa.short import Short
from bsa.timeline import held_frames, timeline_from_json, timeline_hash

ROWS = ("shots", "captions", "sounds", "findings", "holds", "closing")


def _is_int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _video(sdir: Path, key: str) -> str | None:
    """The run's MP4 as `out/<name>.mp4`, None unless it is an existing file under out/."""
    row = engine_runs.load_runs(sdir).get(key)
    mp4 = row.get("mp4") if isinstance(row, dict) else None
    if not isinstance(mp4, str) or not mp4:
        return None
    try:
        rel = Path(mp4).resolve().relative_to((sdir / "out").resolve())
        ok = (sdir / "out" / rel).is_file()
    except (OSError, ValueError):
        return None
    return f"out/{rel.as_posix()}" if ok else None


def _ranges(frames: set[int]) -> list[tuple[int, int]]:
    out: list[list[int]] = []
    for f in sorted(frames):
        if out and f == out[-1][1] + 1:
            out[-1][1] = f
        else:
            out.append([f, f])
    return [(a, b) for a, b in out]


def _sounds(sdir: Path, short: Short, tl) -> tuple[list[dict], str | None]:
    spans = {s.id: s for s in tl.shots}
    problem = None
    sfx = list(short.sfx)
    try:
        sfx = events.to_sfx(events.load(events.path(sdir)), tl) + sfx
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as e:
        problem = f"source/events.json can't be used: {e}".splitlines()[0][:200]
    rows = []
    for fx in sfx:
        span = spans.get(fx.shot)
        if span is None:
            continue
        f = span.f0 + math.floor(round(fx.at_s * tl.fps, 6))
        rows.append({"f": f, "kind": fx.kind, "label": fx.kind.replace("_", " ")})
    return sorted(rows, key=lambda r: r["f"]), problem


def _finding_rows(sdir: Path, run_key: str, frames: int) -> tuple[list[dict], list[dict]]:
    review = sdir / "review"
    placed, unplaced, seen = [], [], set()
    for name in ("checks.json", f"frame-checks-{run_key}.json"):
        try:
            data = json.loads((review / name).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        rows = data.get("findings") if isinstance(data, dict) else None
        for raw in rows if isinstance(rows, list) else []:
            f = pages.ReviewFinding.from_row(raw)
            if f is None:
                continue
            fid = pages.finding_id(f)
            if fid in seen:
                continue
            seen.add(fid)
            row = {"id": fid, "f": f.frame, "severity": f.severity, "check": f.check, "text": f.message,
                   "shot": f.shots[0] if f.shots else None}  # fmt: skip
            if f.frame is not None and _is_int(raw.get("f1")):
                row["f1"] = raw["f1"]
            if f.frame is None:
                unplaced.append({**row, "why": "no frame"})
            elif not 1 <= f.frame <= frames:
                unplaced.append({**row, "why": f"frame {f.frame} is not in this video (frames 1 to {frames})"})
            else:
                placed.append(row)
    placed.sort(key=lambda r: (r["f"], r["id"]))
    return placed, unplaced


def build(sdir: Path, short: Short, run_key: str) -> dict | None:
    """Run `run_key`'s timeline block, or None when the run has no MP4 on disk. Raises when source/timeline.json
    can't be read."""
    sdir = Path(sdir)
    video = _video(sdir, run_key)
    if video is None:
        return None
    data = json.loads((sdir / "source" / "timeline.json").read_text(encoding="utf-8"))
    tl = timeline_from_json(data)
    edit = engine_runs.edit_info(sdir, run_key) or {}
    fps = edit.get("fps")
    if isinstance(fps, (int, float)) and not isinstance(fps, bool) and fps > 0:
        fps = int(fps) if fps == int(fps) else float(fps)
    else:
        fps = tl.fps
    frames = edit.get("frames") if _is_int(edit.get("frames")) and edit["frames"] > 0 else tl.n_frames
    closing = edit.get("closing")
    closing = ({"f0": closing["f0"], "f1": closing["f1"]}
               if isinstance(closing, dict) and _is_int(closing.get("f0")) and _is_int(closing.get("f1")) else None)  # fmt: skip
    titles = {s.id: s.title for s in short.shots}
    holds = []
    for shot in short.shots:
        span = next((s for s in tl.shots if s.id == shot.id), None)
        if span is not None:
            holds += [{"f0": a, "f1": b, "shot": shot.id} for a, b in _ranges(held_frames(shot, span, tl.fps))]
    sounds, problem = _sounds(sdir, short, tl)
    findings, unplaced = _finding_rows(sdir, run_key, frames)
    return {
        "run": run_key,
        "video": {"file": video},
        "fps": fps,
        "frames": frames,
        "duration_s": round(frames / fps, 3),
        "tracks": {
            "shots": [{"id": s.id, "f0": s.f0, "f1": s.f1, "title": titles.get(s.id, "")} for s in tl.shots],
            "captions": [{"f0": c.f0, "f1": c.f1, "text": c.text, "line": c.line_id} for c in tl.cards],
            "sounds": sounds,
            "findings": findings,
            "holds": holds,
            "closing": closing,
        },
        "unplaced": unplaced,
        "stale": edit.get("timeline_hash") != timeline_hash(data),
        "problem": problem,
    }
