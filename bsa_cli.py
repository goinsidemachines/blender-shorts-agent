"""blender-shorts-agent command line (everything that does not need Blender itself).

Run it with Blender's bundled Python (`where` prints its path); `where` and `settings` also work with any Python 3.11+.
Every command prints one JSON object on stdout. On failure it exits non-zero with one plain line on stderr.
"""

from __future__ import annotations

import argparse
import copy
import json
import sys
from dataclasses import asdict
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parent
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))

from bsa import jobstate, messages, settings, workspace  # needs the plugin root on sys.path (above)
from bsa.review import store  # needs the plugin root on sys.path (above)

ERROR_LIMIT = 600  # the one plain error line is trimmed at a sentence or clause boundary past this


class CliError(Exception):
    """A plain-language failure: the message goes to stderr, the payload (if any) to stdout."""

    def __init__(self, message: str, payload: dict | None = None):
        super().__init__(message)
        self.payload = payload


# --------------------------------------------------------------------------- helpers


def short_dir(key: str) -> Path:
    return workspace.resolve_short(key, settings.load_settings().get("workspace"))


def short_json(sdir: Path) -> Path:
    return sdir / "source" / "short.json"


def load(sdir: Path):
    from bsa.short import load_short

    path = short_json(sdir)
    if not path.is_file():
        raise CliError(f"{path} does not exist yet: write the short's spec first")
    try:
        return load_short(path)
    except (ValueError, TypeError) as exc:
        raise CliError(f"{path} is not a valid short spec: {exc}") from exc


def current_timeline(sdir: Path, short):
    """The timeline from the takes when narration finished with a voice, else from reading time."""
    from bsa.narrate.run import takes_durations, takes_onsets
    from bsa.timeline import build_timeline

    durations = takes_durations(sdir, short)
    end_card = settings.has_end_card(settings.load_settings(), short)
    onsets = takes_onsets(sdir, short) if durations is not None else None  # captions wait for the voice
    return build_timeline(short, durations, end_card=end_card, onsets=onsets), durations is not None


def built_timeline(sdir: Path, short):
    """current_timeline, which must match source/timeline.json (the timing build.py animated)."""
    from bsa import freshness

    tl, from_takes = current_timeline(sdir, short)
    try:
        freshness.check_timeline(sdir, tl)
    except freshness.StaleError as exc:
        raise CliError(str(exc)) from exc
    return tl, from_takes


_STRING_KEYS = {"workspace", "blender_exe", "handle", "end_card", "website", "closing.image", "closing.voice_line", "narrator.provider", "narrator.voice", "narrator.command",
                "narrator.label", "render.engine", "post_format.title_suffix", "post_format.link_line",
                "post_format.pinned_comment", "post_format.title_style",
                "brand.mode", "sound_policy", "freesound.api_key", "feedback"}  # fmt: skip


def _parse_value(key: str, text: str):
    """JSON for numbers, booleans and objects; plain text for keys that hold text ("123" stays a string there).
    A brand.* value stays text unless it is a JSON object or list (a palette, say)."""
    if key in _STRING_KEYS:
        return text
    try:
        value = json.loads(text)
    except ValueError:
        return text
    if key.startswith("brand.") and not isinstance(value, dict | list):
        from bsa import kit

        return value if kit.json_scalar(key) else text
    return value


def _state(sdir: Path) -> dict:
    return {"short": str(sdir), "stages": jobstate.load_state(sdir)["stages"], "next": jobstate.next_stage(sdir)}


# --------------------------------------------------------------------------- commands


def cmd_where(args) -> dict:
    from bsa import locate

    s = settings.load_settings()
    out = {
        "plugin_root": str(PLUGIN_ROOT),
        "bsa_cli": str(PLUGIN_ROOT / "bsa_cli.py"),
        "bsa_blender": str(PLUGIN_ROOT / "bsa_blender.py"),
        "python": sys.executable,
        "settings_path": str(settings.settings_path()),
        "workspace": s.get("workspace"),
        "blender": None,
        "blender_python": None,
        "problems": [],
    }
    try:
        exe = locate.find_blender(s)
    except locate.BlenderNotFound as exc:
        out["problems"].append(str(exc))
        return out
    out["blender"] = str(exe)
    problems = locate.check_blender(exe)
    out["problems"] += problems
    if not problems:
        out["blender_python"] = str(locate.blender_python(exe))
    return out


def _rules_out() -> dict:
    from bsa import rules

    s = settings.load_settings()
    return {"rules": rules.global_rules(s), "asked": bool(s.get("rules_asked")),
            "kinds": {k: v.label for k, v in rules.KINDS.items()}}  # fmt: skip


def cmd_rule(args) -> dict:
    """settings rule add TEXT [--kind K] [--part-of ID] | remove ID | none."""
    from bsa import rules

    try:
        if args.key == "add" and args.value is not None:
            rule = rules.add_rule(args.value, kind=args.kind, part_of=args.part_of)
        elif args.key == "remove" and args.value:
            rule = rules.remove_rule(args.value)
        elif args.key == "none" and args.value is None:
            rules.mark_none()
            rule = None
        else:
            raise CliError('usage: settings rule add "TEXT" [--kind KIND] [--part-of ID] | rule remove ID | rule none')
    except ValueError as exc:
        raise CliError(str(exc)) from exc
    return {"rule": rule, **_rules_out(), "summary": settings.summary_line(settings.load_settings())}


def cmd_settings(args) -> dict:
    if args.action == "show":
        s = settings.load_settings()
        return {"settings": settings.redacted(s), "rules": s.get("rules") or [], "path": str(settings.settings_path())}
    if args.action == "rules":
        return _rules_out()
    if args.action == "post-format":
        from bsa import post

        s = settings.load_settings()
        return {"post_format": s.get("post_format") or {}, "asked": bool(s.get("post_format_asked")),
                "keys": list(post.FORMAT_KEYS)}  # fmt: skip
    if args.action == "rule":
        return cmd_rule(args)
    if args.key in ("rules", "rules_next"):
        raise CliError('hard rules are changed with settings rule add "TEXT" [--kind KIND] or settings rule remove ID')
    if args.action == "missing":
        return {
            "missing": settings.missing(settings.load_settings()),
            "default_workspace": str(workspace.default_workspace(Path.cwd())),
        }
    if args.action == "summary":
        return {"summary": settings.summary_line(settings.load_settings())}
    if args.action == "check":
        from bsa import kit

        s = settings.load_settings()
        return {"advice": kit.advice(s), "problems": kit.problems(s)}
    # set
    if not args.key or args.value is None:
        raise CliError("usage: settings set KEY VALUE (for example: settings set render.engine cycles)")
    try:
        value = settings.check_value(args.key, _parse_value(args.key, args.value))
    except ValueError as exc:
        raise CliError(str(exc)) from exc
    if args.key == "closing.image" and isinstance(value, str) and value.strip():
        path = Path(value.strip()).expanduser().resolve()
        if not path.is_file() or path.suffix.lower() not in (".png", ".jpg", ".jpeg"):
            raise CliError(f"closing.image must be a PNG or JPG file (1080x1920); {path} is not one")
        value = str(path)
    if args.key == "closing.seconds":
        from bsa import closing

        if isinstance(value, bool) or not isinstance(value, int | float):
            raise CliError(f"closing.seconds must be a number of seconds, e.g. 2.5, got {args.value!r}")
        value = closing.clamp_seconds(value)
    if args.key in ("website", "closing.image", "closing.voice_line") and isinstance(value, str):
        value = value.strip() or None  # "" = none
    if args.key in ("workspace", "blender_exe") and isinstance(value, str):
        value = str(Path(value).expanduser().resolve())
    if args.key == "workspace":
        workspace.ensure_workspace(Path(value))
    if args.key == "handle":
        value = value.strip().lstrip("@")  # "" = asked, and the user wants no handle
    s = settings.set_value(args.key, value)
    if args.key == "post_format" or args.key.startswith("post_format."):
        s = settings.set_value("post_format_asked", True)
    out = {"settings": settings.redacted(s), "summary": settings.summary_line(s)}
    font = value if args.key == "brand.font" else (value.get("font") if args.key == "brand" and isinstance(
        value, dict) else None)  # fmt: skip
    if isinstance(font, str) and font.strip():  # checked once here, as CLI fonts does: not on every short
        from bsa import fonts

        out["font_check"] = fonts.brand_font_check(font)
    return out


