"""The camera test (BLENDER camtest): a Workbench grey fly-through of the camera path, its contact sheet and the
record of what it was made from, in review/camtest/. A draft view of the camera, never a picture: every real render
uses the user's own engine. Pure: no bpy."""

from __future__ import annotations

from pathlib import Path

FOLDER = Path("review") / "camtest"
MP4 = "camtest.mp4"
SHEET = "camtest-sheet.png"
RECORD = "camtest.json"
SHEET_FRAMES = 12


def percent(scale: float) -> int:
    """`scale` (a fraction of 1080x1920) as a resolution percentage: the nearest multiple of 5 from 5 to 100, so
    both sides stay even (1080 * p / 100 and 1920 * p / 100), which H.264 needs. 0.33 is 35."""
    return min(100, max(5, 5 * round(scale * 100 / 5)))


def record(blend: list | None, tl_hash: str, shots: list[str], scale: float, frames: int) -> dict:
    """What review/camtest/camtest.json keeps: the .blend's [size, mtime_ns] (freshness.blend_stat) and the timing
    the clip was made from, the shots in it, the scale asked for, its percentage and its frames."""
    return {"blend": list(blend) if blend is not None else None, "timeline": tl_hash, "shots": list(shots),
            "scale": scale, "percent": percent(scale), "frames": frames}  # fmt: skip


def stale(rec: dict | None, blend: list | None, tl_hash: str) -> str | None:
    """None when the camera test in `rec` is from this timing and this .blend; else why it is out of date."""
    if not rec:
        return "no camera test yet"
    if rec.get("timeline") != tl_hash:
        return "the timing changed since the camera test"
    if blend is None or rec.get("blend") != list(blend):
        return "the scene changed since the camera test"
    return None
