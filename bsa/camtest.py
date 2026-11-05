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


def build_stamp(short_dir: Path) -> str:
    """A fingerprint of the build inputs (freshness.build_inputs: build.py and the local modules it imports), their
    paths, sizes and contents: it changes when any of them is edited, added or dropped, and not when a copy (another
    drive, a file system with coarser times) only moves their times. "" without build.py."""
    import hashlib
    import json

    from bsa.freshness import build_inputs

    src = Path(short_dir) / "source"
    rows = []
    for f in build_inputs(short_dir):
        try:
            data = f.read_bytes()  # a few small scripts
        except OSError:
            continue
        rows.append([f.relative_to(src).as_posix() if f.is_relative_to(src) else f.name, len(data),
                     hashlib.sha256(data).hexdigest()])  # fmt: skip
    return hashlib.sha256(json.dumps(rows).encode("utf-8")).hexdigest()[:16] if rows else ""


def shot_spans(tl) -> dict[str, list[int]]:
    """Each shot's [f0, f1] in timeline `tl`: what decides the camera path of a shot (with the fps)."""
    return {s.id: [s.f0, s.f1] for s in tl.shots}


def record(blend: list | None, tl_hash: str, shots: list[str], scale: float, frames: int, *,
           spans: dict | None = None, fps: float | None = None, build: str | None = None) -> dict:  # fmt: skip
    """What review/camtest/camtest.json keeps: the .blend's [size, mtime_ns] (freshness.blend_stat) and the timing
    the clip was made from, the shots in it, the scale asked for, its percentage and its frames; and what decides
    the camera path: the tested shots' spans ({id: [f0, f1]}), the fps and the build inputs' stamp (build_stamp)."""
    rec = {"blend": list(blend) if blend is not None else None, "timeline": tl_hash, "shots": list(shots),
           "scale": scale, "percent": percent(scale), "frames": frames}  # fmt: skip
    if spans is not None:
        rec.update(spans={k: list(spans[k]) for k in shots}, fps=fps, build=build)
    return rec


def path_stale(rec: dict | None, spans: dict, fps: float, build: str, blend: list | None, tl_hash: str) -> str | None:
    """None when the clip in `rec` still shows the camera path: its shots' spans and the fps are the current ones
    (`spans` of the whole timeline now) and no build input changed since. Narration that only moves lines, and a
    .blend rebuilt from the same build.py, leave it current. A record without spans (older) falls back to stale()."""
    if not rec:
        return "no camera test yet"
    made = rec.get("spans")
    if not isinstance(made, dict) or "build" not in rec:
        return stale(rec, blend, tl_hash)
    if rec.get("fps") != fps or any(spans.get(k) != v for k, v in made.items()):
        return "the shot windows changed since the camera test"
    if rec.get("build") != build:
        return "build.py changed since the camera test"
    return None


def stale(rec: dict | None, blend: list | None, tl_hash: str) -> str | None:
    """None when the camera test in `rec` is from this timing and this .blend; else why it is out of date."""
    if not rec:
        return "no camera test yet"
    if rec.get("timeline") != tl_hash:
        return "the timing changed since the camera test"
    if blend is None or rec.get("blend") != list(blend):
        return "the scene changed since the camera test"
    return None