def cmd_machine(args) -> dict:
    from bsa import machine

    m = machine.detect()
    out = asdict(m)
    for g, d in zip(m.gpus, out["gpus"], strict=True):
        d["free_gb"] = g.free_gb
    out["summary"] = machine.describe(m)
    out["vram_warning"] = machine.vram_warning(m, args.need_gb) if args.need_gb else None
    return out


def cmd_new_short(args) -> dict:
    from bsa.short import Short, save_short

    root = settings.load_settings().get("workspace")
    if not root:
        raise CliError("No workspace is set yet: run settings set workspace PATH first.")
    length = 45 if args.length is None else args.length  # the interview's default
    if isinstance(length, float) and length.is_integer():
        length = int(length)  # "--length 40" is stored as 40, not 40.0
    if not LENGTH_RANGE_S[0] <= length <= LENGTH_RANGE_S[1]:
        raise CliError(f"--length must be {LENGTH_RANGE_S[0]} to {LENGTH_RANGE_S[1]} seconds, got {length:g}")
    try:
        sdir = workspace.new_short(Path(root), args.title, number=args.number)
    except ValueError as exc:
        raise CliError(str(exc)) from exc
    path = short_json(sdir)
    if not path.exists():
        save_short(Short(slug=workspace.slugify(args.title), title=args.title, length_s=length,
                         topic=(args.topic or "").strip()), path)
    return {"short": str(sdir), "slug": sdir.name.split("-", 1)[1], "next": jobstate.next_stage(sdir)}


LENGTH_RANGE_S = (15, 90)


def _rule_problems(short) -> tuple[list[str], list[str]]:
    """(errors, warnings) of the short against the user's hard rules and its own (bsa.rules.problems)."""
    from bsa import rules

    return rules.problems(short, settings.load_settings())


def _gate(sdir: Path, stage: str) -> None:
    """The interview is done only when short.json has a title, topic, audience and a length the user agreed; the
    story only when the spec validates."""
    from bsa.short import validate

    short = load(sdir)
    if stage == "interview":
        lo, hi = LENGTH_RANGE_S
        gaps = [name for name in ("title", "topic", "audience") if not str(getattr(short, name) or "").strip()]
        try:
            length_ok = lo <= float(short.length_s) <= hi
        except (TypeError, ValueError):
            length_ok = False
        if not length_ok:
            gaps.append(f"a length_s between {lo} and {hi} seconds (it is {short.length_s!r})")
        if gaps:
            raise CliError(f"The interview isn't finished: short.json still needs {', '.join(gaps)}. Ask the user, "
                           "write the answers into short.json, then mark the interview done.")  # fmt: skip
        return
    errors = validate(short) + _rule_problems(short)[0]
    if errors:
        raise CliError(f"The story can't be marked done while CLI validate SHORT reports {len(errors)} problem(s): "
                       + "; ".join(errors))  # fmt: skip


def cmd_state(args) -> dict:
    sdir = short_dir(args.short)
    if args.action == "done":
        info = {}
        if args.stage is None:
            raise CliError("usage: state SHORT done STAGE [--info JSON]")
        if args.info:
            try:
                info = json.loads(args.info)
            except ValueError as exc:
                raise CliError(f"--info is not valid JSON: {exc}") from exc
            if not isinstance(info, dict):
                raise CliError("--info must be a JSON object")
        if args.stage in ("interview", "story"):
            _gate(sdir, args.stage)
        if args.stage in jobstate.APPROVALS:
            _require_approval(sdir, args.stage)
        if args.stage == "build":  # a rebuild invalidates the checks and every render made from the old build
            jobstate.reset_from(sdir, "checks")
        jobstate.mark_done(sdir, args.stage, **info)
    elif args.action == "reset":
        if args.stage is None:
            raise CliError("usage: state SHORT reset STAGE")
        from bsa.review import decisions, stages

        later = jobstate.STAGES[jobstate.STAGES.index(args.stage) :]
        decisions.supersede(sdir, [s for s in (*stages.APPROVAL_STAGES, *stages.ACK_STAGES) if s in later])
        jobstate.reset_from(sdir, args.stage)
    elif args.action == "restore":  # state.json damaged: put state.json.bak back
        jobstate.restore(sdir)
    elif args.action is not None:
        raise CliError("usage: state SHORT [done STAGE [--info JSON] | reset STAGE | restore]")
    return _state(sdir)


def _require_approval(sdir: Path, stage: str) -> None:
    """story, storyboard and engine are marked done only through `page decide`, which leaves an applied approval of
    the stage's current version."""
    from bsa.review import decisions, snapshot

    version = snapshot.stage_version(sdir, stage)
    found = decisions.approved(sdir, stage, version) if version is not None else None
    if not found or not found["applied"]:
        raise CliError(f"Approvals are recorded with CLI page decide SHORT {stage} approve --by page|chat (the {stage} "
                       f"has no approval for its current version {version})")  # fmt: skip


def _apply_decision(sdir: Path, entry: dict) -> dict:
    """Act on a decision: claim it (marks it applied, once, under the store lock); the winner marks an approved
    approval stage done (after the story's gate). A failure gives the claim back and is raised. Returns the decision
    as stored."""
    from bsa.review import decisions

    if not decisions.claim(sdir, entry["id"]):  # decide and wait raced: the other one is acting on it
        return decisions.get(sdir, entry["id"])
    try:
        if entry["decision"] == "approve" and entry["stage"] in jobstate.APPROVALS:
            _mark_approved(sdir, entry)
    except BaseException:
        decisions.unclaim(sdir, entry["id"])
        raise
    return decisions.get(sdir, entry["id"])


def _mark_approved(sdir: Path, entry: dict) -> None:
    """Mark an approval stage done from its approval (after the story's gate). Idempotent: the same fields again."""
    if entry["stage"] == "story":
        _gate(sdir, "story")
    jobstate.mark_done(sdir, entry["stage"], decision=entry["id"], version=entry["version"], by=entry["by"],
                       **(entry["choice"] or {}))  # fmt: skip


def _repair_half_applied(sdir: Path, stage: str | None = None) -> list[dict]:
    """Finish approvals that were claimed (applied) but whose stage never reached state.json: the power went off
    between decisions.claim and jobstate.mark_done. Only an approval of the stage's current version counts. Returns
    the approvals repaired."""
    from bsa.review import decisions, snapshot

    done = jobstate.load_state(sdir)["stages"]
    found = {d["stage"] for d in decisions.load(sdir)
             if d["decision"] == "approve" and d["applied"] and not d["superseded"]
             and d["stage"] in jobstate.APPROVALS and d["stage"] not in done and stage in (None, d["stage"])}  # fmt: skip
    repaired = []
    for s in sorted(found, key=jobstate.STAGES.index):
        version = snapshot.stage_version(sdir, s)
        entry = decisions.approved(sdir, s, version) if version is not None else None
        if entry is not None and entry["applied"]:
            _mark_approved(sdir, entry)
            repaired.append(entry)
    return repaired


def _page_stage(stage: str | None) -> str:
    from bsa.review import stages

    if stage not in stages.PAGE_STAGES:
        raise CliError(f"{stage!r} is not a page stage; expected one of {', '.join(stages.PAGE_STAGES)}")
    return stage


def _page_decide_thumbnail(args, sdir: Path) -> dict:
    """page decide SHORT thumbnail --choice N [--by page|chat]: use candidate N (its files to out/, the choice
    recorded in review/thumbs.json and short.json)."""
    from bsa import thumbs

    if len(args.words) != 3:
        raise CliError("usage: page decide SHORT thumbnail --choice N [--by page|chat]")
    if args.note is not None or args.version is not None:
        raise CliError("page decide SHORT thumbnail takes only --choice N and --by page|chat (no --note or --version): "
                       "leave a note on a candidate with the page, or tell Claude in the chat")  # fmt: skip
    by = args.by or "chat"
    if by not in ("page", "chat"):
        raise CliError("A thumbnail is chosen on the page or in the chat: --by page|chat")
    digits = args.choice.strip() if isinstance(args.choice, str) else ""
    if not (digits.isascii() and digits.isdigit()):
        raise CliError(f"page decide SHORT thumbnail needs --choice N, the candidate's number (not {args.choice!r})")
    try:
        return {"thumbnail": thumbs.choose(sdir, int(digits), by)}
    except (ValueError, store.StoreDamaged, TimeoutError) as exc:
        raise CliError(str(exc)) from exc


