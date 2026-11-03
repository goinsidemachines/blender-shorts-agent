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

from bsa import engine_runs, jobstate, render_status, workspace
from bsa.narrate.run import HEARD_FILE
from bsa.review import decisions, snapshot, stages, worddiff
from bsa.short import load_short, spoken_text
from bsa.timeline import Card, Span, Timeline, build_timeline, timeline_hash, to_json

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


COUNTER_STEP = 7 * 255 / 219  # the full-range grey between two frames of a counter video


def make_counter_mp4(path: Path, frames: int = 30) -> bool:
    """A 270x480 30 fps H.264 MP4 whose frame n (1-based) is a flat grey, (n-1)*7*255/219 in full range, so the
    frame on screen can be read back from one pixel (counter_frame). False when ffmpeg is missing."""
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        return False
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [ffmpeg, "-y", "-loglevel", "error", "-f", "lavfi", "-i", "color=gray:s=270x480:r=30",
           "-vf", "format=yuv420p,geq=lum='16+N*7':cb=128:cr=128", "-frames:v", str(frames),
           "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "10", str(path)]  # fmt: skip
    return subprocess.run(cmd, check=False, capture_output=True).returncode == 0 and path.exists()


def counter_frame(grey: float) -> int:
    """The frame (1-based) of a counter video (make_counter_mp4) whose pixel reads `grey` (0-255)."""
    return round(grey / COUNTER_STEP) + 1


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
    for key in ("preview", "final"):  # the final's own file too: a run's timeline reads only its own
        (review / f"frame-checks-{key}.json").write_text(json.dumps(frames), encoding="utf-8")


def write_timeline(sdir: Path, frames: int | None = None) -> dict:
    """source/timeline.json: the short at reading pace or, with `frames`, a synthetic timeline of that length (the
    short's shots in equal parts, a caption card every 10 frames). Returns the JSON."""
    short = load_short(sdir / "source" / "short.json")
    if frames is None:
        data = to_json(build_timeline(short, None))
    else:
        n = len(short.shots)
        bounds = [round(frames * i / n) for i in range(n + 1)]
        shots, lines, cards = [], [], []
        for i, shot in enumerate(short.shots):
            f0, f1 = bounds[i] + 1, bounds[i + 1]
            shots.append(Span(shot.id, f0, f1))
            lines.append(Span(f"l{i + 1:02d}", f0, f1))
            cards += [Card(f"caption {c}", f"l{i + 1:02d}", c, min(c + 9, f1)) for c in range(f0, f1 + 1, 10)]
        data = to_json(Timeline(short.fps, frames, shots, lines, cards))
    path = sdir / "source" / "timeline.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=1), encoding="utf-8")
    return data


def record_edit(sdir: Path, key: str, *, stale: bool = False, closing: int = 0) -> None:
    """An engine-runs.json row for run `key` with its MP4 (made when missing) and an `edit` matching the current
    source/timeline.json, or, with `stale`, another timing. `closing` frames of closing card follow the story."""
    data = json.loads((sdir / "source" / "timeline.json").read_text(encoding="utf-8"))
    n = data["n_frames"]
    slug = load_short(sdir / "source" / "short.json").slug
    mp4 = sdir / "out" / (f"{slug}.mp4" if key == "final" else f"{slug}-{key}.mp4")
    if not mp4.is_file() and not make_mp4(mp4) and key != "final":  # the final's file is make_short's to make
        mp4.parent.mkdir(parents=True, exist_ok=True)
        mp4.write_bytes(bytes(2000))  # no ffmpeg: a stand-in file, enough for the snapshot (never played)
    edit = {"timeline_hash": "0" * 16 if stale else timeline_hash(data), "fps": float(data["fps"]),
            "frames": n + closing, "closing": {"f0": n + 1, "f1": n + closing} if closing else None}  # fmt: skip
    runs = engine_runs.load_runs(sdir)
    row = runs.get(key) if isinstance(runs.get(key), dict) else {}
    row.update(preset=key.split("-")[0], mp4=str(mp4), edit=edit)
    runs[key] = row
    path = engine_runs.runs_path(sdir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(runs, indent=2), encoding="utf-8")


def make_short(workspace_dir: Path, stage: str, *, number: int = 1, side_run: str | None = None) -> Path:
    """A pendulum short (NN-pendulum) whose current page stage is `stage` ("done" = everything finished).

    Every page stage before it is marked done in state.json, with an applied approve decision for each approval
    stage. From Render on it has the preview's render status (running while Render is current, else done) and,
    with `side_run`, a finished side run's (`preview-<side_run>`), on a 30-frame timeline; after Render, the checks,
    the frame checks and the final MP4 (a 30-frame counter video, when ffmpeg is there). The post text comes with
    the short.json."""
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
        write_timeline(sdir, frames=30)  # the final's counter video is 30 frames: the flicker at 12 sits on it
        record_edit(sdir, "preview")
    if upto > stages.PAGE_STAGES.index("render"):
        _write_checks(sdir)
        make_counter_mp4(sdir / "out" / "pendulum.mp4")
        record_edit(sdir, "final")
    return sdir


LOOK_SHOTS = ("s01", "s02", "s03")  # the look preview's first, middle and last shot


def write_look(sdir: Path, style: str = "toy", set_: str = "workbench", *, built: str = "toon", seconds: float = 2.5,
               first: bool = True, no_estimates: bool = False, warnings: list | None = None,
               swatches: list | None = None) -> dict:
    """stills/look/<shot>.jpg (PNG bytes: a flat colour that follows the style and set), stills/look/kit.png and
    stills/look.json as `BLENDER look` writes them, built as `built` on the workbench, its swatches (by default) those
    of the made-up kit (tests/kitfix.py) with the short's own style on top. Returns the JSON."""
    if swatches is None:
        import kitfix

        from bsa import kit, looks

        swatches = looks.swatches(kit.kit_for({"brand": kitfix.KIT}, load_short(Path(sdir) / "source" / "short.json")))
    look = Path(sdir) / "stills" / "look"
    tone = sum(map(ord, style + set_)) % 180
    for i, sid in enumerate(LOOK_SHOTS):
        write_png(look / f"{sid}.jpg", np.full((16, 9, 3), (tone + 20 * i, 120, 200 - tone // 2), np.uint8))
    write_png(look / "kit.png", np.full((9, 16, 3), (234, 242, 248), np.uint8))
    data = {"style": style, "set": set_, "built": {"style": built, "set": "workbench"},
            "frames": [{"shot": sid, "frame": 10 * (i + 1), "file": f"look/{sid}.jpg"}
                       for i, sid in enumerate(LOOK_SHOTS)],
            "sheet": "look/kit.png", "swatches": swatches, "engine": "eevee", "seconds_per_frame": seconds,
            "estimates": None if no_estimates else {"eevee_s": 300.0, "cycles_s": 1200.0},
            "first": first, "warnings": list(warnings or [])}  # fmt: skip
    (Path(sdir) / "stills" / "look.json").write_text(json.dumps(data, indent=1), encoding="utf-8")
    return data


def _wer(want: str, said: str) -> float:
    return sum(1 for op, *_ in worddiff.diff(want, said) if op != "same") / max(len(worddiff.words(want)), 1)


def write_heard(sdir: Path, lines: dict[str, str], *, checked: bool = True, why_not: str | None = None) -> None:
    """audio/vo/heard.json for the short, with `lines` heard as given ({line id: what Whisper heard}), their word
    error rates from worddiff, and a take file for each line heard."""
    short = load_short(sdir / "source" / "short.json")
    vo = sdir / "audio" / "vo"
    vo.mkdir(parents=True, exist_ok=True)
    entries = {}
    for ln in short.lines:
        if ln.id in lines:
            want, said = spoken_text(ln), lines[ln.id]
            entries[ln.id] = {"text": want, "said": said, "wer": _wer(want, said)}
            (vo / f"{ln.id}.wav").write_bytes(b"RIFF")
    data = {"checked": checked, "why_not": why_not, "lines": entries}
    (vo / HEARD_FILE).write_text(json.dumps(data, indent=2), encoding="utf-8")


def with_numbers(sdir: Path) -> None:
    """Put two numbers on screen that no fact backs: l03 states "4 m" and "1 m" and cites f-period (whose claim has
    no number); l04 states "270" and cites nothing."""
    path = sdir / "source" / "short.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    for line in data["lines"]:
        if line["id"] == "l03":
            line["text"] = "A 4 m pendulum swings half as fast as a 1 m one."
        elif line["id"] == "l04":
            line["text"] = "Clocks kept time this way for 270 years."
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def write_thumbs(sdir: Path, n: int = 2, findings: list[dict] | None = None) -> None:
    """review/thumbs/<i>-<size>.png for i in 1..n, each a flat colour of its own at the real size, and a
    review/thumbs.json with those candidates (hero s0<i>@<15*i>, from the final render, `findings` (none by default)
    and no warnings, the layouts of the centre hero)."""
    from bsa import post, thumbs

    short = load_short(sdir / "source" / "short.json")
    text = thumbs.text_from_post(post.fields(short)) or thumbs.ThumbText(("Why it keeps time",), None, None)
    style = thumbs.style_for(short, {})
    hero, _ = thumbs.hero_box([])
    layouts = {"1080x1920": thumbs.layout_dict(thumbs.vertical(text, hero, style)),
               "1280x720": thumbs.layout_dict(thumbs.landscape(text, hero, style))}  # fmt: skip
    rows = []
    for i in range(1, n + 1):
        files = thumbs.files_for(i)
        for size, rel in files.items():
            w, h = thumbs.SIZES[size]
            write_png(sdir / rel, np.full((h, w, 3), (30 * i % 256, 90, 200 - 20 * i % 200), np.uint8))
        rows.append({"n": i, "hero": {"shot": f"s0{i}", "frame": 15 * i, "crop": None, "why": thumbs.WHY_POST},
                     "source": "final", "files": files, "layouts": layouts, "findings": list(findings or []),
                     "warnings": []})  # fmt: skip
    thumbs.put_candidates(sdir, rows)


def write_like(sdir: Path, other_title: str = "Eiffel Tower", copied: tuple[str, ...] | None = None) -> Path:
    """Make the short at `sdir` start from another short (`new-short --like`): an earlier short titled `other_title`
    in the same workspace, and a short.json `like` plus the copied values. Returns the other short's folder."""
    sdir = Path(sdir)
    other = workspace.new_short(sdir.parent, other_title, 2)
    data = json.loads(SOURCE.read_text(encoding="utf-8"))
    data["title"] = other_title
    (other / "source" / "short.json").write_text(json.dumps(data), encoding="utf-8")
    path = sdir / "source" / "short.json"
    mine = json.loads(path.read_text(encoding="utf-8"))
    mine["style"] = {"style": "toy", "set": "workbench", "palette": {"accent": "#E8B33A"}, "font": "Segoe UI"}
    mine["look"] = "fresh"
    mine["pace"] = {"shots": 8, "min_s": 4.0, "holds": 2}
    mine["closing"] = False
    mine["narrator"] = {"voice": "af_heart", "speed": 1.0, "take_mode": "paragraph", "lead_in": 0.3}
    keys = copied or ("style", "look", "length_s", "pace", "closing", "narrator")
    mine["like"] = {"short": other.name, "copied": list(keys), "at": "2026-11-04T10:00:00Z", "title": other_title,
                    "style_keys": ["style", "set", "palette.accent", "font"]}
    path.write_text(json.dumps(mine), encoding="utf-8")
    return other
