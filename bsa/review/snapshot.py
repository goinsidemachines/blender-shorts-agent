"""The review page's snapshot: everything the page shows about one short, built fresh from the files on disk.

Reading never writes anything except stage versions (and only when what the user sees changed). A damaged input
(short.json, state.json, a store file) becomes a plain problem line for the part it affects; nothing is reset and
nothing raises."""

from __future__ import annotations

import hashlib
import json
import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from bsa import jobstate, messages
from bsa import settings as settings_mod
from bsa.review import decisions, notes, stages, versions
from bsa.short import Short, load_short

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


def _later(ctx: Ctx) -> dict | None:
    return None


BUILDERS: dict[str, Callable[[Ctx], dict | None]] = {
    "interview": _interview,
    "story": _later,
    "storyboard": _later,
    "engine": _later,
    "narration": _later,
    "render": _later,
    "final": _later,
    "post": _later,
}


def stage_version(sdir: Path, stage: str, ctx: Ctx | None = None) -> int | None:
    """The stage's current version: its content fingerprinted and looked up (or added) in versions.json."""
    if stage not in VERSIONED:
        return None
    ctx = ctx or load_ctx(sdir)
    content = BUILDERS[stage](ctx)
    if content is None:
        return None
    return versions.version_of(sdir, stage, versions.fingerprint(content, sdir))


def _stage(ctx: Ctx, stage: str, status: str, live: list[dict]) -> dict:
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
        content = BUILDERS[stage](ctx)
        out["content"] = content
        if content is not None and stage in VERSIONED:
            out["version"] = versions.version_of(ctx.sdir, stage, versions.fingerprint(content, ctx.sdir))
    except Exception as e:  # noqa: BLE001 - a bad input must never break the page
        out["content"], out["version"] = None, None
        out["problem"] = _plain(ctx.sdir, e)
    mine = [d for d in live if d.get("stage") == stage]
    out["decision"] = mine[-1] if mine else None
    v = out["version"]
    if status == "current" and v is not None:
        approves = [d for d in mine if d.get("decision") == "approve" and d.get("version") == v]
        if stage in stages.APPROVAL_STAGES:
            out["needs_yes"] = not approves
        out["waiting_for_claude"] = any(not d.get("applied") for d in approves)
    return out


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
    rows = [_stage(ctx, s, status[s], live) for s in stages.PAGE_STAGES]
    current = next((s for s in stages.PAGE_STAGES if status[s] == "current"), stages.PAGE_STAGES[-1])
    short = ctx.short
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
        "feedback": None,
        "phone": None,
    }


def etag(snap: dict) -> str:
    """A quoted sha256 of the snapshot without its `generated` time: unchanged content, unchanged etag."""
    body = {k: v for k, v in snap.items() if k != "generated"}
    raw = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)
    return '"' + hashlib.sha256(raw.encode("utf-8")).hexdigest() + '"'