def _page_decide(args, sdir: Path) -> dict:
    from bsa import engine_runs
    from bsa.review import decisions, notes, snapshot, stages

    if len(args.words) >= 3 and args.words[2] == "thumbnail":
        return _page_decide_thumbnail(args, sdir)
    if len(args.words) != 4:
        raise CliError("usage: page decide SHORT STAGE approve|changes --by page|chat|settings")
    stage, decision = _page_stage(args.words[2]), args.words[3]
    if stage not in (*stages.APPROVAL_STAGES, *stages.ACK_STAGES):
        raise CliError(f"The {stages.LABELS[stage]} has nothing to approve.")
    if decision not in decisions.DECISIONS:
        raise CliError(f"usage: page decide SHORT STAGE approve|changes (not {decision!r})")
    if args.by is None:
        raise CliError("page decide needs --by page|chat|settings")
    if args.by == "settings" and decision != "approve":
        raise CliError("Only an engine approval can be answered from settings.")
    choice = {"engine": args.choice} if args.choice is not None else None
    if args.by == "settings":  # the engine the page shows as already chosen: short.json's, else the setting
        setting = (settings.load_settings().get("render") or {}).get("engine")
        wanted, source = engine_runs.pick(None, engine_runs.short_engine(sdir), setting)
        if args.choice is None or args.choice != wanted:
            where = "short.json's render.engine" if source == "short.json" else "The render.engine setting"
            raise CliError(f"{where} is {wanted!r}, so it can't answer {args.choice!r}: ask the user which engine "
                           "to use, then record it with --by chat.")  # fmt: skip
    current = snapshot.stage_version(sdir, stage)
    if current is None:
        raise CliError(f"The {stages.LABELS[stage]} has nothing to answer yet.")
    version = args.version if args.version is not None else current
    if decision == "changes":
        open_notes = [n for n in notes.load(sdir) if n["stage"] == stage and n["status"] in ("open", "sent")]
        if not open_notes and not args.note:
            raise CliError("Add a note saying what to change first.")
    elif stage == "story" and version == current:
        _gate(sdir, "story")  # a story that doesn't validate is refused before anything is recorded
    if args.note:
        notes.add(sdir, stage, {"kind": "overall", "id": None, "version": version}, args.note, "chat")
    try:
        entry = decisions.record(sdir, stage, version, decision, args.by, current=current, choice=choice)
    except decisions.DecisionRefused as exc:
        raise CliError(str(exc)) from exc
    sent = notes.send_stage(sdir, stage) if decision == "changes" or args.note else []
    entry = _apply_decision(sdir, entry)
    return {"decision": entry, "state": _state(sdir), "notes_sent": sent}


def _page_note(args, sdir: Path) -> dict:
    from bsa.review import notes

    if len(args.words) != 4 or args.words[3] not in notes.ANSWERS or args.reply is None:
        raise CliError("usage: page note SHORT ID done|declined --reply TEXT")
    try:
        return {"note": notes.answer(sdir, args.words[2], args.words[3], args.reply)}
    except ValueError as exc:
        raise CliError(str(exc)) from exc


NOTES_AFTER_CHANGES_S = 5.0  # how long `page wait` waits for the notes a "changes" answer sends (a second write)


def _with_findings(sdir: Path, notes: list[dict]) -> list[dict]:
    """Each note on a finding gains that finding's row ("finding"), or "gone": True when no check reports it."""
    from bsa.review import pages

    out = []
    for n in notes:
        target = n.get("target")
        if isinstance(target, dict) and target.get("kind") == "finding":
            row = pages.resolve_finding(sdir, str(target.get("id")))
            n = {**n, "gone": True} if row is None else {**n, "finding": row}
        out.append(n)
    return out


def _page_wait(args, sdir: Path) -> dict:
    """Wait until the stage has something for Claude: an answer to apply (applied here), sent notes (this stage's,
    or newly sent on any stage), or an answer applied elsewhere since the wait began (`page decide --by chat`), which
    also ends it. A half-applied approval (power off between the claim and state.json) is repaired."""
    import time

    from bsa.review import decisions, notes, snapshot, stages

    if len(args.words) != 2 or args.stage is None:
        raise CliError("usage: page wait SHORT --stage STAGE [--timeout S] [--poll S]")
    stage = _page_stage(args.stage)
    deadline = time.monotonic() + (args.timeout if args.timeout is not None else 600.0)
    poll = max(args.poll, 0.05)

    def sent_notes(of: str | None = None) -> list[dict]:
        return [n for n in notes.load(sdir) if n["status"] == "sent" and of in (None, n["stage"])]

    def applied_approvals() -> list[dict]:
        return [d for d in decisions.load(sdir) if d["stage"] == stage and d["decision"] == "approve"
                and d["applied"] and not d["superseded"]]  # fmt: skip

    def stage_done() -> bool:
        return stages.statuses(jobstate.load_state(sdir)["stages"])[stage] == "done"

    old_sent = {n["id"] for n in sent_notes() if n["stage"] != stage}  # other stages' notes sent before: not news
    old_applied = {d["id"] for d in applied_approvals()}
    was_done = stage_done()
    superseded: list[str] = []

    def news() -> list[dict]:
        return [n for n in sent_notes() if n["stage"] == stage or n["id"] not in old_sent]

    def result(version, entry, sent, *, applied=None, timed_out=False, repaired=()) -> dict:
        return {"stage": stage, "version": version, "decision": entry, "notes": _with_findings(sdir, sent),
                "note_stages": sorted({n["stage"] for n in sent}),
                "applied": entry is not None if applied is None else applied,
                "by": entry["by"] if entry else None, "timed_out": timed_out, "superseded": superseded,
                "repaired": [d["id"] for d in repaired]}  # fmt: skip

    while True:
        version = snapshot.stage_version(sdir, stage)
        superseded += decisions.supersede_stale(sdir, stage, version)  # answers to content that has changed since
        repaired = _repair_half_applied(sdir, stage)
        if repaired:
            return result(version, repaired[-1], news(), repaired=repaired)
        waiting = [d for d in decisions.unapplied(sdir) if d["stage"] == stage and d["version"] == version]
        if waiting:
            entry = _apply_decision(sdir, waiting[-1])
            if entry["decision"] == "changes":  # the server records the answer, then sends the notes: wait for them
                until = time.monotonic() + NOTES_AFTER_CHANGES_S
                while not sent_notes(stage) and time.monotonic() < until:
                    time.sleep(0.05)
            return result(version, entry, news())
        new = [d for d in applied_approvals() if d["id"] not in old_applied and d["version"] == version]
        if new or (not was_done and stage_done()):  # answered elsewhere (page decide --by chat) since the wait began
            return result(version, new[-1] if new else None, news(), applied=True)
        sent = news()
        if sent:
            return result(version, None, sent)
        if time.monotonic() >= deadline:
            return result(version, None, sent, timed_out=True)
        time.sleep(min(poll, max(deadline - time.monotonic(), 0.0)))


def _page_notes(args, sdir: Path) -> dict:
    """What `page_notes` points at, in full: the notes not answered yet (open: the user is still writing; sent: for
    Claude to act on) and the decisions not applied yet (`page wait --stage STAGE` applies one)."""
    from bsa.review import decisions, notes

    if len(args.words) != 2:
        raise CliError("usage: page notes SHORT [--stage STAGE]")
    stage = _page_stage(args.stage) if args.stage is not None else None
    from bsa.review import snapshot

    try:
        superseded = []
        for s in sorted({d["stage"] for d in decisions.unapplied(sdir)}):
            superseded += decisions.supersede_stale(sdir, s, snapshot.stage_version(sdir, s))
        repaired = _repair_half_applied(sdir)  # an approval the power cut off before state.json was written
        found = [n for n in notes.load(sdir) if n["status"] in ("open", "sent")]
        waiting = decisions.unapplied(sdir)
    except store.StoreDamaged as exc:
        raise CliError(str(exc)) from exc
    snap = snapshot.build(sdir)
    keys = ("id", "stage", "target", "text", "status", "by")
    shown = [{k: n.get(k) for k in keys} for n in found if stage in (None, n["stage"])]
    return {"notes": _with_findings(sdir, shown),
            "decisions": [{k: d.get(k) for k in ("id", "stage", "version", "decision")} for d in waiting
                          if stage in (None, d["stage"])],
            "superseded": superseded, "repaired": [d["id"] for d in repaired],
            "waiting_for_yes": [s["id"] for s in snap["stages"] if s["needs_yes"]]}  # fmt: skip


