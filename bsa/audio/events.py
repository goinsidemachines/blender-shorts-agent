"""Sound events from the build: `source/events.json`, written by build.py from the simulation, played by the mix.

Each event is {"kind", "t" or "frame", "shot", "gain", "dur_s", "level_db"}:

- `kind`: an sfx kind ("click", "thud", ...), played like an `sfx` entry (a cached recording of that kind, variants
  rotating; under natural-only the same rules).
- `t`: seconds, from the start of `shot` when it is given, else from frame 1 of the short; or `frame`: the short's
  1-based frame number (the build's own frame, exact).
- `shot`: optional; without it the event belongs to the shot whose frames hold it.
- `gain`: 0..1 (default 1), linear: a softer impact plays quieter (20 log10 gain dB below `level_db`). Events under
  FLOOR are left out.
- `dur_s` (default DUR_S) and `level_db` (default LEVEL_DB): as in `sfx`.

Events of one kind closer than MERGE_S are heard as one (their gains added, up to 1). The file is a list, or
{"events": [...]}; build.py writes it, nobody types it by hand.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

from bsa.audio.sfx import KINDS
from bsa.short import Sfx

DUR_S = 0.4
LEVEL_DB = -18.0
MERGE_S = 0.015
FLOOR = 0.03


def path(short_dir) -> Path:
    return Path(short_dir) / "source" / "events.json"


def load(p) -> list[dict]:
    """The events in `p` ([] when there is no file). ValueError for a file that is not a list of objects."""
    p = Path(p)
    if not p.is_file():
        return []
    data = json.loads(p.read_text(encoding="utf-8"))
    if isinstance(data, dict):
        data = data.get("events", [])
    if not isinstance(data, list) or not all(isinstance(e, dict) for e in data):
        raise ValueError(f"{p} must be a list of events ({{\"t\" or \"frame\", \"kind\", ...}}) or {{\"events\": [...]}}")
    return data


def _where(i: int, e: dict, tl) -> tuple[str, float]:
    """(shot id, seconds from that shot's first frame) of event `i`."""
    shots = {s.id: s for s in tl.shots}
    if "frame" in e:
        frame = float(e["frame"])
    elif "t" in e:
        base = shots[e["shot"]].f0 if e.get("shot") in shots else 1
        frame = base + float(e["t"]) * tl.fps
    else:
        raise ValueError(f"events[{i}] needs \"t\" (seconds) or \"frame\" (the short's frame)")
    if e.get("shot"):
        if e["shot"] not in shots:
            raise ValueError(f"events[{i}] refers to unknown shot {e['shot']!r}")
        span = shots[e["shot"]]
    else:
        span = next((s for s in tl.shots if s.f0 <= frame < s.f1 + 1), None)
        if span is None:
            raise ValueError(f"events[{i}] at frame {frame:g} is outside the video (frames 1-{tl.n_frames})")
    return span.id, round((frame - span.f0) / tl.fps, 4)


def to_sfx(evts: list[dict], tl, merge_s: float = MERGE_S, floor: float = FLOOR) -> list[Sfx]:
    """The events as `sfx` entries for the mix, in time order (close ones of a kind merged, quiet ones left out)."""
    rows = []
    for i, e in enumerate(evts):
        kind = e.get("kind")
        if kind not in KINDS:
            raise ValueError(f"events[{i}] has kind {kind!r}; valid kinds: {', '.join(sorted(KINDS))}")
        shot, at = _where(i, e, tl)
        start = next(s.f0 for s in tl.shots if s.id == shot)
        rows.append({"t": (start - 1) / tl.fps + at, "shot": shot, "at": at, "kind": kind,
                     "gain": float(e.get("gain", 1.0)), "dur": float(e.get("dur_s", DUR_S)),
                     "level": float(e.get("level_db", LEVEL_DB))})  # fmt: skip
    rows.sort(key=lambda r: r["t"])
    merged: list[dict] = []
    last: dict[str, dict] = {}
    for r in rows:
        prev = last.get(r["kind"])
        if prev is not None and r["t"] - prev["t"] < merge_s:
            prev["gain"] = min(1.0, prev["gain"] + r["gain"])
            continue
        merged.append(r)
        last[r["kind"]] = r
    return [Sfx(r["kind"], r["shot"], r["at"], r["dur"], r["level"] + 20 * math.log10(r["gain"]))
            for r in merged if r["gain"] >= floor]  # fmt: skip
