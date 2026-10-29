"""blender-shorts-agent command line (everything that does not need Blender itself).

Run it with Blender's bundled Python (`where` prints its path); `where` and `settings` also work with any Python 3.11+.
Every command prints one JSON object on stdout. On failure it exits non-zero with one plain line on stderr.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parent
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))

from bsa import jobstate, messages, settings, workspace  # needs the plugin root on sys.path (above)

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
    from bsa.narrate.run import takes_durations
    from bsa.timeline import build_timeline

    durations = takes_durations(sdir, short)
    end_card = settings.has_end_card(settings.load_settings(), short)
    return build_timeline(short, durations, end_card=end_card), durations is not None


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
                "narrator.label", "render.engine",
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
        return text
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
    return {"settings": settings.redacted(s), "summary": settings.summary_line(s)}


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


def _rule_rows(short) -> list[dict]:
    from bsa import rules

    return rules.panel_rows(rules.effective(settings.load_settings(), short), short)


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
        if args.info:
            try:
                info = json.loads(args.info)
            except ValueError as exc:
                raise CliError(f"--info is not valid JSON: {exc}") from exc
            if not isinstance(info, dict):
                raise CliError("--info must be a JSON object")
        if args.stage in ("interview", "story"):
            _gate(sdir, args.stage)
        if args.stage == "build":  # a rebuild invalidates the checks and every render made from the old build
            jobstate.reset_from(sdir, "checks")
        jobstate.mark_done(sdir, args.stage, **info)
    elif args.action == "reset":
        jobstate.reset_from(sdir, args.stage)
    elif args.action is not None:
        raise CliError("usage: state SHORT [done STAGE [--info JSON] | reset STAGE]")
    return _state(sdir)


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
    return fonts.glyph_warnings(short, s) + recorded.policy_warnings(
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
    from bsa.short import spoken_warnings, validate
    from bsa.timeline import length_warning

    sdir = short_dir(args.short)
    short = load(sdir)
    rule_errors, rule_warnings = _rule_problems(short)
    errors = validate(short) + rule_errors
    warnings = spoken_warnings(short) if not errors else []
    warnings += rule_warnings
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


def cmd_narrate(args) -> dict:
    from bsa.narrate.run import clear_stale_takes, narrate

    sdir = short_dir(args.short)
    if "storyboard" not in jobstate.load_state(sdir)["stages"]:  # binding: narration (paid credit) only after a yes
        raise CliError("Narration is made only after the storyboard is approved: show the storyboard page and get a "
                       "yes first (then CLI state SHORT done storyboard).")  # fmt: skip
    short = load(sdir)
    s = settings.load_settings()
    from bsa import closing

    voiced = closing.with_voice_line(short, closing.resolve(s, short))  # the closing line, in the same voice
    narrator = s.get("narrator") or {}
    clear_stale_takes(sdir / "audio" / "vo", voiced, narrator)
    closing_note = None
    if narrator.get("provider") == "elevenlabs":  # one request for every line: never pay for the story twice
        result = narrate(sdir, voiced, s)
    else:  # the story first, so a closing line the voice can't say never costs the short its voice
        result = narrate(sdir, short, s)
        if result.status == "ok" and voiced is not short:
            extra = narrate(sdir, voiced, s)  # only the missing take is spoken
            if extra.status != "ok":
                jobstate.mark_done(sdir, "narration", status="ok", provider=narrator.get("provider"),
                                   **({"message": result.message} if result.message else {}))  # fmt: skip
                closing_note = (f"the closing line couldn't be narrated ({_one_line(extra.message)}), so the "
                                "closing card is silent")  # fmt: skip
    out = {"status": result.status, "durations": result.durations, "message": result.message}
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


def _pending_assets(sdir: Path) -> list:
    """Assets planned with `assets plan` and not downloaded yet."""
    from bsa.short import Asset

    path = _plan_path(sdir)
    if not path.is_file():
        return []
    return [Asset(**a) for a in json.loads(path.read_text(encoding="utf-8"))]


def cmd_review(args) -> dict:
    from bsa.review import pages

    sdir = short_dir(args.short)
    short = load(sdir)
    out = sdir / "review" / f"{args.page}.html"
    if args.page == "story":
        styles_path = sdir / "source" / "styles.json"
        options = None
        look = settings.look_mode(settings.load_settings(), short)
        if styles_path.is_file() and look != "brand":  # the brand has no look options
            styles = json.loads(styles_path.read_text(encoding="utf-8"))
            options = [{**o, "thumb": f"stills/style-{i}.jpg"} for i, o in enumerate(styles, 1)]
        try:
            tl, _ = current_timeline(sdir, short)
        except Exception:  # noqa: BLE001 - broken takes: pace the page at reading speed
            tl = _reading_timeline(short)
        rows, _ = _pacing(short, tl)
        page = pages.render_story(short, out, options, _pending_assets(sdir), pacing=rows, rules=_rule_rows(short),
                                  look=look)
    elif args.page == "storyboard":
        tl, _ = current_timeline(sdir, short)
        page = pages.render_storyboard(short, tl, sdir / "stills", out, rules=_rule_rows(short),
                                       closing=_closing_info(sdir, short))
    else:
        final = sdir / "out" / f"{short.slug}.mp4"
        mp4 = final if final.exists() else sdir / "out" / f"{short.slug}-preview.mp4"
        findings, redo_all = pages.load_findings(sdir)  # rows as check and frame-check write them; unknown keys ignored
        page = pages.render_final(short, findings, mp4, out, (sdir / "review" / "checks.json").is_file(), redo_all,
                                  rules=_rule_rows(short), closing=_closing_info(sdir, short))  # fmt: skip
    if args.open:
        pages.open_page(page)
    return {"page": str(page)}


def _closing_info(sdir: Path, short) -> dict | None:
    """The closing card for the review pages while the short has one (BLENDER stills, closing or edit make it)."""
    from bsa import closing
    from bsa.review import pages

    if not closing.active(settings.load_settings(), short):
        return None
    return pages.closing_info(sdir / "stills")


def cmd_render_status(args) -> dict:
    """Where the preview chain, a render or a check, frame-check or edit is: its status file plus `alive` (its
    Blender process is still running), `stale` (it says running but the process is gone), `step` (the current step
    and its progress), `idle` and `last_run` (how the last run ended, when nothing runs) and a one-line `summary`.
    Rewrites the page when the run can't (it is not running) so the page shows the true state too."""
    from bsa import render_status as rs
    from bsa.review import pages

    sdir = short_dir(args.short)
    presets = [args.preset] if args.preset else ["preview", "final"]
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
    page = rs.page_path(sdir, preset)
    if not out["alive"] or not page.is_file():
        pages.render_status_page(out, page)
    out["page"] = str(page)
    out["status_file"] = str(rs.status_path(sdir, preset))
    if args.open:
        pages.open_page(page)
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
    rep = feedback.report(sdir, workspace=s.get("workspace"), scrub=feedback.settings_scrub(s) + _short_text(sdir),
                          mark=True)  # fmt: skip
    page = feedback.render_page(rep, sdir / "review" / "plugin-feedback.html")
    if args.open:
        from bsa.review import pages

        pages.open_page(page)
    return {"page": str(page), "title": rep["title"], "url": rep["url"], "shortened": rep["shortened"],
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
    if args.name:
        path = Path(args.name) if Path(args.name).is_file() else fonts.find_font_file(args.name, fonts.font_dirs())
        out = {"name": args.name, "installed": path is not None, "file": str(path) if path else None, **out}
        out.update(fonts.describe(path, args.name, args.text) if path else {})
    if args.short:
        sdir = short_dir(args.short)
        out["glyph_warnings"] = fonts.glyph_warnings(load(sdir), settings.load_settings())
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

    sp = sub.add_parser("settings", help='show | set KEY VALUE | missing | summary | rules | rule add "TEXT" '
                        "[--kind K] [--part-of ID] | rule remove ID | rule none")  # fmt: skip
    sp.add_argument("action", choices=["show", "set", "missing", "summary", "rules", "rule"])
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

    sp = sub.add_parser("state", help="SHORT [done STAGE [--info JSON] | reset STAGE]")
    sp.add_argument("short")
    sp.add_argument("action", nargs="?", choices=["done", "reset"])
    sp.add_argument("stage", nargs="?", choices=jobstate.STAGES)
    sp.add_argument("--info", default=None)
    sp.set_defaults(fn=cmd_state)

    for name, fn, text in [
        ("validate", cmd_validate, "check short.json (sources, ids, sizes)"),
        ("timeline", cmd_timeline, "write source/timeline.json (from the takes if narrated)"),
        ("narrate", cmd_narrate, "generate the narration (never blocks) and re-time the short"),
        ("mix", cmd_mix, "write audio/mix.wav (voice + effects + source/sound.py)"),
    ]:
        sp = sub.add_parser(name, help=text)
        sp.add_argument("short")
        sp.set_defaults(fn=fn)

    sp = sub.add_parser("voice", help="suggest | plan MODEL... | install MODEL... | sample MODEL VOICE | list [MODEL] | test-custom")
    sp.add_argument("action", choices=["suggest", "plan", "install", "sample", "list", "test-custom"])
    sp.add_argument("args", nargs="*")
    sp.set_defaults(fn=cmd_voice)

    sp = sub.add_parser("review", help="write (and --open) a review page")
    sp.add_argument("page", choices=["story", "storyboard", "final"])
    sp.add_argument("short")
    sp.add_argument("--open", action="store_true")
    sp.set_defaults(fn=cmd_review)

    sp = sub.add_parser("render-status", help="SHORT [--preset preview|final] [--open]: where the preview chain or a render is")
    sp.add_argument("short")
    sp.add_argument("--preset", choices=["preview", "final"], help="default: the most recently updated")
    sp.add_argument("--open", action="store_true", help="open the live page review/render-<preset>.html")
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
    sp.add_argument("--open", action="store_true", help="open review/plugin-feedback.html (report)")
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
    except CliError as exc:
        if exc.payload is not None:
            print(json.dumps(exc.payload, indent=2, default=str))
        print(_one_line(exc), file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001 - every failure becomes one plain line for Claude to relay
        print(_one_line(f"{type(exc).__name__}: {exc}"), file=sys.stderr)
        return 1
    print(json.dumps(out, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
