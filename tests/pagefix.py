"""Fixture shorts for the review page tests: a pendulum short at any page stage, plus PNG and MP4 helpers."""

from __future__ import annotations

import json
import os
import shutil
import struct
import subprocess
import zlib
from pathlib import Path

import numpy as np

from bsa import jobstate, render_status, workspace
from bsa.review import decisions, snapshot, stages

SOURCE = Path(__file__).resolve().parent / "e2e" / "pendulum" / "source" / "short.json"


def write_png(path: Path, rgb: np.ndarray) -> None:
    """Write an HxWx3 uint8 array as an 8-bit RGB PNG (stdlib zlib only)."""
    rgb = np.ascontiguousarray(rgb, dtype=np.uint8)
    h, w, _ = rgb.shape
    raw = b"".join(b"\x00" + rgb[y].tobytes() for y in range(h))

    def chunk(kind: bytes, data: bytes) -> bytes:
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)

    png = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(png)


def make_mp4(path: Path, seconds: float = 1.0) -> bool:
    """A small test-pattern MP4 (1080x1920 scaled to 270x480) made with ffmpeg; False when ffmpeg is missing."""
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        return False
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [ffmpeg, "-y", "-loglevel", "error", "-f", "lavfi", "-i", f"testsrc=size=1080x1920:rate=30:duration={seconds}",
           "-vf", "scale=270:480", "-pix_fmt", "yuv420p", str(path)]  # fmt: skip
    return subprocess.run(cmd, check=False, capture_output=True).returncode == 0 and path.exists()


SHOTS = ("s01", "s02", "s03")
FRAMES = 90  # a render-status fixture's frames, 30 a shot
REDO_ALL = "BLENDER render SHORT --preset preview --redo-flagged"


def write_render_status(sdir: Path, key: str, *, state: str = "done", done: int = FRAMES) -> Path:
    """review/render-status-<key>.json as the preview chain writes it (a run with its render section), `done` of
    FRAMES frames, plus a thumbnail per started shot. A running one names this process, so it reads as alive."""
    preset = key.split("-")[0]
    per = FRAMES // len(SHOTS)
    running = state == "running"
    pid = os.getpid()
    token = render_status.process_token(pid)
    t0 = 1_800_000_000.0
    shots = []
    for i, sid in enumerate(SHOTS):
        n = max(0, min(per, done - i * per))
        shot_state = "done" if n == per else "rendering" if n else "pending"
        thumb = render_status.thumb_rel(key, sid) if n else None
        if thumb:
            write_png(sdir / "review" / thumb, np.full((16, 9, 3), 60 * (i + 1), np.uint8))
        shots.append({"id": sid, "f0": i * per + 1, "f1": (i + 1) * per, "total": per, "done": n, "reused": 0,
                      "rendered": n, "stale": 0, "current_frame": i * per + n if n else None, "state": shot_state,
                      "thumb": thumb, "thumb_frame": i * per + n if n else None, "checked": 0, "warnings": 0,
                      "errors": 0, "reason": None})  # fmt: skip
    current = next((s for s in shots if s["state"] == "rendering"), None) if running else None
    render = {"version": render_status.VERSION, "short": {"slug": "pendulum"}, "preset": preset, "engine": "eevee",
              "state": state, "phase": "rendering" if running else state, "error": None, "pid": pid,
              "pid_token": token, "started": t0, "updated": t0 + 10, "finished": None if running else t0 + 20,
              "frames": {"total": FRAMES, "done": done, "reused": 0, "rendered": done, "stale": 0,
                         "to_render": FRAMES},
              "percent": round(100.0 * done / FRAMES, 2),
              "current": {"shot": current["id"], "frame": current["current_frame"]} if current else None,
              "eta_s": 120.0 if running else 0.0, "eta_source": "measured" if running else None,
              "shots": shots, "settings": {}, "checks": {"frames_checked": 0, "errors": 0, "warnings": 0,
                                                         "findings": []}}  # fmt: skip
    steps = []
    for name in render_status.CHAIN_STEPS:
        before = render_status.CHAIN_STEPS.index(name) < render_status.CHAIN_STEPS.index("render")
        st = "done" if not running or before else "running" if name == "render" else "pending"
        steps.append({"name": name, "state": st, "started": t0, "finished": None if st != "done" else t0 + 1,
                      "summary": None, "progress": None})  # fmt: skip
    status = {"version": render_status.RUN_VERSION, "short": {"slug": "pendulum"}, "preset": preset,
              "kind": "preview", "state": state, "phase": "render" if running else state, "error": None, "pid": pid,
              "pid_token": token, "run_id": f"{pid}-1", "started": t0, "updated": t0 + 10,
              "finished": None if running else t0 + 20, "run": {"kind": "preview",
              "current": "render" if running else None, "steps": steps}, "render": render}  # fmt: skip
    path = render_status.status_path(sdir, key)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(status, indent=1), encoding="utf-8")
    return path


