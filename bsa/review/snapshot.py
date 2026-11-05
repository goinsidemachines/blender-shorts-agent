"""The review page's snapshot: everything the page shows about one short, built fresh from the files on disk.

Reading never writes anything except stage versions (and only when what the user sees changed). A damaged input
(short.json, state.json, a store file) becomes a plain problem line for the part it affects; nothing is reset and
nothing raises."""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from bsa import captions, engine_runs, feedback, jobstate, messages, render_status, rules
from bsa import closing as closing_mod
from bsa import settings as settings_mod
from bsa.layout import H, W, caption_rect, safe_rect
from bsa.narrate.run import HEARD_FILE
from bsa.review import decisions, facts, notes, pages, stages, store, track, versions, voice
from bsa.short import Short, camera_stage_wanted, label_text, load_short
from bsa.timeline import WindowOverrun, build_timeline, pacing

SCHEMA = 1
VERSIONED = ("interview", "story", "storyboard", "camera", "engine", "narration", "final", "post")


@dataclass
class Ctx:
    sdir: Path
    settings: dict
    short: Short | None
    short_problem: str | None
    state: dict | None
    state_problem: str | None


def _plain(sdir: Path, err) -> str:
    """One line for an error, without the short's own folder path in front of file names."""
    return messages.one_line(str(err).replace(str(sdir) + os.sep, ""))


def load_ctx(sdir: Path, settings: dict | None = None) -> Ctx:
    """The inputs every builder reads. Never raises: a bad input is None plus a one-line problem."""
    sdir = Path(sdir)
    if settings is None:
        try:
            settings = settings_mod.load_settings()
        except Exception:  # noqa: BLE001 - a bad input must never break the page
            settings = {}
    short = short_problem = state = state_problem = None
    try:
        short = load_short(sdir / "source" / "short.json")
    except FileNotFoundError:
        short_problem = "source/short.json is missing"
    except Exception as e:  # noqa: BLE001 - a bad input must never break the page
        short_problem = _plain(sdir, f"source/short.json can't be read: {type(e).__name__}: {e}")
    try:
        state = jobstate.load_state(sdir)
    except jobstate.StateDamaged as e:
        state_problem = _plain(sdir, e)
    except Exception as e:  # noqa: BLE001 - a bad input must never break the page
        state_problem = _plain(sdir, f"state.json can't be read: {type(e).__name__}: {e}")
    return Ctx(sdir, settings, short, short_problem, state, state_problem)


def _interview(ctx: Ctx) -> dict | None:
    if ctx.short is None:
        raise RuntimeError(ctx.short_problem)
    s = ctx.short
    out = {"title": s.title, "topic": s.topic, "audience": s.audience, "length_s": s.length_s, "field": s.field}
    if s.like:
        out["started_from"] = _started_from(ctx.sdir, s)
    return out


def _started_from(sdir: Path, s: Short) -> dict:
    """What this short started from (`new-short --like`): the other short's title (its folder name when its short.json
    is gone or unreadable) and each copied value as one plain line. Review information, left out of the version."""
    from bsa import like

    name = str(s.like.get("short") or "")
    title = str(s.like.get("title") or "") or name  # the live title wins, then the recorded one, then the folder
    try:
        if Path(name).name != name:
            raise ValueError(name)
        title = load_short(Path(sdir).parent / name / "source" / "short.json").title or name
    except Exception:  # noqa: BLE001, S110 - the other short may be gone: the page still names it
        pass
    copied = [{"key": k, "label": like.LABELS.get(k, k), "value": like.describe(k, getattr(s, k, None), s.like)}
              for k in s.like.get("copied") or [] if k in like.COPY_KEYS]  # fmt: skip
    return {"title": title, "short": name, "copied": copied}


def _short(ctx: Ctx) -> Short:
    if ctx.short is None:
        raise RuntimeError(ctx.short_problem)
    return ctx.short


def still_ref(sdir: Path, shot_id: str) -> dict | None:
    """A still as `{"file": "stills/<id>.jpg"}` (or `.png` when there is no `.jpg`: fixture stills are PNG); None
    when the short has neither."""
    for ext in ("jpg", "png"):
        if (Path(sdir) / "stills" / f"{shot_id}.{ext}").is_file():
            return {"file": f"stills/{shot_id}.{ext}"}
    return None