def cmd_page(args) -> dict:
    if args.words[0] in ("decide", "note", "notes", "wait"):
        if len(args.words) < 2:
            raise CliError(f"usage: page {args.words[0]} SHORT ...")
        args.short = args.words[1]
        sdir = short_dir(args.short)
        handlers = {"decide": _page_decide, "note": _page_note, "notes": _page_notes, "wait": _page_wait}
        return handlers[args.words[0]](args, sdir)
    if args.words[0] == "serve":
        return _page_serve(args)
    if len(args.words) != 1:
        raise CliError(f"usage: page SHORT [--open] [--stage STAGE]; unexpected {' '.join(args.words[1:])!r}")
    args.short = args.words[0]
    return _page_open(args, short_dir(args.short))


def _page_serve(args) -> dict:
    """The detached page server's own process: serve the workspace until idle."""
    from bsa.review import launch

    ws = args.workspace or settings.load_settings().get("workspace")
    if not ws or not Path(ws).is_dir():
        raise CliError("page serve needs --workspace PATH (an existing folder) or the workspace setting")
    launch.run_server(Path(ws).resolve(), args.lan)
    return {"stopped": True}


def _page_open(args, sdir: Path) -> dict:
    """Start or reuse the workspace's server, run doctor when this short has never had one, and open the page."""
    from bsa.review import launch, stages

    if args.stage is not None and args.stage not in stages.PAGE_STAGES:
        raise CliError(f"unknown stage {args.stage!r}; expected one of {', '.join(stages.PAGE_STAGES)}")
    ws = sdir.parent  # the server serves the shorts of the folder this one is in
    lan = bool(args.lan or (settings.load_settings().get("page") or {}).get("lan") is True)
    try:
        info, failed = launch.ensure_server(ws, lan=lan), None
    except launch.ServerUnavailable as exc:
        info, failed = None, exc
    doctor_json = sdir / "review" / "doctor.json"
    if not doctor_json.is_file():
        _doctor_checks(sdir, ws, info, save=True)

    def ensure(_ws):
        if failed is not None:
            raise failed
        return info

    out = launch.open_page(ws, sdir, stage=args.stage, force=args.open, ensure=ensure)
    try:
        checks = json.loads(doctor_json.read_text("utf-8")).get("checks") or []
    except (OSError, ValueError, AttributeError):
        checks = []
    health = [c for c in checks if isinstance(c, dict) and c.get("level") != "ok"
              and not (c.get("id") == "server" and out["mode"] == "server")]  # fmt: skip
    phone = launch.phone_url(info, sdir.name) if info is not None else None  # with page.lan only
    return {**out, "health": health, "phone": phone}


def _doctor_checks(sdir: Path | None, ws: Path | None, server: dict | None, save: bool) -> list[dict]:
    from bsa import doctor, fonts

    short = None
    if sdir is not None and short_json(sdir).is_file():
        try:
            short = load(sdir)
        except CliError:
            short = None  # a bad spec is validate's finding; the font check falls back to the brand font
    checks = doctor.run(settings.load_settings(), ws, short, server=server, cache_dirs=fonts.font_caches(sdir))
    if save and sdir is not None:
        doctor.save(sdir, checks)
    return checks


def cmd_doctor(args) -> dict:
    from bsa.review import launch

    sdir = short_dir(args.short) if args.short else None
    ws = settings.load_settings().get("workspace")
    ws = sdir.parent if sdir is not None else Path(ws) if ws else None  # where the short really is, as page SHORT
    checks = _doctor_checks(sdir, ws, launch.server_info(ws) if ws else None, save=True)
    return {"checks": checks, "ok": not any(c["level"] == "error" for c in checks)}


def cmd_engine(args) -> dict:
    """The engine this short renders with and where that comes from (the engine stage, short.json's render.engine,
    or the setting), plus the seconds per frame each engine's runs measured and their MP4s (bsa.engine_runs)."""
    from bsa import engine_runs

    sdir = short_dir(args.short)
    approved = (jobstate.load_state(sdir)["stages"].get("engine") or {}).get("engine")
    own = engine_runs.short_engine(sdir)
    setting = (settings.load_settings().get("render") or {}).get("engine")
    engine, source = engine_runs.pick(approved, own, setting)
    return {"short": str(sdir), "engine": engine, "from": source,
            "approved": approved if approved in engine_runs.ENGINES else None, "short_json": own,
            "setting": setting, "runs": engine_runs.compare(sdir)}  # fmt: skip


def _sound_library(sdir: Path):
    """The workspace's recorded clips, after indexing any pack an earlier version left in `.assets-cache/sfx/`."""
    from bsa.assets import polyhaven, sounds
    from bsa.audio import recorded

    cache = polyhaven.cache_dir(sdir)
    sounds.adopt_legacy(cache)
    return recorded.Library(cache)


def _font_and_sound_warnings(sdir: Path, short) -> list[str]:
    """The style font's missing glyphs and wrong weight (variable font), and sfx the sound policy forbids or that
    have no cached recording."""
    from bsa import fonts
    from bsa.audio import recorded

    s = settings.load_settings()
    return fonts.glyph_warnings(short, s, cache_dirs=fonts.font_caches(sdir)) + recorded.policy_warnings(
        short, _sound_library(sdir), recorded.sound_policy(short, s))


def _pacing(short, tl) -> tuple[list, list]:
    """(per-shot spoken vs silent seconds, warnings for shots that are mostly silent)."""
    from bsa.timeline import pacing, pacing_warnings

    end_card = settings.has_end_card(settings.load_settings(), short)
    return pacing(short, tl, end_card), pacing_warnings(short, tl, end_card)


def _reading_timeline(short):
    """The timeline at reading pace (no takes)."""
    from bsa.timeline import build_timeline

    return build_timeline(short, None, settings.has_end_card(settings.load_settings(), short))


def cmd_validate(args) -> dict:
    from bsa import post
    from bsa.short import spoken_warnings, validate
    from bsa.timeline import length_warning

    sdir = short_dir(args.short)
    short = load(sdir)
    from bsa import kit

    rule_errors, rule_warnings = _rule_problems(short)
    errors = validate(short) + rule_errors
    post_errors, post_warnings = post.problems(short, settings.load_settings())
    errors += post_errors + kit.errors(settings.load_settings(), short)  # cards the kit can't lay out
    warnings = spoken_warnings(short) if not errors else []
    warnings += rule_warnings + post_warnings
    rows, source = [], None
    if not errors:
        if short.length_s > 0:
            msg = length_warning(short, _reading_timeline(short))
            if msg:
                warnings.append(msg)
        try:
            tl, from_takes = current_timeline(sdir, short)
        except Exception:  # noqa: BLE001 - broken takes: pace it at reading speed (narrate reports the takes)
            tl, from_takes = _reading_timeline(short), False
        rows, pace_warnings = _pacing(short, tl)
        warnings += pace_warnings
        source = "takes" if from_takes else "reading pace"
    warnings += _font_and_sound_warnings(sdir, short)
    warnings += kit.problems(settings.load_settings(), short)
    out = {"ok": not errors, "errors": errors, "warnings": warnings, "pacing": rows, "pacing_from": source}
    if errors:
        raise CliError(f"short.json has {len(errors)} problem(s): " + "; ".join(errors), out)
    return out


def cmd_timeline(args) -> dict:
    from bsa.timeline import length_drift, to_json

    sdir = short_dir(args.short)
    short = load(sdir)
    tl, from_takes = current_timeline(sdir, short)
    data = to_json(tl)
    path = sdir / "source" / "timeline.json"
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    rows, warnings = _pacing(short, tl)
    return {
        "path": str(path),
        "n_frames": tl.n_frames,
        "seconds": round(tl.n_frames / tl.fps, 2),
        "takes": from_takes,
        "length_drift": round(length_drift(short, tl), 3) if short.length_s else None,
        "shots": [asdict(s) for s in tl.shots],
        "pacing": rows,
        "warnings": warnings,
    }


