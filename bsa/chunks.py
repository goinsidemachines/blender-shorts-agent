"""Rendering in chunks: a long render runs as a row of child Blender processes, each rendering at most `render.chunk_frames`
fresh frames, so memory a render leaks (Freestyle over many frames) is freed with each child and can never grow
unbounded. A crashed final resumes from the frames on disk. Standard library only; no bpy.

The parent command holds the GPU lock and the live status for the whole run. Each child is `render ... --max-frames N
--in-chunk`: it resumes from disk like any render (frames that are there and complete are kept), renders up to N fresh
frames, says whether more are left, and exits. The parent starts the next child until none is left.
"""

from __future__ import annotations

from pathlib import Path

DEFAULT = 250  # fresh frames per child process when render.chunk_frames is not set


def parse(value) -> int:
    """A `render.chunk_frames` value as stored: a whole number of frames, `0` (or `off`) for no chunks. ValueError for
    anything else."""
    if isinstance(value, str) and value.strip().lower() == "off":
        return 0
    if isinstance(value, bool) or (isinstance(value, float) and not value.is_integer()):
        n = -1
    else:
        try:
            n = int(value)
        except (TypeError, ValueError):
            n = -1
    if n < 0:
        raise ValueError(f"{value!r} is not a valid render.chunk_frames: use a number of frames per Blender process "
                         f"(e.g. {DEFAULT}), or 0 or off to render in one process")  # fmt: skip
    return n


def resolve(settings: dict) -> int:
    """The chunk size from the settings: render.chunk_frames, DEFAULT when unset or not valid. 0 = no chunks."""
    value = (settings.get("render") or {}).get("chunk_frames")
    if value is None:
        return DEFAULT
    try:
        return parse(value)
    except ValueError:
        return DEFAULT


def missing(folder: Path, f0: int, f1: int) -> list[int]:
    """The frames f0..f1 with no non-empty PNG in `folder`. (Whether a present frame is complete is the render's own
    check, bsa_blender._drop_damaged, which deletes a cut-off one before this is asked.)"""
    folder = Path(folder)
    out = []
    for f in range(f0, f1 + 1):
        try:
            if (folder / f"{f:04d}.png").stat().st_size > 0:
                continue
        except OSError:
            pass
        out.append(f)
    return out


def take(todo: list[int], budget: int) -> tuple[int, int, int]:
    """(first, last, how many) of the frames one child renders from the sorted missing frames `todo` with room for
    `budget` of them: the range from the first missing frame to the budget-th. Frames already there inside the range
    are skipped by the render itself, so the range may be longer than the count."""
    n = min(len(todo), max(budget, 0))
    if n <= 0:
        raise ValueError("nothing to take")
    return todo[0], todo[n - 1], n


def chunk_count(fresh: int, size: int) -> int:
    """How many child processes `fresh` frames need at `size` frames each (0 when none are needed or chunks are off)."""
    return -(-fresh // size) if size > 0 and fresh > 0 else 0


def wanted(fresh: int, size: int) -> bool:
    """Render in chunks? Only when chunks are on and more than one would be needed: a render that fits one chunk
    runs in this process, as it always did."""
    return chunk_count(fresh, size) > 1


def merge_shots(results: list[list[dict]]) -> list[dict]:
    """One row per shot from the shot rows every child returned, in first-seen order: the first child's `why`, `kept`
    and `frames`, with `rendered` summed over the children (a shot may be cut across two), `damaged` kept. A
    "partial" row is a shot the child stopped inside; the child that finishes it returns the full row."""
    order: list[str] = []
    rows: dict[str, dict] = {}
    for chunk in results:
        for row in chunk:
            sid = row["shot"]
            if sid not in rows:
                order.append(sid)
                rows[sid] = {k: v for k, v in row.items() if k != "partial"}
                continue
            first = rows[sid]
            first["rendered"] = first.get("rendered", 0) + row.get("rendered", 0)
            first["frames"] = max(first.get("frames", 0), row.get("frames", 0))
            if row.get("damaged") and not first.get("damaged"):
                first["damaged"] = row["damaged"]
    return [rows[s] for s in order]