def _read_list(path: Path) -> list[dict]:
    """A planning file's list of objects; [] when it is missing, unreadable or of another shape."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [d for d in data if isinstance(d, dict)] if isinstance(data, list) else []


def _reading_timeline(ctx: Ctx, short: Short):
    """The short at reading pace. The story and storyboard are approved before narration exists, so their pacing and
    times are not read from takes: that keeps their versions steady once the voice is made."""
    return _reading_timeline_and_problem(ctx, short)[0]


def _reading_timeline_and_problem(ctx: Ctx, short: Short):
    """(the timeline at reading pace, None), or for a one-take short whose lines overrun a window (the timeline it
    would be, the WindowOverrun's one line): the shots still show while the storyboard says what to fix."""
    try:
        return build_timeline(short, None, settings_mod.has_end_card(ctx.settings, short)), None
    except WindowOverrun as e:
        if e.timeline is None:
            raise
        return e.timeline, _plain(ctx.sdir, e)


def _asset_rows(assets: list) -> list[dict]:
    return [
        {"name": a.name, "from": urlparse(a.source).netloc or a.source, "license": a.license, "bytes": a.bytes}
        for a in assets
    ]


def _pending_assets(sdir: Path) -> list:
    from bsa.short import Asset

    out = []
    for item in _read_list(sdir / "source" / "assets-plan.json"):
        try:
            out.append(Asset(**item))
        except TypeError:
            continue
    return out


def _rule_rows(ctx: Ctx, short: Short) -> list[dict]:
    return rules.panel_rows(rules.effective(ctx.settings, short), short)


def _story(ctx: Ctx) -> dict | None:
    short = _short(ctx)
    tl = _reading_timeline(ctx, short)
    end_card = settings_mod.has_end_card(ctx.settings, short)
    pace = {row["shot"]: row for row in pacing(short, tl, end_card)}
    beats = []
    for i, shot in enumerate(short.shots, 1):
        row = pace.get(shot.id)
        beats.append(
            {
                "shot": shot.id,
                "n": i,
                "title": shot.title,
                "badge": shot.badge,
                "picture": shot.picture,
                "lines": [{"id": ln.id, "text": ln.text} for ln in short.lines if ln.shot == shot.id],
                "pace": {k: row[k] for k in ("seconds", "spoken_s", "silent_s", "too_silent", "suggest_s")}
                if row
                else None,
            }
        )
    look = settings_mod.look_mode(ctx.settings, short)
    options = []
    if look != "brand":  # the brand has no look options
        for i, o in enumerate(_read_list(ctx.sdir / "source" / "styles.json"), 1):
            options.append(
                {
                    "name": o.get("name"),
                    "description": o.get("description", ""),
                    "file": still_ref(ctx.sdir, f"style-{i}"),
                }
            )
    return {
        "lead": f"{len(short.shots)} shots, about {short.length_s:.1f} s. Read it through and say what to change.",
        "hook": short.lines[0].text if short.lines else "",
        "closing": short.lines[-1].text if short.lines else "",
        "beats": beats,
        "facts": [
            {"id": f.id, "claim": f.claim, "source": f.source, "is_link": facts.is_link(f.source)} for f in short.facts
        ],
        "look": {"note": pages.LOOK_NOTES.get(look or ""), "options": options},
        "assets": {"pending": _asset_rows(_pending_assets(ctx.sdir)), "downloaded": _asset_rows(short.assets)},
        "rules": _rule_rows(ctx, short),
        "facts_panel": facts.panel(short),
    }


def _pct(v: float, whole: float) -> float:
    return round(100 * v / whole, 3)


def _rect_pct(r) -> dict:
    return {"l": _pct(r.x, W), "t": _pct(r.y, H), "w": _pct(r.w, W), "h": _pct(r.h, H)}


def _closing_card(ctx: Ctx, short: Short) -> dict | None:
    if not closing_mod.active(ctx.settings, short):
        return None
    card = pages.closing_info(ctx.sdir / "stills")
    if card is None:
        return None
    held = card.get("held_s")
    return {
        "file": f"stills/{closing_mod.CARD_FILE}",
        "said": card.get("voice_line") or None,
        "held": float(held) if held else float(card.get("seconds") or 0),
        "warnings": [str(w) for w in card.get("warnings") or []],
    }


LOOK_JSON = "look.json"  # stills/look.json, written by `BLENDER look` (bsa.blender.lookframes)
# Shown with the look, never part of what is approved: the measured speed, whether it was the first, and the note
# that the kit changed since (a kit edit alone must not ask for a new yes; rendering the look again does).
LOOK_INFO = ("engine", "seconds_per_frame", "estimates", "first", "kit_warning")
OLD_KIT = "the look was rendered with older kit colours: run BLENDER look again"
_HEX6 = re.compile(r"#[0-9A-Fa-f]{6}")


def _look_file(sdir: Path, rel) -> dict | None:
    """A look preview file named relative to stills/ (as look.json names them) as a file ref, None when it is not a
    plain file under stills/look/ or is not there."""
    if not isinstance(rel, str) or not rel.startswith("look/"):
        return None
    name = rel[len("look/") :]
    if not name or "/" in name or "\\" in name or name in (".", ".."):
        return None
    return {"file": f"stills/{rel}"} if (Path(sdir) / "stills" / rel).is_file() else None


def _read_look(sdir: Path) -> tuple[dict, list[str]]:
    """look.json's fields, each of the type the page needs (a wrong one left out), and a warning per field left
    out. A file that can't be read at all is one warning."""
    redo = "make the look preview again"
    try:
        data = json.loads((sdir / "stills" / LOOK_JSON).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        return {}, [_plain(sdir, f"stills/{LOOK_JSON} can't be read: {e}: {redo}")]
    if not isinstance(data, dict):
        return {}, [f"stills/{LOOK_JSON} is not a JSON object: {redo}"]
    want = {"style": str, "set": str, "built": dict, "frames": list, "sheet": str, "engine": str,
            "estimates": dict, "warnings": list, "swatches": list}  # fmt: skip
    out: dict = {}
    bad: list[str] = []
    for key, kind in want.items():
        value = data.get(key)
        if value is None:
            continue
        if isinstance(value, kind):
            out[key] = value
        else:
            bad.append(key)
    secs = data.get("seconds_per_frame")
    if isinstance(secs, (int, float)) and not isinstance(secs, bool):
        out["seconds_per_frame"] = float(secs)
    elif secs is not None:
        bad.append("seconds_per_frame")
    out["first"] = data.get("first") is True
    frames = []
    for f in out.get("frames", []):
        if isinstance(f, dict) and isinstance(f.get("shot"), str):
            frames.append(f)
        elif "frames" not in bad:
            bad.append("frames")
    out["frames"] = frames
    swatches = [w for w in out.get("swatches", []) if isinstance(w, dict) and isinstance(w.get("name"), str)
                and isinstance(w.get("hex"), str) and _HEX6.fullmatch(w["hex"])]  # fmt: skip
    if len(swatches) != len(out.get("swatches", [])):
        bad.append("swatches")
    if "swatches" in out:
        out["swatches"] = [{"name": w["name"], "hex": w["hex"].upper()} for w in swatches]
    warnings = [f"stills/{LOOK_JSON} has a wrong {k!r}, left out: {redo}" for k in bad]
    return out, [str(w) for w in out.get("warnings", []) if w] + warnings


def _look(ctx: Ctx, short: Short) -> dict | None:
    """The look preview (`BLENDER look`) for the Storyboard stage: None without a kit or without stills/look.json.
    Everything shown comes from look.json (what was rendered); a field of the wrong type, or a file that can't be
    read, is a warning, never a problem for the stage. A kit whose colours changed since is a kit_warning."""
    from bsa import kit as kit_mod
    from bsa import looks, setplace

    kit = kit_mod.kit_for(ctx.settings, short)
    if kit is None or not (ctx.sdir / "stills" / LOOK_JSON).is_file():
        return None
    data, warnings = _read_look(ctx.sdir)
    style = data.get("style")
    set_name = data.get("set", "none") if style else None
    built = data.get("built", {})
    titles = {s.id: s.title for s in short.shots}
    frames = [{"shot": f["shot"], "title": titles.get(f["shot"], f["shot"]), "file": _look_file(ctx.sdir, f.get("file"))}
              for f in data.get("frames", [])]  # fmt: skip
    now = [{"name": w["name"], "hex": w["hex"].upper()} for w in looks.swatches(kit)]
    rendered = data.get("swatches")
    est = data.get("estimates")
    return {
        "style": style,
        "set": set_name,
        "built": {"style": built.get("style"), "set": built.get("set")},
        "styles": [s for s in kit_mod.STYLES if s != style],
        "sets": [s for s in setplace.SETS if s != set_name] + (["none"] if set_name != "none" else []),
        "frames": frames,
        "sheet": _look_file(ctx.sdir, data.get("sheet")),
        "swatches": now if rendered is None else rendered,  # an older look.json without them: the kit now
        "engine": data.get("engine"),
        "seconds_per_frame": data.get("seconds_per_frame"),
        "estimates": {k: est.get(k) for k in ("eevee_s", "cycles_s")} if est is not None else None,
        "first": data.get("first", False),
        "warnings": warnings,
        "kit_warning": OLD_KIT if rendered is not None and rendered != now else None,
    }


def _storyboard(ctx: Ctx) -> dict | None:
    short = _short(ctx)
    tl, timing_problem = _reading_timeline_and_problem(ctx, short)
    fps = tl.fps
    spans = {s.id: s for s in tl.shots}
    line_spans = {s.id: s for s in tl.lines}
    total = max(tl.n_frames, 1) / fps
    size = int(short.style.get("caption", {}).get("size", 64)) if isinstance(short.style, dict) else 64
    shots = []
    for i, shot in enumerate(short.shots, 1):
        span = spans.get(shot.id)
        lines = []
        for ln in short.lines:
            if ln.shot != shot.id:
                continue
            ls = line_spans.get(ln.id)
            lines.append(
                {
                    "id": ln.id,
                    "text": ln.text,
                    "at_s": round((ls.f0 - 1) / fps, 1) if ls else None,
                    "cards": [c.text for c in tl.cards if c.line_id == ln.id],
                }
            )
        shots.append(
            {
                "id": shot.id,
                "n": i,
                "title": shot.title,
                "badge": shot.badge,
                "time": "not timed yet" if span is None else f"{(span.f0 - 1) / fps:.1f} s to {span.f1 / fps:.1f} s",
                "flex": span.f1 - span.f0 + 1 if span else 1,
                "still": still_ref(ctx.sdir, shot.id),
                "lines": lines,
                "labels": [label_text(t) for t in shot.labels],
            }
        )
    out = {
        "lead": f"{len(shots)} shots, {total:.1f} s in total. Check the pictures and the captions.",
        "total_s": round(total, 1),
        "shots": shots,
        "closing": _closing_card(ctx, short),
        "rules": _rule_rows(ctx, short),
        "look": _look(ctx, short),
        "frame": {
            "safe": _rect_pct(safe_rect()),
            "caption": _rect_pct(caption_rect(pages.CAPTION_LINES, size)),
            "cap_px": size,
        },
    }
    if timing_problem:  # a one-take window too short for its lines: the stage's problem (_stage), the shots still show
        out["timing_problem"] = timing_problem
    return out


CAMERA_LEAD = ("The camera path in Workbench grey: a draft of the move, not the look. Check what each shot frames and "
               "how the camera moves.")


def _camera_file(sdir: Path, name: str) -> dict | None:
    """A camera test file (review/camtest/<name>) as a file ref tagged with its mtime_ns, so a new clip under the
    same name is loaded again; None when it is not there."""
    from bsa import camtest

    path = sdir / camtest.FOLDER / name
    try:
        mtime = path.stat().st_mtime_ns if path.is_file() else None
    except OSError:
        mtime = None
    return None if mtime is None else {"file": (camtest.FOLDER / name).as_posix(), "tag": mtime}


def _camera_record(sdir: Path) -> dict | None:
    from bsa import camtest

    try:
        data = json.loads((sdir / camtest.FOLDER / camtest.RECORD).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _camera_timing(ctx: Ctx, short: Short) -> str:
    """The hash of the timeline as it is now (what source/timeline.json holds once CLI timeline has run): the takes'
    timing once narration made them, else reading pace, as BLENDER camtest works it out."""
    from bsa.narrate.run import takes_durations, takes_onsets
    from bsa.timeline import timeline_hash, to_json

    durations = takes_durations(ctx.sdir, short)
    tl = build_timeline(short, durations, settings_mod.has_end_card(ctx.settings, short),
                        onsets=takes_onsets(ctx.sdir, short) if durations is not None else None)  # fmt: skip
    return timeline_hash(json.loads(json.dumps(to_json(tl))))


def _camera(ctx: Ctx) -> dict | None:
    """The camera test (BLENDER camtest) for the optional Camera stage: None for a short without the stage. `stale`
    (left out of the version) says why the clip no longer shows the short: none yet, made for only some shots, or
    made before the timing or the scene changed."""
    from bsa import camtest, freshness
    from bsa import workspace as workspace_mod

    short = _short(ctx)
    if not camera_stage_wanted(short):
        return None
    command = f"BLENDER camtest {workspace_mod.short_key(ctx.sdir, ctx.settings.get('workspace'))}"
    video = _camera_file(ctx.sdir, camtest.MP4)
    rec = _camera_record(ctx.sdir) if video is not None else None
    made = None
    if rec is not None:
        made = {k: rec.get(k) for k in ("shots", "frames", "percent")}
    tested = rec.get("shots") if rec is not None else None
    missing = [s.id for s in short.shots if not isinstance(tested, list) or s.id not in tested]
    if rec is not None and missing:  # a clip of some shots (--shots) does not show the short
        stale = f"the camera test has no {', '.join(missing)}: run {command} for every shot"
    else:
        try:
            timing = _camera_timing(ctx, short)
        except Exception as e:  # noqa: BLE001 - a bad input must never break the page
            stale = _plain(ctx.sdir, f"the timing can't be worked out: {e}")
        else:
            stale = camtest.stale(rec, freshness.blend_stat(ctx.sdir), timing)
    return {
        "lead": CAMERA_LEAD,
        "video": video,
        "sheet": _camera_file(ctx.sdir, camtest.SHEET),
        "made": made,
        "stale": stale,
        "command": command,
    }


def _estimate(sdir: Path, engine: str) -> float | None:
    try:
        data = json.loads((sdir / "review" / "estimate.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    secs = data.get(f"{engine}_s") if isinstance(data, dict) else None
    return float(secs) if isinstance(secs, (int, float)) and not isinstance(secs, bool) and secs > 0 else None


def _engine(ctx: Ctx) -> dict | None:
    """The engine choice. The engine stage's own approved engine (state.json) is left out on purpose: approving
    writes it, and that must not move the version the user just approved."""
    setting = (ctx.settings.get("render") or {}).get("engine")
    engine, source = engine_runs.pick(None, engine_runs.short_engine(ctx.sdir), setting)
    runs = []
    for r in engine_runs.compare(ctx.sdir):
        mp4 = Path(r["mp4"]) if r.get("mp4") else None
        try:
            rel = mp4.resolve().relative_to(ctx.sdir.resolve()).as_posix() if mp4 else None
        except ValueError:
            rel = mp4.name if mp4 else None
        runs.append({**r, "mp4": rel})  # a name, not a {"file"}: the version must not hash a whole video
    return {
        "engine": engine,
        "from": {"short.json": "short.json", "settings": "settings"}.get(source or "", "ask"),
        "choices": [
            {"engine": e, "label": e.capitalize(), "estimate_s": _estimate(ctx.sdir, e)} for e in ("eevee", "cycles")
        ],
        "machine_note": None,
        "runs": runs,
    }


def _narration(ctx: Ctx) -> dict | None:
    if ctx.state is None:
        raise RuntimeError(ctx.state_problem)
    info = ctx.state["stages"].get("narration") or {}
    vo = ctx.sdir / "audio" / "vo"
    content = {
        "status": info.get("status"),
        "provider": info.get("provider"),
        "message": info.get("message"),
        "takes": len(list(vo.glob("*.wav"))) if vo.is_dir() else 0,
    }
    if ctx.short is not None and (vo / HEARD_FILE).is_file():  # only once narration has run
        line = closing_mod.voice_line(closing_mod.resolve(ctx.settings, ctx.short), ctx.short)
        content["voice"] = voice.block(ctx.sdir, ctx.short, content, closing=line)
    return content


APPROVED_RUNS = ("preview", "final")  # the approved engine's runs; any other status key is a side run
_RUN_KEY = re.compile(r"^(preview|final)(-[a-z0-9][a-z0-9-]{0,31})?$")  # bsa.engine_runs.status_key names


def _thumb(sdir: Path, rel: str | None, frame) -> dict | None:
    """A render-status thumbnail (named relative to review/) as a file ref, None until it exists. `frame` lets the
    page load a thumbnail again when the render writes a newer one under the same name."""
    if not isinstance(rel, str) or not rel or not (sdir / "review" / rel).is_file():
        return None
    return {"file": f"review/{rel}", "frame": frame if isinstance(frame, int) else None}


def _run_label(key: str, preset: str) -> str:
    tag = key[len(preset) + 1 :] if key.startswith(preset + "-") else ""
    return preset.capitalize() + (f" · {tag}" if tag else "")


def _step_row(s: dict) -> dict:
    """A run step for the page: its name and state, and as `note` while it runs what it is on now (its progress's
    `current`, e.g. the checks beside the render: "checking s02 (2 of 4), 0 errors so far") and how long it has not
    moved when it stalled beside the render (`stalled_s`); once it failed or stopped, why (its `error`)."""
    row = {"name": str(s.get("name")), "state": s.get("state")}
    p = s.get("progress")
    if s.get("state") == "running":
        notes = [str(p["current"])] if isinstance(p, dict) and p.get("current") else []
        if isinstance(s.get("stalled_s"), (int, float)):
            notes.append(f"no progress for {render_status.fmt_duration(s['stalled_s'])}")
        if notes:
            row["note"] = ": ".join(notes)
    elif s.get("state") in ("failed", "stopped") and s.get("error"):
        row["note"] = str(s["error"])
    return row


def _run(sdir: Path, key: str, status: dict, now: float, short: Short | None) -> dict:
    d = render_status.describe(status, now, ago=False)
    preset = str(status.get("preset") or key.split("-")[0])
    state = "stopped" if d.get("stale") else str(status.get("state") or "none")
    shots = []
    for s in d.get("shots") or []:
        if isinstance(s, dict) and s.get("id"):
            shots.append({"id": str(s["id"]), "state": s.get("state"), "done": s.get("done"), "total": s.get("total"),
                          "thumb": _thumb(sdir, s.get("thumb"), s.get("thumb_frame"))})  # fmt: skip
    current = d.get("current") if isinstance(d.get("current"), dict) else {}
    latest = next((s["thumb"] for s in shots if s["id"] == current.get("shot") and s["thumb"]), None)
    if latest is None:
        latest = next((s["thumb"] for s in reversed(shots) if s["thumb"]), None)
    run = status.get("run") if isinstance(status.get("run"), dict) else {}
    timeline, timeline_problem = None, None
    try:
        if short is not None:
            timeline = track.build(sdir, short, key)
    except Exception as e:  # noqa: BLE001 - a bad timeline.json hides this run's timeline, not the run
        timeline_problem = _plain(sdir, e)
    steps = [_step_row(s) for s in run.get("steps") or [] if isinstance(s, dict)]
    return {
        "key": key,
        "label": _run_label(key, preset),
        "side": key not in APPROVED_RUNS,
        "state": state,
        "summary": str(d.get("summary") or ""),
        "frames": d.get("frames") if isinstance(d.get("frames"), dict) else {},
        "eta_s": d.get("eta_s") if state == "running" else None,
        "steps": steps,
        "shots": shots,
        "latest": latest,
        "timeline": timeline,
        **({"timeline_problem": timeline_problem} if timeline_problem else {}),
    }


def _render(ctx: Ctx) -> dict | None:
    """Every run's live status: the approved engine's preview and final first, then the side runs. Not versioned:
    progress is not something to approve."""
    found = {}
    for path in (ctx.sdir / "review").glob("render-status-*.json"):
        key = path.stem.removeprefix("render-status-")
        if _RUN_KEY.match(key):
            status = render_status.read_status(path)
            if status is not None:
                found[key] = status
    keys = [k for k in APPROVED_RUNS if k in found] + sorted(k for k in found if k not in APPROVED_RUNS)
    now = time.time()
    runs = []
    for k in keys:
        try:
            runs.append(_run(ctx.sdir, k, found[k], now, ctx.short))
        except Exception as e:  # noqa: BLE001 - one odd status file must not hide the other runs
            runs.append({"key": k, "label": _run_label(k, k.split("-")[0]), "side": k not in APPROVED_RUNS,
                         "state": "none", "summary": _plain(ctx.sdir, f"render-status-{k}.json can't be read: "
                                                                     f"{type(e).__name__}: {e}"),
                         "frames": {}, "eta_s": None, "steps": [], "shots": [], "latest": None, "timeline": None})  # fmt: skip
    return {"runs": runs}


finding_id = pages.finding_id  # moved to pages (track.py needs it); kept here for older callers


_where = pages.where_text


def _final_writing(ctx: Ctx) -> bool:
    """The final MP4 is being written right now: the final step is not done in state.json and the final run (its
    edit encodes out/<slug>.mp4 in place) is running."""
    if ctx.state is not None and "final" in ctx.state["stages"]:
        return False
    status = render_status.read_status(render_status.status_path(ctx.sdir, "final"))
    return bool(status) and status.get("state") == "running" and render_status.is_alive(status)


def _final(ctx: Ctx) -> dict | None:
    """The final video, the checks' findings (each with a stable id for a note), the hard rules and the closing
    card. Whether the user said it looks good (`acked`) is added after the version is taken (see _stage). While the
    MP4 is being written (`writing`) it is not shown and the stage has no version."""
    short = _short(ctx)
    mp4 = ctx.sdir / "out" / f"{short.slug}.mp4"
    writing = _final_writing(ctx)
    found, redo_all = pages.load_findings(ctx.sdir)
    rows, seen = [], set()
    for f in found:
        fid = finding_id(f)
        if fid in seen:  # the same finding in two files is one finding
            continue
        seen.add(fid)
        rows.append({"id": fid, "check": f.check, "severity": f.severity, "message": f.message, "where": _where(f),
                     "redo": f.redo})  # fmt: skip
    size = mp4.stat().st_size if mp4.is_file() and not writing else None
    timeline, timeline_problem = None, None
    if size is not None:
        try:
            timeline = track.build(ctx.sdir, short, "final")
        except Exception as e:  # noqa: BLE001 - a bad timeline.json hides the timeline, not the video
            timeline_problem = _plain(ctx.sdir, e)
    vtt = captions.paths(ctx.sdir, short)["vtt"]
    has_captions = vtt.is_file() and not captions.state(ctx.sdir, short)["stale"]
    return {
        "video": {"file": f"out/{short.slug}.mp4"} if size is not None else None,
        "captions": {"file": f"out/{short.slug}.vtt"} if has_captions else None,
        "writing": writing,
        "size_mb": round(size / 1_000_000, 1) if size is not None else None,
        "findings": rows,
        "redo_all": redo_all,
        "checks_run": (ctx.sdir / "review" / "checks.json").is_file(),
        "rules": _rule_rows(ctx, short),
        "closing": _closing_card(ctx, short),
        "timeline": timeline,
        "facts_panel": facts.panel(short),
        **({"timeline_problem": timeline_problem} if timeline_problem else {}),
    }


# Where a post.problems line belongs: the first field whose words it starts with (the thumbnail's and posted's go to
# none of the fields: they are _OTHER_PREFIXES, shown in the Thumbnail section).
_NOTE_PREFIXES = (("post pinned comment", "pinned_comment"), ("post title", "title"),
                  ("post description", "description"), ("post hashtag", "hashtags"), ("post has ", "hashtags"),
                  ("post tags", "tags"), ("post link", "link"), ("post related", "related"))  # fmt: skip
_OTHER_PREFIXES = ("post thumbnail", "post posted")


def _field_text(key: str, value) -> str:
    if key == "related":
        if not isinstance(value, dict):
            return ""
        return " ".join(str(value[k]).strip() for k in ("title", "url") if value.get(k) and str(value[k]).strip())
    if isinstance(value, list):
        return (", " if key == "tags" else " ").join(str(v) for v in value)
    return str(value or "")


def _post_fields(short, settings: dict) -> tuple[list[dict], dict[str, str], list[str]]:
    """One row per post field (its text, its count against the platform limit and, for the description, the
    platforms it fits), each field's warning or error, {key: note}, and the thumbnail's and posted's problems (no
    field's). The notes are kept apart: they follow the user's settings and the checks' wording, neither of which is
    the post itself, so they never move its version."""
    from bsa import post as post_mod

    p = post_mod.fields(short)
    counts = post_mod.counts(short)
    errors, warnings = post_mod.problems(short, settings or {})
    notes_of: dict[str, list[str]] = {}
    others = []
    for line in errors + warnings:  # errors first: the worse news leads
        if line.startswith(_OTHER_PREFIXES):
            others.append(line)
            continue
        key = next((k for prefix, k in _NOTE_PREFIXES if line.startswith(prefix)), None)
        if key:
            notes_of.setdefault(key, []).append(line)
    rows = []
    for key in post_mod.FIELDS:
        c = counts.get(key, {})
        desc = p["description"] if key == "description" and isinstance(p["description"], str) else None
        rows.append({"key": key, "label": post_mod.LABELS[key], "text": _field_text(key, p[key]),
                     "count": c.get("count"), "max": c.get("max"), "warn_over": c.get("warn_over"),
                     "state": c.get("state"), "badges": post_mod.fits(desc) if desc is not None else []})  # fmt: skip
    return rows, {k: "; ".join(v) for k, v in notes_of.items()}, others


def _texts(items) -> list[str]:
    return [str(x) for x in items] if isinstance(items, list) else []


def _candidates(sdir: Path, data: dict) -> list[dict]:
    """The thumbnail candidates of review/thumbs.json as the page shows them, with which one is chosen. Only error
    and warning findings: an info row (a measurement skipped, say) is no news, as in `BLENDER thumbs`'s output."""
    from bsa import thumbs

    chosen = thumbs._chosen_n(data.get("chosen"))
    out = []
    for c in data.get("candidates") or []:
        n = c.get("n") if isinstance(c, dict) else None
        if not isinstance(n, int) or isinstance(n, bool):
            continue
        files = thumbs.files_for(n)
        stamps = []
        for rel in files.values():  # a remade candidate gets new image URLs: the browser never shows the old one
            try:
                st = (sdir / rel).stat()
                stamps.append(f"{st.st_mtime_ns}.{st.st_size}")
            except OSError:
                stamps.append("missing")
        out.append({"n": n, "hero": c.get("hero") if isinstance(c.get("hero"), dict) else None,
                    "source": c.get("source"), "vertical": {"file": files["1080x1920"]},
                    "landscape": {"file": files["1280x720"]}, "warnings": _texts(c.get("warnings")),
                    "findings": [f for f in c.get("findings") or []
                                 if isinstance(f, dict) and f.get("severity") in ("error", "warning")],
                    "chosen": n == chosen, "tag": "-".join(stamps)})  # fmt: skip
    return out


def _post(ctx: Ctx) -> dict | None:
    """The post text to copy with its counts and notes (a note on any field), the chosen thumbnail and the
    candidates to choose from, the files to upload, the before-you-post checklist, the closing card and the asset
    credits. The thumbnail is versioned only once short.json records a choice and its file is in out/; the
    candidates, files, checklist and field notes are not (see _UNVERSIONED): making them, ticking a box or changing
    a setting is not something to answer. A damaged review/thumbs.json or review/checklist.json is a line (`thumbs_problem`,
    `checklist_problem`), not a broken stage."""
    from bsa import postpack, thumbs
    from bsa.review import checklist

    short = _short(ctx)
    post = short.post if isinstance(short.post, dict) else {}
    fields, field_notes, thumbnail_problems = _post_fields(short, ctx.settings)
    th = post.get("thumbnail") if isinstance(post.get("thumbnail"), dict) else {}
    chosen = thumbs._chosen_n(th.get("chosen"))
    out_file = postpack.OUT_FILES["1080x1920"]
    thumbnail = None
    if chosen is not None and (ctx.sdir / out_file).is_file():
        thumbnail = {"chosen": chosen, "file": {"file": out_file}}
    video, cap_state = postpack.video_info(ctx.sdir, short), captions.state(ctx.sdir, short)  # once per build
    content = {"fields": fields, "field_notes": field_notes, "thumbnail_problems": thumbnail_problems,
               "thumbnail": thumbnail, "candidates": [], "files": postpack.files(ctx.sdir, short, video, cap_state, ctx.settings),
               "checklist": [], "closing": _closing_card(ctx, short), "credits": _asset_rows(short.assets)}  # fmt: skip
    try:
        content["candidates"] = _candidates(ctx.sdir, thumbs.load(ctx.sdir))
    except store.StoreDamaged as e:
        content["thumbs_problem"] = _plain(ctx.sdir, e)
    try:
        ticks = checklist.load(ctx.sdir)
    except store.StoreDamaged as e:
        ticks = {}
        content["checklist_problem"] = _plain(ctx.sdir, e)
    content["checklist"] = [{**item, "done": ticks.get(item["id"], False)}
                            for item in postpack.checklist_items(ctx.sdir, short, video, cap_state, ctx.settings)]  # fmt: skip
    return content


BUILDERS: dict[str, Callable[[Ctx], dict | None]] = {
    "interview": _interview,
    "story": _story,
    "storyboard": _storyboard,
    "camera": _camera,
    "engine": _engine,
    "narration": _narration,
    "render": _render,
    "final": _final,
    "post": _post,
}


# Content keys left out of a stage's fingerprint: the engine's side-run measurements (a side run is not something the
# user approves, so it must not move the version they answered).
_UNVERSIONED = {"interview": ("started_from",), "engine": ("runs",), "narration": ("voice",), "camera": ("stale",),
                "story": ("facts_panel",), "final": ("timeline", "facts_panel", "timeline_problem", "captions"),
                "post": ("candidates", "files", "checklist", "field_notes", "thumbs_problem",
                         "checklist_problem", "thumbnail_problems")}


def stage_fingerprint(sdir: Path, stage: str, content: dict) -> str:
    """The fingerprint a stage's version is looked up by: its content without the review blocks (_UNVERSIONED)."""
    seen = {k: v for k, v in content.items() if k not in _UNVERSIONED.get(stage, ())}
    if stage == "storyboard" and "look" in seen:  # the look is approved with the storyboard, its timing is not
        if seen["look"] is None:
            del seen["look"]  # no look preview: the storyboard's version is what it was before looks existed
        elif isinstance(seen["look"], dict):
            seen["look"] = {k: v for k, v in seen["look"].items() if k not in LOOK_INFO}
    return versions.fingerprint(seen, sdir)


def _version_of(sdir: Path, stage: str, content: dict) -> int | None:
    """The one place a stage's content is fingerprinted and looked up (or added) in versions.json. None for the
    final while its MP4 is still being written: a half-written file is never a version."""
    if stage == "final" and content.get("writing"):
        return None
    return versions.version_of(sdir, stage, stage_fingerprint(sdir, stage, content))


def stage_version(sdir: Path, stage: str, ctx: Ctx | None = None) -> int | None:
    """The stage's current version: its content fingerprinted and looked up (or added) in versions.json."""
    if stage not in VERSIONED:
        return None
    ctx = ctx or load_ctx(sdir)
    content = BUILDERS[stage](ctx)
    if content is None:
        return None
    return _version_of(sdir, stage, content)


def _stage(ctx: Ctx, stage: str, status: str, live: list[dict], problems: list[str]) -> dict:
    out = {
        "id": stage,
        "label": stages.LABELS[stage],
        "status": status,
        "needs_yes": False,
        "waiting_for_claude": False,
        "version": None,
        "content": None,
        "problem": None,
        "decision": None,
    }
    try:
        out["content"] = BUILDERS[stage](ctx)
    except Exception as e:  # noqa: BLE001 - a bad input must never break the page
        out["problem"] = _plain(ctx.sdir, e)
    if isinstance(out["content"], dict) and out["content"].get("timing_problem"):
        out["problem"] = out["content"]["timing_problem"]  # the storyboard's one-take window overrun
    if out["content"] is not None and stage in VERSIONED:
        try:
            out["version"] = _version_of(ctx.sdir, stage, out["content"])
        except store.StoreDamaged as e:  # the content still shows; the damaged file is said once, not per stage
            line = _plain(ctx.sdir, e)
            if line not in problems:
                problems.append(line)
        except Exception as e:  # noqa: BLE001 - a bad input must never break the page
            out["problem"] = _plain(ctx.sdir, e)
    mine = [d for d in live if d.get("stage") == stage]
    out["decision"] = mine[-1] if mine else None
    v = out["version"]
    if stage in stages.ACK_STAGES and isinstance(out["content"], dict):  # after the version: acking must not move it
        out["content"]["acked"] = v is not None and any(
            d.get("decision") == "approve" and d.get("version") == v for d in mine
        )
    if status == "current" and v is not None:
        approves = [d for d in mine if d.get("decision") == "approve" and d.get("version") == v]
        if stage in stages.APPROVAL_STAGES:
            out["needs_yes"] = not approves
        out["waiting_for_claude"] = any(not d.get("applied") for d in approves)
    return out


def _feedback(ctx: Ctx) -> dict | None:
    """The plugin feedback panel's report, only once the user said yes to seeing it (feedback.allow_panel) and never
    with the setting `off`. Redacted like the CLI's report, of the pending entries; `sent` when none is pending (all
    reported). Building it never marks anything reported."""
    if not feedback.panel_allowed(ctx.sdir, ctx.settings.get("feedback")):
        return None
    s = ctx.short
    own = [t for t in [s.title, *(ln.text for ln in s.lines)] if t and t.strip()] if s else []
    sent = not feedback.pending(ctx.sdir)  # every entry reported already: the panel says so instead of asking again
    rep = feedback.report(ctx.sdir, workspace=ctx.settings.get("workspace"),
                          scrub=feedback.settings_scrub(ctx.settings) + own, mark=False)  # fmt: skip
    return {"sent": sent, **{k: rep[k] for k in ("entries", "title", "body", "url", "shortened", "by_severity", "ids")}}


def _health(sdir: Path) -> list[dict]:
    """The checks `doctor` saved in review/doctor.json that are not ok (warnings and errors), for the banner."""
    try:
        checks = json.loads((sdir / "review" / "doctor.json").read_text("utf-8")).get("checks")
    except (OSError, ValueError, AttributeError):
        return []
    out = []
    for c in checks if isinstance(checks, list) else []:
        if isinstance(c, dict) and c.get("level") != "ok" and c.get("text"):
            out.append({"id": str(c.get("id") or ""), "level": str(c.get("level")), "text": str(c["text"]),
                        "fix": str(c["fix"]) if c.get("fix") else None})  # fmt: skip
    return out


def build(sdir: Path, *, settings: dict | None = None, now: float | None = None) -> dict:
    sdir = Path(sdir)
    ctx = load_ctx(sdir, settings)
    problems = [p for p in (ctx.short_problem, ctx.state_problem) if p]
    done = set(ctx.state["stages"]) if ctx.state else set()
    page = stages.page_stages(camera_stage_wanted(ctx.short))  # a short.json that can't be read: no camera stage
    status = stages.statuses(done, page)
    live: list[dict] = []
    try:
        live = [d for d in decisions.load(sdir) if not d.get("superseded")]
    except Exception as e:  # noqa: BLE001 - a bad input must never break the page
        problems.append(_plain(sdir, e))
    note_list: list[dict] = []
    try:
        note_list = notes.load(sdir)
    except Exception as e:  # noqa: BLE001 - a bad input must never break the page
        problems.append(_plain(sdir, e))
    rows = [_stage(ctx, s, status[s], live, problems) for s in page]
    current = next((s for s in page if status[s] == "current"), page[-1])
    short = ctx.short
    panel = None
    try:
        panel = _feedback(ctx)
    except Exception as e:  # noqa: BLE001 - a bad input must never break the page
        problems.append(_plain(sdir, f"the plugin feedback can't be shown: {type(e).__name__}: {e}"))
    return {
        "schema": SCHEMA,
        "generated": time.time() if now is None else now,
        "short": {
            "key": sdir.name,
            "title": short.title if short else sdir.name,
            "slug": short.slug if short else sdir.name,
        },
        "current": current,
        "problems": problems,
        "stages": rows,
        "notes": note_list,
        "health": _health(sdir),
        "feedback": panel,
        "phone": None,
    }


def etag(snap: dict) -> str:
    """A quoted sha256 of the snapshot without its `generated` time: unchanged content, unchanged etag."""
    body = {k: v for k, v in snap.items() if k != "generated"}
    raw = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)
    return '"' + hashlib.sha256(raw.encode("utf-8")).hexdigest() + '"'