def _restore_file(path: Path, data: bytes | None) -> str | None:
    """Put a file's saved bytes back (or remove it when there were none). Returns why it couldn't, never raises."""
    try:
        if data is None:
            path.unlink(missing_ok=True)
        else:
            path.write_bytes(data)
    except OSError as exc:
        return str(exc)
    return None


def _prepare_retake(sdir: Path, short, voiced, s: dict, args) -> dict | None:
    """narrate --line ID [--say TEXT]: refuse (nothing touched) what can't be re-taken, write the respelling, and
    throw the line's take away with the next seed. Returns {"line", "seed"}, or None without --line."""
    from bsa.narrate import run as nrun
    from bsa.short import save_short, validate

    line_id, say = getattr(args, "line", None), getattr(args, "say", None)
    if line_id is None:
        if say is not None:
            raise CliError("--say needs --line: say which line to re-take with these words.")
        return None
    if say is not None and not say.strip():
        raise CliError("--say can't be empty: give the words the voice should say.")
    if "narration" not in jobstate.load_state(sdir)["stages"]:
        raise CliError("Narrate the short first: CLI narrate SHORT")
    if (s.get("narrator") or {}).get("provider") in (None, "none"):
        raise CliError("This short uses captions only, so there is no take to re-take.")
    ids = [ln.id for ln in voiced.lines]
    if line_id not in ids:
        raise CliError(f"No line {line_id!r}; the lines are {', '.join(ids)}")
    edited = None
    if say is not None:
        from bsa import closing

        if line_id == closing.LINE_ID:
            raise CliError("The closing line's words are the closing setting's voice_line: change it there.")
        edited = copy.deepcopy(short)
        next(ln for ln in edited.lines if ln.id == line_id).say = say
        problems = validate(edited)
        if problems:
            raise CliError("That respelling isn't valid: " + "; ".join(problems))
    problem = nrun.retake_problem(sdir / "audio" / "vo", line_id)  # every refusal before anything is written
    if problem:
        raise CliError(problem)
    try:
        seed = nrun.retake(sdir / "audio" / "vo", line_id)
    except (ValueError, OSError) as exc:
        raise CliError(_one_line(exc)) from exc
    if edited is not None:
        try:
            save_short(edited, short_json(sdir))
        except OSError as exc:
            raise CliError(f"The take was thrown away (next seed {seed}), but short.json couldn't be saved with the "
                           f"new words ({_one_line(exc)}): run the same command again.") from exc  # fmt: skip
        new = {ln.id: ln for ln in edited.lines}
        short.lines = edited.lines  # the run below speaks the new words
        voiced.lines = [new.get(ln.id, ln) for ln in voiced.lines]
    return {"line": line_id, "seed": seed}


def cmd_narrate(args) -> dict:
    from bsa.narrate.run import HEARD_FILE, check_line_ids, clear_stale_takes, narrate

    sdir = short_dir(args.short)
    if "storyboard" not in jobstate.load_state(sdir)["stages"]:  # binding: narration (paid credit) only after a yes
        raise CliError("Narration is made only after the storyboard is approved: show the storyboard on the page "
                       "and get a yes first (CLI page wait SHORT --stage storyboard, or CLI page decide SHORT "
                       "storyboard approve --by chat).")  # fmt: skip
    short = load(sdir)
    s = settings.load_settings()
    from bsa import closing

    voiced = closing.with_voice_line(short, closing.resolve(s, short))  # the closing line, in the same voice
    narrator = s.get("narrator") or {}
    try:
        check_line_ids(voiced)  # before anything is deleted or spoken
    except ValueError as exc:
        raise CliError(_one_line(exc)) from exc
    retaken = _prepare_retake(sdir, short, voiced, s, args)
    clear_stale_takes(sdir / "audio" / "vo", voiced, narrator)
    from bsa.narrate import custom

    custom.clear_log(sdir / "audio" / "vo")  # this run's output only: the story's takes and the closing line's
    closing_note = None
    if narrator.get("provider") == "elevenlabs":  # one request for every line: never pay for the story twice
        result = narrate(sdir, voiced, s)
    else:  # the story first, so a closing line the voice can't say never costs the short its voice
        result = narrate(sdir, short, s)
        if result.status == "ok" and voiced is not short:
            heard_file = sdir / "audio" / "vo" / HEARD_FILE
            story_heard = heard_file.read_bytes() if heard_file.is_file() else None
            extra = narrate(sdir, voiced, s)  # only the missing take is spoken
            if extra.status != "ok":
                why = _restore_file(heard_file, story_heard)  # the voice check is of the story's takes
                jobstate.mark_done(sdir, "narration", status="ok", provider=narrator.get("provider"),
                                   **({"message": result.message} if result.message else {}))  # fmt: skip
                closing_note = (f"the closing line couldn't be narrated ({_one_line(extra.message)}), so the "
                                "closing card is silent")  # fmt: skip
                if why:
                    closing_note += f"; the voice check's record couldn't be restored ({_one_line(why)})"
    out = {"status": result.status, "durations": result.durations, "message": result.message}
    if retaken:
        out["retake"] = retaken
    log = sdir / "audio" / "vo" / custom.LOG_FILE
    if log.is_file():  # the user's own tool's output; its fallbacks and warnings would otherwise pass unseen
        out.update(voice_log=str(log), voice_log_notes=custom.log_notes(log.parent))
    if closing_note:
        out["warnings"] = [closing_note]
    try:
        out["timeline"] = cmd_timeline(args)
    except Exception as exc:  # noqa: BLE001 - narration never blocks: fall back to captions-only timing
        message = (f"The narration couldn't be timed ({_one_line(exc)}), so this short uses captions only; "
                   "you can add a voice later.")  # fmt: skip
        jobstate.mark_done(sdir, "narration", status="captions-only", provider=(s.get("narrator") or {}).get(
            "provider"), message=message)  # fmt: skip
        out.update(status="captions-only", durations=None, message=message)
        out["timeline"] = cmd_timeline(args)
    return out


def _sound_bed(sdir: Path, short, tl):
    path = sdir / "source" / "sound.py"
    if not path.is_file():
        return None
    import runpy

    ns = runpy.run_path(str(path), run_name="bsa_sound")
    build = ns.get("build_bed")
    if not callable(build):
        raise CliError(f"{path} must define build_bed(short, timeline, short_dir) returning samples at 48 kHz")
    return build(short, tl, sdir)


def cmd_mix(args) -> dict:
    from bsa.audio import recorded
    from bsa.audio.mix import mix, write_wav

    sdir = short_dir(args.short)
    short = load(sdir)
    tl, from_takes = built_timeline(sdir, short)
    from bsa.audio import events

    try:  # build.py's sound events (source/events.json) play like sfx entries of their kind
        built = events.to_sfx(events.load(events.path(sdir)), tl)
    except ValueError as exc:
        raise CliError(f"source/events.json: {exc}") from exc
    short.sfx = list(short.sfx) + built
    bed = _sound_bed(sdir, short, tl)
    takes = sdir / "audio" / "vo" if from_takes else None
    library = _sound_library(sdir)  # recorded clips by sfx kind; synthesis is the fallback
    s = settings.load_settings()
    policy = recorded.sound_policy(short, s)
    from bsa import closing

    card = closing.resolve(s, short)
    end = closing.plan(card, tl.n_frames + 1, tl.fps, closing.take_seconds(takes))
    tail = (end["n_frames"], closing.take(takes), end["line_f0"]) if end else None
    stereo, levels = mix(tl, short, takes, extra_bed=bed, library=library, policy=policy, closing=tail)
    path = sdir / "audio" / "mix.wav"
    write_wav(path, stereo)
    out = {"path": str(path), "n_frames": tl.n_frames + (end["n_frames"] if end else 0), "voice": from_takes,
           "sound_py": bed is not None, "events": len(built), "levels": levels, "sound_policy": policy,
           "sfx": recorded.report(recorded.resolve(short, library, policy))}  # fmt: skip
    if end:
        out["closing"] = {"frames": end["n_frames"], "voice_line": bool(end["line_f0"]),
                          **closing.hold(card, end, tl.fps)}  # "note" when the line holds it past closing.seconds
        if card["voice_line"] and from_takes and not end["line_f0"]:
            out["warnings"] = [("the closing line has no take yet, so the closing card is silent: run CLI narrate "
                                "SHORT again (it makes only the missing take), then mix")]  # fmt: skip
    return out