def _write_checks(sdir: Path) -> None:
    """review/checks.json with a warning and the preview's frame checks with an error and its redo commands."""
    review = sdir / "review"
    checks = {"findings": [{"check": "lit", "severity": "warning", "message": "Bob is dark", "obj": "Bob",
                            "shot": "s01"}]}  # fmt: skip
    frames = {"findings": [{"check": "flicker", "severity": "error", "message": "frame 12 flickers", "frame": 12,
                            "shots": ["s02"], "redo": "BLENDER render SHORT --preset preview --frames 12"}],
              "redo_all": REDO_ALL}  # fmt: skip
    (review / "checks.json").write_text(json.dumps(checks), encoding="utf-8")
    (review / "frame-checks-preview.json").write_text(json.dumps(frames), encoding="utf-8")


def make_short(workspace_dir: Path, stage: str, *, number: int = 1, side_run: str | None = None) -> Path:
    """A pendulum short (NN-pendulum) whose current page stage is `stage` ("done" = everything finished).

    Every page stage before it is marked done in state.json, with an applied approve decision for each approval
    stage. From Render on it has the preview's render status (running while Render is current, else done) and,
    with `side_run`, a finished side run's (`preview-<side_run>`); after Render, the checks, the frame checks and
    the final MP4 (when ffmpeg is there). The post text comes with the short.json."""
    if stage != "done" and stage not in stages.PAGE_STAGES:
        raise ValueError(f"unknown stage {stage!r}")
    sdir = workspace.new_short(Path(workspace_dir), "Pendulum", number)
    shutil.copyfile(SOURCE, sdir / "source" / "short.json")
    for i in (1, 2, 3):  # a still per shot, each its own colour, and a render-time estimate
        write_png(sdir / "stills" / f"s0{i}.png", np.full((8, 8, 3), 40 * i, np.uint8))
    (sdir / "review").mkdir(exist_ok=True)
    (sdir / "review" / "estimate.json").write_text(
        json.dumps({"frames": 450, "eevee_s": 300.0, "cycles_s": 1200.0, "per_scene": []}), encoding="utf-8"
    )
    upto = len(stages.PAGE_STAGES) if stage == "done" else stages.PAGE_STAGES.index(stage)
    ctx = snapshot.load_ctx(sdir, {})
    for page_stage in stages.PAGE_STAGES[:upto]:
        for job_stage in stages.COVERS.get(page_stage, (page_stage,)):
            jobstate.mark_done(sdir, job_stage)
        if page_stage in stages.APPROVAL_STAGES:
            version = snapshot.stage_version(sdir, page_stage, ctx) or 1
            choice = {"engine": "eevee"} if page_stage == "engine" else None
            made = decisions.record(sdir, page_stage, version, "approve", "page", current=version, choice=choice)
            decisions.mark_applied(sdir, made["id"])
    if upto >= stages.PAGE_STAGES.index("render"):
        write_render_status(sdir, "preview", state="running" if stage == "render" else "done",
                            done=10 if stage == "render" else FRAMES)  # fmt: skip
        if side_run:
            write_render_status(sdir, f"preview-{side_run}", state="done")
    if upto > stages.PAGE_STAGES.index("render"):
        _write_checks(sdir)
        make_mp4(sdir / "out" / "pendulum.mp4")
    return sdir
