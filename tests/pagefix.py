"""Fixture shorts for the review page tests: a pendulum short at any page stage, plus PNG and MP4 helpers."""

from __future__ import annotations

import shutil
import struct
import subprocess
import zlib
from pathlib import Path

import numpy as np

from bsa import jobstate, workspace
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


def make_short(workspace_dir: Path, stage: str, *, number: int = 1) -> Path:
    """A pendulum short (NN-pendulum) whose current page stage is `stage` ("done" = everything finished).

    Every page stage before it is marked done in state.json, with an applied approve decision for each approval
    stage."""
    if stage != "done" and stage not in stages.PAGE_STAGES:
        raise ValueError(f"unknown stage {stage!r}")
    sdir = workspace.new_short(Path(workspace_dir), "Pendulum", number)
    shutil.copyfile(SOURCE, sdir / "source" / "short.json")
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
    return sdir