DEPRECATED_PAGE = "use CLI page SHORT"


def _one_page(sdir: Path, stage: str, open_: bool) -> dict:
    """The short's one review page at `stage`. With `open_` it is opened, or a tab already open switches to the
    stage, exactly as `page SHORT --stage STAGE` (its url as "page", plus "opened", "already_open", "mode", "health"
    and "phone"). Without, nothing is started: "page" is the link when the workspace's server already runs, else
    None with "page_hint" saying how to open it."""
    from bsa.review import launch

    if open_:
        out = _page_open(argparse.Namespace(stage=stage, open=False, lan=False), sdir)
        keep = ("opened", "already_open", "mode", "health", "phone")
        return {"page": out["url"], "stage": stage, **{k: out[k] for k in keep if k in out}}
    info = launch.server_info(sdir.parent)
    if info is None:
        return {"page": None, "stage": stage,
                "page_hint": f"The review page isn't running: CLI page SHORT --stage {stage} opens it."}  # fmt: skip
    return {"page": launch.page_url(info, sdir.name, stage), "stage": stage}


def cmd_captions(args) -> dict:
    """Write the final video's caption files (out/<slug>.srt, .vtt and -transcript.txt), cue for cue with its captions.
    Refused unless the final video was cut from the current timing."""
    from bsa import captions

    sdir = short_dir(args.short)
    try:
        return captions.write_files(sdir, load(sdir), settings.load_settings())
    except captions.Stale as exc:
        raise CliError(str(exc)) from exc


def cmd_post(args) -> dict:
    """Write out/post-pack.md: every post field and every output file in one place (written even with problems).
    `post SHORT --posted URL [--platform P]` records where it went; `post related SHORT` lists posted shorts."""
    from bsa import post, postpack

    words = args.words
    if words[0] == "related" and len(words) == 2:
        if args.posted or args.platform is not None:
            raise CliError("post related SHORT only lists posted shorts: record a post with post SHORT --posted URL")
        sdir = short_dir(words[1])
        load(sdir)
        return {"related": post.related(sdir.parent, sdir)}
    if len(words) != 1:
        raise CliError("usage: post SHORT [--posted URL [--platform youtube]] | post related SHORT")
    sdir = short_dir(words[0])
    short = load(sdir)
    if args.posted:
        try:
            return {"posted": post.add_posted(sdir, args.platform or "youtube", args.posted)}
        except (ValueError, TimeoutError) as exc:
            raise CliError(str(exc)) from exc
    if args.platform is not None:
        raise CliError("--platform goes with --posted URL")
    path, changed = postpack.write(sdir, short)
    errors, warnings = post.problems(short, settings.load_settings())
    return {"pack": str(path), "changed": changed, "fields": post.fields(short), "counts": post.counts(short),
            "errors": errors, "warnings": warnings}  # fmt: skip


def cmd_review(args) -> dict:
    """The one page at the story, storyboard or final stage (the separate review pages are retired)."""
    sdir = short_dir(args.short)
    return {**_one_page(sdir, args.page, args.open), "deprecated": DEPRECATED_PAGE}


def cmd_render_status(args) -> dict:
    """Where the preview chain, a render or a check, frame-check or edit is: its status file plus `alive` (its
    Blender process is still running), `stale` (it says running but the process is gone), `step` (the current step
    and its progress), `idle` and `last_run` (how the last run ended, when nothing runs) and a one-line `summary`.
    `page` is the one page's Render stage, which shows the same status live."""
    from bsa import render_status as rs

    sdir = short_dir(args.short)
    presets = [args.preset] if args.preset else ["preview", "final"]
    if getattr(args, "tag", None):  # a side run's status is its own (bsa.engine_runs.status_key)
        from bsa import engine_runs

        try:
            tag = engine_runs.check_tag(args.tag)
        except ValueError as exc:
            raise CliError(str(exc)) from exc
        presets = [f"{p}-{tag}" for p in presets]
    found = [(p, rs.read_status(rs.status_path(sdir, p))) for p in presets]
    found = [(p, st) for p, st in found if st]
    if found:
        preset, status = max(found, key=lambda item: item[1].get("updated") or 0)
    else:
        preset, status = presets[0] if args.preset else "preview", None
    if status is None:
        out = rs.describe({"state": "none", "preset": preset, "short": {"dir": str(sdir)}})
        out["alive"] = False
    else:
        out = rs.describe(status)
    page = _one_page(sdir, "render", args.open)
    out.update({k: v for k, v in page.items() if k != "stage"})
    out["status_file"] = str(rs.status_path(sdir, preset))
    return out


def cmd_feedback(args) -> dict:
    """feedback add SHORT --severity S --summary TEXT [...] | list SHORT | report SHORT [--open] | decline SHORT.
    Problems in the plugin itself, noted locally; nothing is ever sent (bsa.feedback)."""
    from bsa import feedback

    sdir = short_dir(args.short)
    s = settings.load_settings()
    setting = s.get("feedback") or "ask"
    paths = {"workspace": s.get("workspace")}
    if args.action == "add":
        if setting == "off":
            return {"noted": False, "setting": setting}
        if not args.severity or not args.summary:
            raise CliError("usage: feedback add SHORT --severity blocking|wrong-output|rough-edge --summary TEXT")
        stage = args.stage or jobstate.next_stage(sdir)
        try:
            entry = feedback.add(sdir, args.severity, args.summary, happened=args.happened, expected=args.expected,
                                 command=args.cmd, error=args.error, workaround=args.workaround, stage=stage,
                                 blender=args.blender, mode=args.mode, short_paths=paths)  # fmt: skip
        except ValueError as exc:
            raise CliError(str(exc)) from exc
        return {"noted": True, "entry": entry, "file": str(feedback.feedback_path(sdir))}
    if args.action == "decline":
        return {"declined": feedback.decline(sdir)}
    pending = feedback.pending(sdir)
    out = {"setting": setting, "pending": len(pending), "by_severity": feedback.counts(pending),
           "ask": feedback.should_ask(sdir, setting), "file": str(feedback.feedback_path(sdir))}  # fmt: skip
    if args.action == "list":
        return {**out, "entries": feedback.entries(sdir)}
    feedback.allow_panel(sdir)  # the user said yes: the Post stage shows the notes, and they are not asked again
    rep = feedback.report(sdir, workspace=s.get("workspace"), scrub=feedback.settings_scrub(s) + _short_text(sdir),
                          mark=False)  # marked reported only when the user copies it or opens it on GitHub
    page = _one_page(sdir, "post", args.open)
    return {**page, "title": rep["title"], "url": rep["url"], "shortened": rep["shortened"],
            "entries": rep["entries"], "by_severity": rep["by_severity"]}  # fmt: skip


