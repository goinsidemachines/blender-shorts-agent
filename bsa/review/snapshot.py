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

from bsa import closing as closing_mod
from bsa import engine_runs, feedback, jobstate, messages, render_status, rules
from bsa import settings as settings_mod
from bsa.layout import H, W, caption_rect, safe_rect
from bsa.review import decisions, notes, pages, stages, store, versions
from bsa.short import Short, label_text, load_short
from bsa.timeline import build_timeline, pacing

SCHEMA = 1
VERSIONED = ("interview", "story", "storyboard", "engine", "narration", "final", "post")


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
    return {"title": s.title, "topic": s.topic, "audience": s.audience, "length_s": s.length_s, "field": s.field}


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
    return build_timeline(short, None, settings_mod.has_end_card(ctx.settings, short))


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


def _is_link(url: str) -> bool:
    parts = urlparse(url.strip())
    return parts.scheme in ("http", "https") and bool(parts.netloc)


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
            {"id": f.id, "claim": f.claim, "source": f.source, "is_link": _is_link(f.source)} for f in short.facts
        ],
        "look": {"note": pages.LOOK_NOTES.get(look or ""), "options": options},
        "assets": {"pending": _asset_rows(_pending_assets(ctx.sdir)), "downloaded": _asset_rows(short.assets)},
        "rules": _rule_rows(ctx, short),
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


def _storyboard(ctx: Ctx) -> dict | None:
    short = _short(ctx)
    tl = _reading_timeline(ctx, short)
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
    return {
        "lead": f"{len(shots)} shots, {total:.1f} s in total. Check the pictures and the captions.",
        "total_s": round(total, 1),
        "shots": shots,
        "closing": _closing_card(ctx, short),
        "rules": _rule_rows(ctx, short),
        "frame": {
            "safe": _rect_pct(safe_rect()),
            "caption": _rect_pct(caption_rect(pages.CAPTION_LINES, size)),
            "cap_px": size,
        },
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
    return {
        "status": info.get("status"),
        "provider": info.get("provider"),
        "message": info.get("message"),
        "takes": len(list(vo.glob("*.wav"))) if vo.is_dir() else 0,
    }


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


def _run(sdir: Path, key: str, status: dict, now: float) -> dict:
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
    steps = [{"name": str(s.get("name")), "state": s.get("state")} for s in run.get("steps") or [] if isinstance(s, dict)]
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
            runs.append(_run(ctx.sdir, k, found[k], now))
        except Exception as e:  # noqa: BLE001 - one odd status file must not hide the other runs
            runs.append({"key": k, "label": _run_label(k, k.split("-")[0]), "side": k not in APPROVED_RUNS,
                         "state": "none", "summary": _plain(ctx.sdir, f"render-status-{k}.json can't be read: "
                                                                     f"{type(e).__name__}: {e}"),
                         "frames": {}, "eta_s": None, "steps": [], "shots": [], "latest": None})  # fmt: skip
    return {"runs": runs}


def finding_id(f: pages.ReviewFinding) -> str:
    """A finding's id, stable across rebuilds: the first 10 hex characters of sha1(check|message|frame|shots)."""
    frame = "" if f.frame is None else str(f.frame)
    raw = f"{f.check}|{f.message}|{frame}|{','.join(f.shots)}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:10]


def _where(f: pages.ReviewFinding) -> str:
    shots = f.shots
    parts = [
        f"shot{'s' if len(shots) > 1 else ''} {', '.join(shots)}" if shots else "",
        f"scene {f.scene}" if f.scene else "",
        str(f.obj) if f.obj else "",
        f"frame {f.frame}" if f.frame is not None else "",
    ]
    return " · ".join(x for x in parts if x)


def _final(ctx: Ctx) -> dict | None:
    """The final video, the checks' findings (each with a stable id for a note), the hard rules and the closing
    card. Whether the user said it looks good (`acked`) is added after the version is taken (see _stage)."""
    short = _short(ctx)
    mp4 = ctx.sdir / "out" / f"{short.slug}.mp4"
    found, redo_all = pages.load_findings(ctx.sdir)
    rows, seen = [], set()
    for f in found:
        fid = finding_id(f)
        if fid in seen:  # the same finding in two files is one finding
            continue
        seen.add(fid)
        rows.append({"id": fid, "check": f.check, "severity": f.severity, "message": f.message, "where": _where(f),
                     "redo": f.redo})  # fmt: skip
    size = mp4.stat().st_size if mp4.is_file() else None
    return {
        "video": {"file": f"out/{short.slug}.mp4"} if size is not None else None,
        "size_mb": round(size / 1_000_000, 1) if size is not None else None,
        "findings": rows,
        "redo_all": redo_all,
        "checks_run": (ctx.sdir / "review" / "checks.json").is_file(),
        "rules": _rule_rows(ctx, short),
        "closing": _closing_card(ctx, short),
    }


POST_FIELDS = (("title", "Title"), ("description", "Description"), ("hashtags", "Hashtags"))


def _post(ctx: Ctx) -> dict | None:
    """The post text to copy (a note on any field), the closing card and the asset credits."""
    short = _short(ctx)
    post = short.post if isinstance(short.post, dict) else {}
    fields = []
    for key, label in POST_FIELDS:
        value = post.get(key)
        text = " ".join(str(v) for v in value) if isinstance(value, list) else str(value or "")
        fields.append({"key": key, "label": label, "text": text})
    return {"fields": fields, "closing": _closing_card(ctx, short), "credits": _asset_rows(short.assets)}


BUILDERS: dict[str, Callable[[Ctx], dict | None]] = {
    "interview": _interview,
    "story": _story,
    "storyboard": _storyboard,
    "engine": _engine,
    "narration": _narration,
    "render": _render,
    "final": _final,
    "post": _post,
}


def _version_of(sdir: Path, stage: str, content: dict) -> int:
    """The one place a stage's content is fingerprinted and looked up (or added) in versions.json."""
    return versions.version_of(sdir, stage, versions.fingerprint(content, sdir))


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


def build(sdir: Path, *, settings: dict | None = None, now: float | None = None) -> dict:
    sdir = Path(sdir)
    ctx = load_ctx(sdir, settings)
    problems = [p for p in (ctx.short_problem, ctx.state_problem) if p]
    done = set(ctx.state["stages"]) if ctx.state else set()
    status = stages.statuses(done)
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
    rows = [_stage(ctx, s, status[s], live, problems) for s in stages.PAGE_STAGES]
    current = next((s for s in stages.PAGE_STAGES if status[s] == "current"), stages.PAGE_STAGES[-1])
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
        "health": [],
        "feedback": panel,
        "phone": None,
    }


def etag(snap: dict) -> str:
    """A quoted sha256 of the snapshot without its `generated` time: unchanged content, unchanged etag."""
    body = {k: v for k, v in snap.items() if k != "generated"}
    raw = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)
    return '"' + hashlib.sha256(raw.encode("utf-8")).hexdigest() + '"'