def _short_text(sdir: Path) -> list[str]:
    """The short's own title and story lines, kept out of a feedback report."""
    try:
        data = json.loads(short_json(sdir).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if not isinstance(data, dict):
        return []
    lines = [ln.get("text") for ln in data.get("lines") or [] if isinstance(ln, dict)]
    return [str(t) for t in [data.get("title"), *lines] if isinstance(t, str) and t.strip()]


def _registry() -> dict:
    from bsa.narrate import install

    return json.loads(install.REGISTRY.read_text(encoding="utf-8"))


def _voice_test_custom() -> dict:
    """One short test line through the saved voice command, into a temp folder (never touches a short)."""
    import tempfile

    from bsa.narrate import custom
    from bsa.narrate.contract import NarrationError
    from bsa.short import Line

    n = settings.load_settings().get("narrator") or {}
    command = str(n.get("command") or "").strip()
    if not command:
        raise CliError("No voice command is saved yet: run settings set narrator.command \"...\" first.")
    out = Path(tempfile.mkdtemp(prefix="bsa-voice-test-"))
    line = Line(id="test", text="This is a quick test of my voice tool. How does it sound?", shot="s1")
    try:
        seconds = custom.generate(command, [line], out, voice=n.get("voice"), speed=float(n.get("speed") or 1.0))["test"]
    except NarrationError as exc:
        raise CliError(f"The voice test didn't work: {_one_line(exc)}") from exc
    return {"path": str(out / "test.wav"), "seconds": seconds, "command": command}


def cmd_voice(args) -> dict:
    from bsa.narrate import install

    if args.action == "suggest":
        from bsa import machine

        return {**install.suggest(machine.detect()), "installed": install.installed(), "uv": bool(install.uv_path())}
    if args.action == "test-custom":
        return _voice_test_custom()
    if args.action == "plan":
        if not args.args:
            raise CliError("usage: voice plan MODEL...")
        items = install.download_plan(args.args)
        rows = ["| Download | From | Licence | Size |", "|---|---|---|---|"]
        rows += [f"| {i['model']} | {i['repo']} | {i['licence']} | {i['bytes'] / 1e6:.0f} MB |" for i in items]
        total = sum(i["bytes"] for i in items)
        return {"items": items, "total_bytes": total, "table": "\n".join(rows) + f"\n\n**Total: {total / 1e9:.2f} GB**"}
    if args.action == "install":
        if not args.args:
            raise CliError("usage: voice install MODEL... (show voice plan MODEL... to the user first)")
        runner = install.install(args.args)
        return {"runner": str(runner), "installed": install.installed()}
    if args.action == "sample":
        if len(args.args) != 2:
            raise CliError("usage: voice sample MODEL VOICE")
        return {"wav": str(install.sample(*args.args))}
    # list
    if not args.args:
        reg, have = _registry(), install.installed()
        models = [{"model": "elevenlabs", "label": "ElevenLabs (paid, needs an API key)", "installed": None}]
        models += [{"model": k, "label": m["label"], "licence": m["licence"], "installed": have.get(k, False)}
                   for k, m in reg["models"].items() if m["kind"] == "tts"]  # fmt: skip
        return {"models": models}
    model = args.args[0]
    if model == "elevenlabs":
        from bsa.narrate import elevenlabs

        voices = [{"voice": v.get("voice_id"), "name": v.get("name"), "labels": v.get("labels", {})}
                  for v in elevenlabs.list_voices()]  # fmt: skip
        return {"model": model, "voices": voices}
    reg = _registry()["models"]
    if model not in reg:
        raise CliError(f"unknown voice model {model!r}; have: elevenlabs, {', '.join(reg)}")
    m = reg[model]
    voices = [{"voice": k, **v} for k, v in m.get("voices", {}).items()]
    return {"model": model, "default_voice": m.get("default_voice"), "voices": voices}


def cmd_fonts(args) -> dict:
    from bsa import fonts

    out = {"suggestions": fonts.suggestions()}
    sdir = short_dir(args.short) if args.short else None
    if args.name:  # a family, "Family@600" (a static weight, installed or downloaded) or a file
        out = {**fonts.report(args.name, args.text, cache_dirs=fonts.font_caches(sdir)), **out}
    if sdir is not None:
        out["glyph_warnings"] = fonts.glyph_warnings(load(sdir), settings.load_settings(),
                                                     cache_dirs=fonts.font_caches(sdir))  # fmt: skip
    return out


def _plan_path(sdir: Path) -> Path:
    return sdir / "source" / "assets-plan.json"


def cmd_assets(args) -> dict:
    from bsa.assets import gfonts, polyhaven, sounds
    from bsa.short import Asset, save_short

    if args.action == "search":
        if len(args.args) < 2:
            raise CliError("usage: assets search textures|hdris|models|sounds QUERY")
        if args.args[0] == "sounds":
            ws = settings.load_settings().get("workspace")  # a cached pack's kinds come from the library
            return sounds.search(" ".join(args.args[1:]), http=sounds._urlopen, freesound_key=settings.freesound_key(),
                                 cache_dir=Path(ws) / ".assets-cache" if ws else None)
        from bsa import rules

        found, hidden = polyhaven.search_with_rules(args.args[0], " ".join(args.args[1:]),
                                                    rules.global_rules(settings.load_settings()))  # fmt: skip
        return {"results": found, **({"hidden_by_rules": hidden} if hidden else {})}
    if not args.args:
        raise CliError(f"usage: assets {args.action} SHORT ...")
    sdir = short_dir(args.args[0])
    if args.action == "plan":
        from bsa import rules

        hard_rules = rules.effective(settings.load_settings(), load(sdir))
        plans, skips = [], []
        for item in args.args[1:]:
            if sounds.is_sound_id(item):  # pack:ID, commons:FILE[=kinds], freesound:ID[=kinds]
                try:
                    plans += sounds.plan([item], http=sounds._urlopen, head=sounds.content_length,
                                         freesound_key=settings.freesound_key(),
                                         cache_dir=polyhaven.cache_dir(sdir))  # fmt: skip
                except OSError as exc:  # the source is unreachable: named in the result, never silently left out
                    skips.append(sounds.skipped(sounds.source_name(item), exc, "assets plan", item))
            elif gfonts.is_font_id(item):  # font:Family@600
                plans += gfonts.plan([item], http=sounds._urlopen, head=sounds.content_length)
            else:  # a Poly Haven ID[=Display name]
                asset_id, _, name = item.partition("=")
                try:  # a model of a person (or animal, face, logo) is refused under the matching hard rule
                    polyhaven.check_rules(asset_id, hard_rules)
                except ValueError as exc:
                    raise CliError(str(exc)) from exc
                plans += polyhaven.plan([asset_id], resolution=args.resolution, names={asset_id: name} if name else {})
        if not plans and skips:
            raise CliError("nothing planned: " + "; ".join(f"{s['id']} was skipped ({s['source']}: {s['error']}), "
                                                           f"{s['retry']}" for s in skips))  # fmt: skip
        if not plans:
            raise CliError("usage: assets plan SHORT ID[=Display name]... (Poly Haven), pack:ID, commons:FILE[=kind], "
                           "freesound:ID[=kind] (sounds) or font:Family@WEIGHT")  # fmt: skip
        _plan_path(sdir).write_text(json.dumps([asdict(a) for a in plans], indent=2), encoding="utf-8")
        table = polyhaven.format_plan(plans)
        if skips:
            table += "\n\n" + "\n".join(f"- {s['id']} is not in this plan: {s['source']} was skipped ({s['error']}); "
                                         f"{s['retry']}." for s in skips)  # fmt: skip
        out = {"assets": [asdict(a) for a in plans], "total_bytes": sum(polyhaven.download_bytes(a) for a in plans),
               "table": table}  # fmt: skip
        return {**out, "skipped": skips} if skips else out
    # download
    path = _plan_path(sdir)
    if not path.is_file():
        raise CliError("Nothing to download: run assets plan SHORT ID... and get the user's yes first.")
    plans = [Asset(**a) for a in json.loads(path.read_text(encoding="utf-8"))]
    cache = polyhaven.cache_dir(sdir)
    files = {}
    for kind, fetch in (("sounds", sounds.download), ("fonts", gfonts.download)):
        if any(a.kind == kind for a in plans):
            files.update(fetch([a for a in plans if a.kind == kind], cache, http=sounds._urlopen))
    files.update(polyhaven.download([a for a in plans if a.kind not in ("sounds", "fonts")], cache))
    short = load(sdir)
    polyhaven.record(short, plans)
    save_short(short, short_json(sdir))
    path.unlink()
    return {"files": {k: [str(p) for p in v] for k, v in files.items()}}


# --------------------------------------------------------------------------- parser


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="bsa_cli.py", description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("where", help="plugin root, Blender, Blender's Python, settings path").set_defaults(fn=cmd_where)

    sp = sub.add_parser("settings", help='show | set KEY VALUE | missing | check | summary | rules | rule add "TEXT" '
                        "[--kind K] [--part-of ID] | rule remove ID | rule none")  # fmt: skip
    sp.add_argument("action", choices=["show", "set", "missing", "summary", "rules", "rule", "post-format", "check"])
    sp.add_argument("key", nargs="?")
    sp.add_argument("value", nargs="?")
    sp.add_argument("--kind", default=None, help="a rule kind the plugin checks itself (settings rules lists them)")
    sp.add_argument("--part-of", default=None, help="the broad rule this concrete rule spells out")
    sp.set_defaults(fn=cmd_settings)

    sp = sub.add_parser("machine", help="CPU, RAM, GPUs and free video memory")
    sp.add_argument("--need-gb", type=float, default=None, help="warn if no GPU has this much video memory free")
    sp.set_defaults(fn=cmd_machine)

    sp = sub.add_parser("new-short", help="make a new short's folder in the workspace")
    sp.add_argument("title")
    sp.add_argument("--number", type=int, default=None, help="the folder number to use (refused if already used)")
    sp.add_argument("--topic", default=None, help="what a viewer should understand at the end (short.json topic)")
    sp.add_argument("--length", type=float, default=None, help="length in seconds, 15 to 90 (short.json length_s)")
    sp.set_defaults(fn=cmd_new_short)

    sp = sub.add_parser("state", help="SHORT [done STAGE [--info JSON] | reset STAGE | restore]")
    sp.add_argument("short")
    sp.add_argument("action", nargs="?", choices=["done", "reset", "restore"])
    sp.add_argument("stage", nargs="?", choices=jobstate.STAGES)
    sp.add_argument("--info", default=None)
    sp.set_defaults(fn=cmd_state)

    sp = sub.add_parser("page", help="decide SHORT STAGE approve|changes --by page|chat|settings [--choice ENGINE] "
                        "[--note TEXT] [--version N] | decide SHORT thumbnail --choice N [--by page|chat] "
                        "| note SHORT ID done|declined --reply TEXT | notes SHORT [--stage STAGE] "
                        "| wait SHORT --stage STAGE [--timeout S] | SHORT [--open] [--stage STAGE] | serve")  # fmt: skip
    sp.add_argument("words", nargs="+")
    sp.add_argument("--open", action="store_true")
    sp.add_argument("--stage", default=None)
    sp.add_argument("--by", choices=["page", "chat", "settings"], default=None)
    sp.add_argument("--choice", default=None)
    sp.add_argument("--note", default=None)
    sp.add_argument("--reply", default=None)
    sp.add_argument("--version", type=int, default=None)
    sp.add_argument("--timeout", type=float, default=None, help="seconds page wait waits (default 600)")
    sp.add_argument("--poll", type=float, default=1.0)
    sp.add_argument("--workspace", default=None)
    sp.add_argument("--lan", action="store_true")
    sp.set_defaults(fn=cmd_page)

    sp = sub.add_parser("doctor", help="[SHORT]: check Blender, the GPU, the voice, the disk, the caption font and "
                        "the page server (saved to the short's review/doctor.json)")  # fmt: skip
    sp.add_argument("short", nargs="?", default=None)
    sp.set_defaults(fn=cmd_doctor)

    sp = sub.add_parser("engine", help="SHORT: its render engine and where it comes from, and each engine's measured "
                        "seconds per frame and MP4")  # fmt: skip
    sp.add_argument("short")
    sp.set_defaults(fn=cmd_engine)

    for name, fn, text in [
        ("validate", cmd_validate, "check short.json (sources, ids, sizes)"),
        ("timeline", cmd_timeline, "write source/timeline.json (from the takes if narrated)"),
        ("mix", cmd_mix, "write audio/mix.wav (voice + effects + source/sound.py)"),
    ]:
        sp = sub.add_parser(name, help=text)
        sp.add_argument("short")
        sp.set_defaults(fn=fn)

    sp = sub.add_parser("captions", help="SHORT: write the final video's SRT, VTT and transcript beside it")
    sp.add_argument("short")
    sp.set_defaults(fn=cmd_captions)

    sp = sub.add_parser("post", help="SHORT: write out/post-pack.md with every post field and file; lists problems")
    sp.add_argument("words", nargs="+")
    sp.add_argument("--posted", default=None, metavar="URL", help="record where the short was posted (stored only)")
    sp.add_argument("--platform", default=None, choices=["youtube", "instagram", "tiktok", "other"],
                    help="with --posted: where (default youtube)")  # fmt: skip
    sp.set_defaults(fn=cmd_post)

    sp = sub.add_parser("narrate", help="SHORT [--line ID [--say TEXT]]: generate the narration (never blocks) and "
                        "re-time the short; --line re-takes that one line with the next seed")  # fmt: skip
    sp.add_argument("short")
    sp.add_argument("--line", default=None, help="re-take this line only (a different reading)")
    sp.add_argument("--say", default=None, help="with --line: respell the line's spoken words first")
    sp.set_defaults(fn=cmd_narrate)

    sp = sub.add_parser("voice", help="suggest | plan MODEL... | install MODEL... | sample MODEL VOICE | list [MODEL] | test-custom")
    sp.add_argument("action", choices=["suggest", "plan", "install", "sample", "list", "test-custom"])
    sp.add_argument("args", nargs="*")
    sp.set_defaults(fn=cmd_voice)

    sp = sub.add_parser("review", help="story|storyboard|final SHORT [--open]: the one page's link at that stage "
                        "(deprecated: use page SHORT --stage STAGE)")  # fmt: skip
    sp.add_argument("page", choices=["story", "storyboard", "final"])
    sp.add_argument("short")
    sp.add_argument("--open", action="store_true", help="open the page at that stage (page SHORT --stage STAGE)")
    sp.set_defaults(fn=cmd_review)

    sp = sub.add_parser("render-status", help="SHORT [--preset preview|final] [--open]: where the preview chain or a "
                        "render is (the page's Render stage shows it live)")  # fmt: skip
    sp.add_argument("short")
    sp.add_argument("--preset", choices=["preview", "final"], help="default: the most recently updated")
    sp.add_argument("--tag", default=None, help="a side run's status (its tag: the engine's name for --engine alone)")
    sp.add_argument("--open", action="store_true", help="open the one page at its Render stage")
    sp.set_defaults(fn=cmd_render_status)

    sp = sub.add_parser("fonts", help="[NAME]: is NAME installed, plus a few installed fonts to suggest")
    sp.add_argument("name", nargs="?")
    sp.add_argument("--text", default=None, help="list the characters of TEXT the font has no glyph for")
    sp.add_argument("--short", default=None, help="check SHORT's captions, labels, badges and end card")
    sp.set_defaults(fn=cmd_fonts)

    sp = sub.add_parser("assets", help="search textures|hdris|models|sounds QUERY | plan SHORT ID... | download SHORT")
    sp.add_argument("action", choices=["search", "plan", "download"])
    sp.add_argument("args", nargs="*")
    sp.add_argument("--resolution", default="2k")
    sp.set_defaults(fn=cmd_assets)

    sp = sub.add_parser("feedback", help="add SHORT --severity S --summary TEXT | list SHORT | report SHORT [--open] | "
                        "decline SHORT: problems in the plugin itself, noted locally, never sent")  # fmt: skip
    sp.add_argument("action", choices=["add", "list", "report", "decline"])
    sp.add_argument("short")
    sp.add_argument("--severity", choices=["blocking", "wrong-output", "rough-edge"])
    sp.add_argument("--summary")
    sp.add_argument("--happened")
    sp.add_argument("--expected")
    sp.add_argument("--command", dest="cmd")
    sp.add_argument("--error")
    sp.add_argument("--workaround")
    sp.add_argument("--stage")
    sp.add_argument("--blender", help="Blender version, if known")
    sp.add_argument("--mode", choices=["script", "mcp"], help="script or MCP mode, if known")
    sp.add_argument("--open", action="store_true", help="open the one page at its Post stage (report)")
    sp.set_defaults(fn=cmd_feedback)
    return p


def _one_line(text) -> str:
    return messages.one_line(text, ERROR_LIMIT)


def main(argv: list[str] | None = None) -> int:
    # JSON on stdout is pure ASCII (non-ASCII is escaped); stderr must never fail on a console code page.
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(errors="backslashreplace")
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:  # argparse already printed its usage message
        return int(exc.code or 0)
    try:
        out = args.fn(args)
    except (CliError, jobstate.StateDamaged, store.StoreDamaged) as exc:
        if getattr(exc, "payload", None) is not None:
            print(json.dumps(exc.payload, indent=2, default=str))
        print(_one_line(exc), file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001 - every failure becomes one plain line for Claude to relay
        print(_one_line(f"{type(exc).__name__}: {exc}"), file=sys.stderr)
        return 1
    _add_page_notes(args, out)
    print(json.dumps(out, indent=2, default=str))
    return 0


def _add_page_notes(args, out) -> None:
    """A result for a short says what is waiting on its review page (notes, answers Claude hasn't acted on)."""
    from bsa.review import inbox

    try:
        sdir = short_dir(args.short) if getattr(args, "short", None) else None
    except Exception:  # noqa: BLE001 - a short that doesn't resolve has no page
        sdir = None
    inbox.attach(out, sdir)


if __name__ == "__main__":
    sys.exit(main())
