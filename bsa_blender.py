"""blender-shorts-agent commands that run inside Blender.

    blender -b [BLEND] --factory-startup --python-exit-code 1 --python bsa_blender.py -- CMD ...

Blender prints its own log lines, so the result is one line on stdout starting with `BSA_RESULT ` followed by JSON.
On failure the exit code is non-zero and one plain line on stderr says why. When no .blend was given (the usual way),
the commands that need the scenes open the short's `blend/short.blend` themselves, after the ones that keep a live
status have marked their run as starting there.
"""

from __future__ import annotations

import argparse
import json
import sys
from contextlib import contextmanager
from dataclasses import asdict, replace
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parent
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))

from bsa import freshness, jobstate, messages, rules, settings, workspace  # needs the plugin root on sys.path (above)

RESULT_PREFIX = "BSA_RESULT "
ERROR_LIMIT = 600  # the one plain error line is trimmed at a sentence or clause boundary past this


class CliError(Exception):
    def __init__(self, message: str, payload: dict | None = None):
        super().__init__(message)
        self.payload = payload


# --------------------------------------------------------------------------- helpers


def _short_dir(key: str) -> Path:
    return workspace.resolve_short(key, settings.load_settings().get("workspace"))


def _load(sdir: Path):
    from bsa.short import load_short

    path = sdir / "source" / "short.json"
    if not path.is_file():
        raise CliError(f"{path} does not exist yet")
    return load_short(path)


def _timeline(sdir: Path, short):
    from bsa.narrate.run import takes_durations
    from bsa.timeline import build_timeline

    tl = build_timeline(short, takes_durations(sdir, short), settings.has_end_card(settings.load_settings(), short))
    return tl, freshness.check_timeline(sdir, tl)  # (timeline, hash): it must match source/timeline.json


def _engine(sdir: Path) -> str | None:
    """The engine approved at the engine stage, else the saved preference, else None (the scene's own)."""
    info = jobstate.load_state(sdir)["stages"].get("engine") or {}
    engine = info.get("engine") or (settings.load_settings().get("render") or {}).get("engine")
    return engine if engine in ("cycles", "eevee") else None


def _blend(sdir: Path) -> Path:
    return sdir / "blend" / "short.blend"


def _mtime(path: Path | None) -> int | None:
    return path.stat().st_mtime_ns if path is not None and path.is_file() else None


def _open_blend(sdir: Path):
    """The open .blend's data; opens blend/short.blend when Blender was started without one."""
    import bpy

    if not bpy.data.filepath:
        blend = _blend(sdir)
        if not blend.is_file():
            raise CliError(f"{blend} does not exist: run the short's source/build.py first")
        bpy.ops.wm.open_mainfile(filepath=str(blend))
    return bpy.data


def _scene(data, short, shot_id: str, tl_hash: str):
    """The shot and its scene, which must have been built for the current timeline (build.py stamps it)."""
    shot =next((s for s in short.shots if s.id == shot_id), None)
    if shot is None:
        raise CliError(f"no shot {shot_id!r} in short.json")
    scene = data.scenes.get(shot.scene)
    if scene is None:
        have = ", ".join(s.name for s in data.scenes)
        raise CliError(f"shot {shot.id} uses scene {shot.scene!r}, which the .blend does not have (it has: {have})")
    freshness.check_scene_stamp(scene.name, scene.get(freshness.TIMELINE_PROP), tl_hash)
    return shot, scene


def _shots(short, only: str | None):
    if only and not any(s.id == only for s in short.shots):
        raise CliError(f"no shot {only!r} in short.json")
    return [s for s in short.shots if only is None or s.id == only]


def _still_frame(shot, span) -> int:
    return span.f0 + round(shot.still_at * (span.f1 - span.f0))


def _by_scene(short, tl) -> dict[str, list]:
    spans = {s.id: s for s in tl.shots}
    groups: dict[str, list] = {}
    for shot in short.shots:
        groups.setdefault(shot.scene, []).append((shot, spans[shot.id]))
    return groups


def _mp4(sdir: Path, short, preset: str) -> Path:
    return sdir / "out" / (f"{short.slug}.mp4" if preset == "final" else f"{short.slug}-{preset}.mp4")


def _write_findings(path: Path, findings, stamp: dict | None = None, **extra) -> dict:
    """Findings (Finding objects or dicts) into a review JSON with their counts; `extra` keys go in too."""
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = [f if isinstance(f, dict) else asdict(f) for f in findings]
    data = {
        **({"stamp": stamp} if stamp is not None else {}),
        "errors": sum(f.get("severity") == "error" for f in rows),
        "warnings": sum(f.get("severity") == "warning" for f in rows),
        **extra,
        "findings": rows,
    }
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return {"path": str(path), **data}


def _fingerprints(sdir: Path, short, tl, tl_hash: str, data=None, reset_after=False) -> dict | None:
    """{shot id: content fingerprint} of blend/short.blend (bsa.blender.fingerprint), from the cache in
    blend/short.fingerprints.json while it is of this very file, timing and the files it reads; else computed in
    this Blender and cached. None when there is no .blend yet. `reset_after` empties the session again when this had
    to open the .blend (the edit builds its own scene)."""
    if not _blend(sdir).is_file():
        return None
    ids = [s.id for s in short.shots]
    cached = freshness.load_fingerprints(sdir, tl_hash, _fingerprint_code())
    if cached is not None and all(i in cached for i in ids):
        return {i: cached[i] for i in ids}
    import bpy

    from bsa.blender.fingerprint import fingerprints

    opened = not bpy.data.filepath
    data = data if data is not None else _open_blend(sdir)
    groups = []
    for items in _by_scene(short, tl).values():
        _, scene = _scene(data, short, items[0][0].id, tl_hash)
        groups.append((scene, [span for _, span in items]))
    parts: dict = {}
    shots, files = fingerprints(groups, parts)
    freshness.save_fingerprints(sdir, tl_hash, shots, files, code=_fingerprint_code(), parts=parts)
    if reset_after and opened:
        bpy.ops.wm.read_factory_settings(use_empty=True)
    return {i: shots[i] for i in ids}


def _fingerprint_code() -> str:
    """freshness.fingerprint_code for this Blender (None outside it)."""
    try:
        import bpy
    except ImportError:
        return freshness.fingerprint_code(None)
    return freshness.fingerprint_code(bpy.app.version_string)


def _parts(sdir: Path, fps: dict | None) -> dict:
    """{shot id: the parts of its fingerprint} (what changed, for the messages), from the fingerprint cache."""
    return freshness.load_fingerprint_parts(sdir, fps)


def _check_keys(short, tl, fps: dict | None, parts: dict | None = None) -> dict:
    """{shot id: what its scene checks depend on} (freshness.shot_check_inputs)."""
    spans = {s.id: s for s in tl.shots}
    ids = [s.id for s in short.shots]
    rule_key = rules.scene_key(_hard_rules(short), short)
    return {s.id: freshness.shot_check_inputs((fps or {}).get(s.id), spans[s.id], asdict(s), ids,
                                              (parts or {}).get(s.id), rule_key) for s in short.shots}  # fmt: skip


def _hard_rules(short) -> list[dict]:
    """The user's hard rules and the short's own (bsa.rules.effective)."""
    return rules.effective(settings.load_settings(), short)


def _closing_card(sdir: Path, short) -> tuple[dict | None, Path | None, list[str]]:
    """(the short's closing card settings, its PNG in stills/, warnings), made fresh each time (one sequencer frame):
    the user's image scaled to fill 1080x1920, or the card made from the brand. (None, None, warnings) when the short
    has no closing card. The warnings (and what made the card) are kept in stills/closing.json for the review pages."""
    from bsa import closing

    s = settings.load_settings()
    cfg = closing.resolve(s, short)
    png, info = sdir / "stills" / closing.CARD_FILE, sdir / "stills" / closing.INFO_FILE
    warnings = []
    if cfg is not None and cfg["image"] and not Path(cfg["image"]).is_file():
        made = "a card made from your brand and website" if cfg["website"] else "no closing card"
        warnings.append(f"the closing image {cfg['image']} is missing, so this short ends on {made}: set "
                        "closing.image again")  # fmt: skip
        cfg = {**cfg, "image": None}
        if not cfg["website"]:
            cfg = None
    if cfg is None:
        png.unlink(missing_ok=True)
        info.unlink(missing_ok=True)
        return None, None, warnings
    from bsa.blender import closing_card
    from bsa.blender.pixels import read_png

    if cfg["image"]:
        aspect = closing.aspect_warning(*closing_card.image_size(Path(cfg["image"])))
        if aspect:
            warnings.append(aspect)
    brand = settings.brand_for(s, short)
    closing_card.make_card(cfg, brand, png, short.style.get("font") or brand.get("font"))
    warnings += closing.zone_warnings(read_png(png))
    info.write_text(json.dumps({**cfg, "warnings": warnings}, indent=2, ensure_ascii=False), encoding="utf-8")
    return cfg, png, warnings


# --------------------------------------------------------------------------- commands


def cmd_stills(args) -> dict:
    from bsa.blender.stills import render_still

    sdir = _short_dir(args.short)
    short = _load(sdir)
    if args.out and not args.shot:
        raise CliError("--out needs --shot (one still goes to one file)")
    tl, tl_hash = _timeline(sdir, short)
    spans = {s.id: s for s in tl.shots}
    data = _open_blend(sdir)
    engine = _engine(sdir)
    written = []
    for shot in _shots(short, args.shot):
        shot, scene = _scene(data, short, shot.id, tl_hash)
        out = Path(args.out) if args.out else sdir / "stills" / f"{shot.id}.jpg"
        render_still(scene, _still_frame(shot, spans[shot.id]), out, engine=engine)
        written.append((shot.id, out))
    result = {"stills": [str(p) for _, p in written]}
    warnings = _background_warnings(short, written)
    if not args.shot:  # the storyboard shows the closing card too
        _, card, card_warnings = _closing_card(sdir, short)
        if card is not None:
            result["closing"] = str(card)
        warnings += card_warnings
    return {**result, "warnings": warnings} if warnings else result


def _background_warnings(short, stills) -> list[str]:
    """Each still whose background (its most common colour) is far from the intended one, when one is set: the
    short's style.palette.background, else the brand's (unless the short has its own look, "look": "fresh")."""
    from bsa.colour import background_source, background_warning

    found = background_source(short, settings.load_settings())
    if not found:
        return []
    want, whose = found
    from bsa.blender.pixels import read_png

    out = []
    for shot_id, path in stills:
        msg = background_warning(read_png(path), want, f"shot {shot_id}", whose=whose)
        if msg:
            out.append(msg)
    return out


def _check_near_objects(data, short, tl_hash: str) -> None:
    """Every near label's object exists in its shot's scene, else one plain line naming the label and the object."""
    from bsa.short import near_labels

    for shot_id, text, name in near_labels(short):
        _, scene = _scene(data, short, shot_id, tl_hash)
        if scene.objects.get(name) is None:
            raise CliError(f"shot {shot_id} label {text!r} is near {name!r}, which scene {scene.name!r} does not have "
                           "(use the object's exact name)")  # fmt: skip


def _write_anchors(sdir: Path, short, tl, tl_hash: str, data, fps: dict | None = None) -> dict:
    """Project every near label's object for its shot and write source/anchors.json, stamped with each shot's
    fingerprint and the timing; shots unchanged since the last anchors keep theirs. Also the screen boxes of the
    heroes of every shot with a near label (labels keep off them) and of the last shot, with the last shot's other
    meshes during the end card ("scenery": the end card keeps off them after the heroes). Returns its path, how many
    labels it places and which shots it projected again."""
    from bsa.blender.anchors import hero_boxes, project, scenery_boxes
    from bsa.blender.anchors import heroes as hero_names
    from bsa.short import near_labels
    from bsa.timeline import end_card_start

    _check_near_objects(data, short, tl_hash)
    if fps is None:
        fps = _fingerprints(sdir, short, tl, tl_hash, data)
    reuse = freshness.reusable_anchors(sdir, fps, tl_hash)
    spans = {s.id: s for s in tl.shots}
    shots: dict[str, dict] = {}
    projected: set[str] = set()
    for shot_id, _text, name in near_labels(short):
        if name in shots.get(shot_id, {}):
            continue
        prior = reuse["shots"].get(shot_id) or {}
        if name in prior:
            shots.setdefault(shot_id, {})[name] = prior[name]
            continue
        _, scene = _scene(data, short, shot_id, tl_hash)
        projected.add(shot_id)
        try:
            shots.setdefault(shot_id, {}).update(project(scene, [name], spans[shot_id].f0, spans[shot_id].f1))
        except ValueError as exc:
            raise CliError(str(exc)) from exc
    last = short.shots[-1]  # the end card shows over the last shot: it must stay off that shot's heroes
    heroes: dict[str, dict] = {}
    for shot_id in [*sorted(shots), last.id]:  # near labels keep off the other heroes of their shot
        if shot_id in heroes:
            continue
        _, scene = _scene(data, short, shot_id, tl_hash)
        names = hero_names(scene, shot_id)
        span = spans[shot_id]
        end_f0 = min(max(end_card_start(tl), span.f0), span.f1) if shot_id == last.id else None
        prior = reuse["heroes"].get(shot_id)
        if (isinstance(prior, dict) and isinstance(prior.get("boxes"), dict) and set(prior["boxes"]) == set(names)
                and (end_f0 is None or (prior.get("scenery") or {}).get("f0") == end_f0)):  # fmt: skip
            heroes[shot_id] = prior
            continue
        projected.add(shot_id)
        try:
            heroes[shot_id] = hero_boxes(scene, names, span.f0, span.f1)
            if end_f0 is not None:
                heroes[shot_id]["scenery"] = scenery_boxes(scene, end_f0, span.f1, shot_id)
        except ValueError as exc:
            raise CliError(str(exc)) from exc
    out = {"stamp": freshness.anchors_stamp(fps, tl_hash), "shots": shots, "heroes": heroes}
    path = sdir / "source" / freshness.ANCHORS
    path.write_text(json.dumps(out) + chr(10), encoding="utf-8")
    return {"path": str(path), "labels": sum(len(v) for v in shots.values()), "heroes": len(heroes[last.id]["boxes"]),
            "projected": sorted(projected)}  # fmt: skip


def cmd_anchors(args) -> dict:
    sdir = _short_dir(args.short)
    short = _load(sdir)
    tl, tl_hash = _timeline(sdir, short)
    return _write_anchors(sdir, short, tl, tl_hash, _open_blend(sdir))


def cmd_estimate(args) -> dict:
    from bsa.blender.render import estimate_seconds

    sdir = _short_dir(args.short)
    short = _load(sdir)
    tl, tl_hash = _timeline(sdir, short)
    data = _open_blend(sdir)
    per_scene = []
    totals = {"eevee": 0.0, "cycles": 0.0}
    for name, items in _by_scene(short, tl).items():
        shot, scene = _scene(data, short, items[0][0].id, tl_hash)
        frame = _still_frame(shot, items[0][1])
        n = sum(span.f1 - span.f0 + 1 for _, span in items)
        row = {"scene": name, "frames": n}
        for engine in totals:
            secs = estimate_seconds(scene, frame, engine, n)
            row[f"{engine}_s"] = round(secs, 1)
            totals[engine] += secs
        per_scene.append(row)
    out = {"frames": tl.n_frames, "eevee_s": round(totals["eevee"], 1), "cycles_s": round(totals["cycles"], 1),
           "per_scene": per_scene}  # fmt: skip
    path = sdir / "review" / "estimate.json"  # the live render status seeds its time left from this
    path.parent.mkdir(parents=True, exist_ok=True)
    stamp = {"blend": _mtime(Path(data.filepath)) if data.filepath else None, "timeline": tl_hash}
    path.write_text(json.dumps({"stamp": stamp, **out}, indent=2) + "\n", encoding="utf-8")
    return {**out, "path": str(path)}


def _redo_targets(sdir: Path, args, short, spans) -> dict[str, list[int]] | None:
    """{shot id: frames} to render again for --frames or --redo-flagged; None for a normal render."""
    if getattr(args, "frames", None):
        if not args.shot:
            raise CliError("--frames needs --shot (frame numbers are the short's, so say whose shot they are in)")
        try:
            return {args.shot: freshness.parse_frames(args.frames)}
        except ValueError as exc:
            raise CliError(str(exc)) from exc
    if not getattr(args, "redo_flagged", False):
        return None
    path = sdir / "review" / f"frame-checks-{args.preset}.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise CliError(f"--redo-flagged reads {path.name}, which is missing or unreadable: run BLENDER frame-check "
                       f"SHORT --preset {args.preset} first") from exc  # fmt: skip
    out: dict[str, set[int]] = {}
    for f in data.get("findings") or []:
        sid = f.get("shot") if isinstance(f, dict) else None
        if sid in spans and (args.shot is None or sid == args.shot):
            span = spans[sid]
            out.setdefault(sid, set()).update(n for n in f.get("redo_frames") or [] if span.f0 <= n <= span.f1)
    return {sid: sorted(frames) for sid, frames in out.items() if frames}


def _recording(sdir: Path, short, preset: str, step: str):
    """The live status this step records itself in (review/render-status-<preset>.json and its page): inside the
    preview chain the chain's own step, on its own a one-step run that supersedes the last run shown there."""
    from bsa import render_status as rs
    from bsa.review import pages

    page = rs.page_path(sdir, preset)
    return rs.recording(rs.status_path(sdir, preset), short={"slug": short.slug, "title": short.title, "dir": str(sdir)},
                        preset=preset, step=step,
                        render_page=lambda status: pages.render_status_page(rs.describe(status), page))  # fmt: skip


def _progress_cb(fn):
    """A progress callback that can never stop the work it reports on."""

    def call(*a, **kw):
        try:
            fn(*a, **kw)
        except Exception as exc:  # noqa: BLE001 - status only
            print(f"status: {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)

    return call


def cmd_render(args) -> dict:
    sdir = _short_dir(args.short)
    if args.preset == "final":
        freshness.require_current_build(sdir, "the final render")
    short = _load(sdir)
    with _recording(sdir, short, args.preset, "render") as run, _gpu(args, sdir, short.slug, "render", args.preset):
        out = _cmd_render(args, sdir, short, run)
        run.summarize("render", _render_summary(out))
    return out


def _cmd_render(args, sdir: Path, short, run) -> dict:
    from bsa.blender.render import render_range

    tl, tl_hash = _timeline(sdir, short)
    spans = {s.id: s for s in tl.shots}
    redo = _redo_targets(sdir, args, short, spans)
    shots = _shots(short, args.shot) if redo is None else [s for s in _shots(short, args.shot) if s.id in redo]
    if redo is not None and not shots:
        return {"preset": args.preset, "shots": [], "redo": {},
                "note": f"no flagged frames in review/frame-checks-{args.preset}.json: nothing to render again"}
    data = _open_blend(sdir)
    engine = _engine(sdir)
    fps = _fingerprints(sdir, short, tl, tl_hash, data)
    if args.preset == "final":  # the final render needs checks that passed on every shot as it is now
        freshness.require_checks(sdir, tl_hash, _check_keys(short, tl, fps, _parts(sdir, fps)), "the final render")
    _check_near_objects(data, short, tl_hash)  # before any frame: anchors run after the render
    parts = _parts(sdir, fps)
    stamps = {s.id: freshness.frames_stamp(spans[s.id], engine, (fps or {}).get(s.id), args.preset, parts.get(s.id))
              for s in shots}  # fmt: skip
    tracker, watch, plan = _render_watch(sdir, short, args.preset, engine, shots, spans, stamps, fps, tl_hash, redo,
                                         run=run)  # fmt: skip
    done, warnings = [], []
    with watch:
        tracker.start()
        try:
            for shot in shots:
                shot, scene = _scene(data, short, shot.id, tl_hash)
                span = spans[shot.id]
                folder = sdir / "frames" / args.preset / shot.id
                if redo is not None:
                    patch = freshness.redo_frames(folder, span, stamps[shot.id], redo[shot.id])
                    if patch["mixed"]:
                        warnings.append(f"shot {shot.id} changed since its frames were rendered: only frames "
                                        f"{freshness.frames_spec(patch['frames'])} are from this build, the rest from "
                                        "the earlier one (render the whole shot if the change shows there)")  # fmt: skip
                else:
                    freshness.prepare_frames(folder, stamps[shot.id])
                tracker.begin_shot(shot.id)
                p = plan[shot.id]
                if p["reason"] is None and p["reused"] == p["total"]:  # unchanged and complete: nothing to render
                    frames = [folder / f"{f:04d}.png" for f in range(span.f0, span.f1 + 1)]
                else:
                    frames = render_range(scene, span.f0, span.f1, folder, args.preset, engine=engine,
                                          on_start=watch.on_start)  # fmt: skip
                tracker.end_shot(shot.id)
                done.append({"shot": shot.id, "frames": len(frames), "rendered": p["total"] - p["reused"],
                             "kept": p["reused"], "why": p["reason"] or "unchanged", "folder": str(folder)})  # fmt: skip
            tracker.set_phase("anchors")
            anchors = _write_anchors(sdir, short, tl, tl_hash, data, fps)  # the edit places near labels from these
        except BaseException as exc:
            stopped = watch.cancelled or isinstance(exc, KeyboardInterrupt)
            tracker.finish("stopped" if stopped else "failed", error=_one_line(f"{exc}") or type(exc).__name__)
            raise
        tracker.finish("done")
    from bsa import render_status as rs

    out = {"preset": args.preset, "engine": engine, "shots": done, "anchors": anchors,
           "status": str(rs.status_path(sdir, args.preset)), "page": str(rs.page_path(sdir, args.preset))}  # fmt: skip
    if redo is not None:
        out["redo"] = {sid: freshness.frames_spec(frames) for sid, frames in redo.items()}
    if warnings:
        out["warnings"] = warnings
    return out


def _render_watch(sdir: Path, short, preset: str, engine, shots, spans, stamps, fps, tl_hash: str, redo=None, run=None):
    """The live status of this render (review/render-status-<preset>.json and review/render-<preset>.html), the
    render handlers that keep it current, and the plan per shot ({shot id: total, reused, stale, reason})."""
    from bsa import render_status as rs
    from bsa.blender.render_watch import Watch
    from bsa.review import pages

    plan = {}
    for shot in shots:
        span = spans[shot.id]
        folder = sdir / "frames" / preset / shot.id
        p = rs.frames_plan(folder, span.f0, span.f1, stamps[shot.id])
        if redo is not None:
            total = span.f1 - span.f0 + 1
            on_disk = sum(1 for f in range(span.f0, span.f1 + 1) if f not in redo[shot.id]
                          and (folder / f"{f:04d}.png").is_file())  # fmt: skip
            p = {"total": total, "reused": on_disk, "stale": 0,
                 "reason": f"redoing frames {freshness.frames_spec(redo[shot.id])}"}  # fmt: skip
        plan[shot.id] = p
    page = rs.page_path(sdir, preset)
    tracker = rs.Tracker(
        rs.status_path(sdir, preset),
        short={"slug": short.slug, "title": short.title, "dir": str(sdir)},
        preset=preset,
        engine=engine,
        shots=[{"id": s.id, "f0": spans[s.id].f0, "f1": spans[s.id].f1, **plan[s.id]} for s in shots],
        stamp={"timeline": tl_hash, "fingerprints": {s.id: (fps or {}).get(s.id) for s in shots}},
        seed_s_per_frame=rs.seed_from_estimate(sdir, engine, preset),
        render_page=lambda status: pages.render_status_page(rs.describe(status), page),
        run=run,
    )
    return tracker, Watch(tracker, sdir, preset), plan


def _check_row(finding, shot_id: str, scene_name: str) -> dict:
    """One scene-check finding as review/checks.json stores it (the review pages read these rows)."""
    return {**asdict(finding), "shot": shot_id, "scene": scene_name}


def _merge_findings(rows: list[dict]) -> list[dict]:
    """One row per distinct finding: the same scene-wide finding from two shots of one scene is listed once, with
    every shot it came from under "shots"."""
    out: dict[tuple, dict] = {}
    for f in rows:
        key = (f.get("check"), f.get("severity"), f.get("obj"), f.get("frame"), f.get("message"), f.get("scene"))
        shots = f.get("shots") or [f.get("shot")]
        if key in out:
            out[key]["shots"] = sorted(set(out[key]["shots"]) | set(shots))
        else:
            out[key] = {**f, "shots": sorted(set(shots))}
    return sorted(out.values(), key=lambda f: f.get("severity") != "error")


def cmd_check(args) -> dict:
    """Scene checks per shot into review/checks.json. Shots unchanged since the last checks (same fingerprint,
    frames, short.json scene and labels, shot list, checks code and --every) keep their findings; only the others
    are checked again (--full checks every shot). Its progress (frames checked of those to check, the shot it is on, time left) shows
    on the --preset's live status page."""
    sdir = _short_dir(args.short)
    short = _load(sdir)
    with _recording(sdir, short, getattr(args, "preset", None) or "preview", "check") as run:
        out = _cmd_check(args, sdir, short, run)
        run.summarize("check", {"warnings": out.get("warnings"), "checked": out.get("checked"), "kept": out.get("kept")})
    return out


def _cmd_check(args, sdir: Path, short, run) -> dict:
    from bsa.blender.checks import run_checks

    tl, tl_hash = _timeline(sdir, short)
    data = _open_blend(sdir)
    fps = _fingerprints(sdir, short, tl, tl_hash, data)
    stamp = freshness.checks_stamp(tl_hash, _check_keys(short, tl, fps, _parts(sdir, fps)), args.every)
    reuse = {} if getattr(args, "full", False) else freshness.reusable_checks(sdir, stamp)
    plan = [(shot, span) for items in _by_scene(short, tl).values() for shot, span in items]
    progress = [{"id": shot.id, "scene": shot.scene, "total": span.f1 - span.f0 + 1,
                 "done": span.f1 - span.f0 + 1 if shot.id in reuse else 0,
                 "state": "kept" if shot.id in reuse else "pending"} for shot, span in plan]  # fmt: skip
    total = sum(p["total"] for p in progress if p["state"] == "pending")
    counted = [0]  # frames checked in the shots finished so far
    run.progress(0, total, current=None, items=progress, force=True)
    rows: list[dict] = []
    checked, kept = [], []
    hard_rules = _hard_rules(short)  # scene names a rule may forbid are warnings (bsa.rules.name_hits)
    for (shot, span), item in zip(plan, progress, strict=True):
        if shot.id in reuse:
            rows += [{**f, "shots": [shot.id]} for f in reuse[shot.id]]
            kept.append(shot.id)
            continue
        _, scene = _scene(data, short, shot.id, tl_hash)
        item["state"] = "checking"
        where = f"shot {shot.id} (scene {scene.name})"

        def on_frame(_sid, done, _n, item=item, where=where):
            item["done"] = done
            run.progress(counted[0] + done, total, current=where)

        found = run_checks(scene, replace(tl, shots=[span]), sample_every=args.every, short=short,
                           progress=_progress_cb(on_frame), rules=hard_rules)  # fmt: skip
        item["state"], item["done"] = "done", item["total"]
        counted[0] += item["total"]
        rows += [_check_row(f, shot.id, scene.name) for f in found]
        checked.append(shot.id)
    run.progress(counted[0], total, current=None, force=True)
    out = _write_findings(sdir / "review" / "checks.json", _merge_findings(rows), stamp, checked=checked, kept=kept)
    if out["errors"]:
        raise CliError(f"{out['errors']} check error(s); see review/checks.json and fix them before rendering", out)
    jobstate.mark_done(sdir, "checks", warnings=out["warnings"])
    return out


def _rendered(sdir: Path, short, preset: str, tl, fps: dict | None) -> list:
    """(shot, frame paths) for every shot: exactly its current frames, rendered for this timing, engine and shot
    content (fingerprint)."""
    spans = {s.id: s for s in tl.shots}
    engine = _engine(sdir)
    folder = sdir / "frames" / preset
    parts = _parts(sdir, fps)
    return [(shot, freshness.rendered_frames(folder / shot.id, spans[shot.id], engine, (fps or {}).get(shot.id),
                                             preset, parts.get(shot.id))) for shot in short.shots]  # fmt: skip


REDO_AROUND = {"flicker": 1, "stuck_frame": 1}  # frames either side a redo takes too (flicker: t-1..t+1)


def _redo(sdir: Path, preset: str, finding, span) -> dict:
    """The frames to render again for one frame finding, and the command that does it."""
    if finding.frame is None:
        return {}
    k = REDO_AROUND.get(finding.check, 0)
    lo = finding.frame - k
    hi = finding.frame + (k if finding.check == "flicker" else 0)  # a stuck frame: it and the one it repeats
    frames = list(range(max(span.f0, lo), min(span.f1, hi) + 1))
    spec = freshness.frames_spec(frames)
    return {"redo_frames": frames,
            "redo": f'BLENDER render "{sdir}" --preset {preset} --shot {span.id} --frames {spec}'}  # fmt: skip


def cmd_frame_check(args) -> dict:
    from bsa.blender.pixels import read_png
    from bsa.image_checks import frame_checks

    sdir = _short_dir(args.short)
    short = _load(sdir)
    with _recording(sdir, short, args.preset, "frame-check") as run:
        out = _cmd_frame_check(args, sdir, short, run, frame_checks, read_png)
        run.summarize("frame-check", {"warnings": out.get("warnings")})
    return out


def _motion(sdir: Path, short, tl, tl_hash: str) -> dict:
    """{shot id: frames where a hero or the camera moves} from blend/short.blend, so a stuck frame is reported only
    where motion was keyed; {} when the scenes can't be read (then every stuck frame is reported)."""
    if not _blend(sdir).is_file():
        return {}
    try:
        from bsa.blender.motion import moving_frames

        data = _open_blend(sdir)
        spans = {s.id: s for s in tl.shots}
        return {shot.id: moving_frames(_scene(data, short, shot.id, tl_hash)[1], spans[shot.id].f0, spans[shot.id].f1,
                                       shot.id) for shot in short.shots}  # fmt: skip
    except Exception as exc:  # noqa: BLE001 - the motion only narrows a warning
        print(f"frame-check: motion not read ({exc}); every stuck frame is reported", file=sys.stderr)
        return {}


FC_CACHE_VERSION = 1


def _fc_code() -> str:
    """What the frame findings depend on besides the frames: the checks' and the decoder's code."""
    import hashlib

    h = hashlib.sha256(str(FC_CACHE_VERSION).encode())
    for rel in ("bsa/image_checks.py", "bsa/blender/pixels.py"):
        p = PLUGIN_ROOT / rel
        h.update(p.read_bytes() if p.is_file() else rel.encode())
    return h.hexdigest()[:16]


def _fc_keys(frames: list[Path]) -> list:
    """Each frame file as [name, size, mtime in ns]: a frame rendered again changes at least its mtime."""
    out = []
    for p in frames:
        try:
            st = p.stat()
            out.append([p.name, st.st_size, st.st_mtime_ns])
        except OSError:
            out.append([p.name, None, None])
    return out


def _load_fc_cache(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    shots = data.get("shots") if isinstance(data, dict) else None
    return shots if isinstance(shots, dict) else {}


def _save_fc_cache(path: Path, shots: dict) -> None:
    from bsa import render_status as rs

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        rs.write_atomic(path, json.dumps({"version": FC_CACHE_VERSION, "shots": shots}))
    except OSError as exc:  # a cache only: the next frame-check reads every frame again
        print(f"frame-check: cache not written ({exc})", file=sys.stderr)


def _finding(d: dict):
    """A frame finding back from its cached dict."""
    from bsa.image_checks import Finding, FlickerFinding

    if "box" in d:
        return FlickerFinding(d["check"], d["severity"], d.get("obj"), d.get("frame"), d["message"], tuple(d["box"]),
                              d.get("share", 0.0))  # fmt: skip
    return Finding(d["check"], d["severity"], d.get("obj"), d.get("frame"), d["message"])


def _fc_workers() -> int:
    """Threads for decoding and checking frames: several when Blender's loader is safe in threads (5.0+), else 1."""
    try:
        from bsa.blender.pixels import thread_safe
    except ImportError:  # not inside Blender (tests)
        return 1
    from bsa.image_checks import default_workers

    return default_workers() if thread_safe() else 1


def _cmd_frame_check(args, sdir: Path, short, run, frame_checks, read_png) -> dict:
    from bsa.timeline import held_frames

    tl, tl_hash = _timeline(sdir, short)
    spans = {s.id: s for s in tl.shots}
    fps = _fingerprints(sdir, short, tl, tl_hash)
    motion = _motion(sdir, short, tl, tl_hash)
    rows = []
    shots = _rendered(sdir, short, args.preset, tl, fps)
    total = sum(len(frames) for _, frames in shots)
    items = [{"id": shot.id, "total": len(frames), "done": 0, "state": "pending"} for shot, frames in shots]
    run.progress(0, total, items=items, force=True)
    cache_path = sdir / "review" / f"frame-check-cache-{args.preset}.json"
    cache, code = _load_fc_cache(cache_path), _fc_code()
    kept = {}
    workers = _fc_workers()
    before = 0
    for (shot, frames), item in zip(shots, items, strict=True):  # one shot at a time: never across a cut
        held = held_frames(shot, spans[shot.id], short.fps)
        moving = motion.get(shot.id)
        key = {"code": code, "frames": _fc_keys(frames), "moving": sorted(moving) if moving is not None else None,
               "held": sorted(held)}  # fmt: skip
        hit = cache.get(shot.id)
        if hit is not None and hit.get("key") == key:  # every frame as it was when last checked: same findings
            found = [_finding(d) for d in hit.get("findings") or []]
            item.update(state="done", done=len(frames), cached=True)
            run.progress(before + len(frames), total, current=f"shot {shot.id} (unchanged, kept)")
        else:
            item["state"] = "checking"

            def on_frame(done, _n, item=item, before=before, where=f"shot {shot.id}"):
                item["done"] = done
                run.progress(before + done, total, current=where)

            found = frame_checks(frames, load=read_png, progress=_progress_cb(on_frame), moving=moving, held=held,
                                 workers=workers)  # Blender's image loader, in threads where it can  # fmt: skip
            item["state"] = "done"
        kept[shot.id] = {"key": key, "findings": [asdict(f) for f in found]}
        for f in found:
            rows.append({**asdict(f), "shot": shot.id, **_redo(sdir, args.preset, f, spans[shot.id])})
        before += len(frames)
    _save_fc_cache(cache_path, kept)
    run.progress(before, total, force=True)
    rows.sort(key=lambda f: f["severity"] != "error")
    flagged = sum(1 for f in rows if f.get("redo_frames"))
    extra = {"redo_all": f'BLENDER render "{sdir}" --preset {args.preset} --redo-flagged'} if flagged else {}
    out = _write_findings(sdir / "review" / f"frame-checks-{args.preset}.json", rows, **extra)
    if out["errors"]:
        raise CliError(f"{out['errors']} frame error(s); see review/frame-checks-{args.preset}.json", out)
    return out


def cmd_edit(args) -> dict:
    from bsa.blender import edit

    sdir = _short_dir(args.short)
    if args.preset == "final":
        freshness.require_current_build(sdir, "the final edit")
    short = _load(sdir)
    with _recording(sdir, short, args.preset, "edit") as run:
        out = _cmd_edit(args, sdir, short, run, edit)
        run.summarize("edit", {"mp4": out.get("mp4")})
    return out


def _cmd_edit(args, sdir: Path, short, run, edit) -> dict:
    out_mp4 = _mp4(sdir, short, args.preset)
    out_mp4.unlink(missing_ok=True)  # a stale video must never pass for this one if the encode fails
    tl, tl_hash = _timeline(sdir, short)
    fps = _fingerprints(sdir, short, tl, tl_hash, reset_after=True)
    _rendered(sdir, short, args.preset, tl, fps)  # every shot's exact frames, from this timing, engine and content
    if args.preset == "final":
        freshness.require_checks(sdir, tl_hash, _check_keys(short, tl, fps, _parts(sdir, fps)), "the final edit")
    from bsa.short import near_labels

    need = near_labels(short)
    anchors = freshness.load_anchors(sdir, fps, tl_hash, need) if need else None
    label_heroes = _label_heroes(sdir, fps, tl_hash, need)
    warnings, heroes = [], None
    if settings.has_end_card(settings.load_settings(), short):  # the end card is polish: without anchors it is only warned
        try:
            last = short.shots[-1].id
            heroes = freshness.load_hero_boxes(sdir, fps, tl_hash, last).get(last)
            if not isinstance(heroes, dict) or not isinstance(heroes.get("boxes"), dict):
                heroes = None
                warnings.append("hero positions not recorded: run BLENDER anchors SHORT (the end card stays in the "
                                "upper third unchecked)")  # fmt: skip
        except freshness.StaleError as exc:
            warnings.append(f"the end card stays in the upper third without checking it is clear of the heroes ({exc})")
    out_mp4.parent.mkdir(parents=True, exist_ok=True)
    mix = sdir / "audio" / "mix.wav"
    end, card_warnings = _closing_plan(sdir, short, tl, mix)
    warnings += card_warnings
    n_frames = tl.n_frames + (end["n_frames"] if end else 0)
    scene = edit.build_edit(short, tl, sdir / "frames" / args.preset, mix if mix.is_file() else None, out_mp4,
                            settings.load_settings(), preset=args.preset, anchors=anchors, hero_boxes=heroes,
                            label_heroes=label_heroes, closing=end)  # fmt: skip
    run.progress(0, n_frames, current="encoding the MP4", force=True)
    path = edit.encode(scene, progress=lambda done, n: run.progress(done, n, current="encoding the MP4"))
    info = edit.probe(path)
    want = {"width": short.width, "height": short.height, "frames": n_frames}
    wrong = {k: (info.get(k), v) for k, v in want.items() if info.get(k) != v}
    if abs(float(info.get("fps") or 0) - short.fps) > 0.01:
        wrong["fps"] = (info.get("fps"), short.fps)
    if mix.is_file() and not info.get("has_audio"):  # the mix must be in the video, not silently dropped
        wrong["has_audio"] = (info.get("has_audio"), True)
    if wrong:
        raise CliError(f"the encoded video is off: {wrong} (got, wanted)", {"mp4": str(path), **info})
    jobstate.mark_done(sdir, args.preset, mp4=str(path), **info)
    if edit.end_card_note(scene):
        warnings.append(edit.end_card_note(scene))
    warnings += edit.glyph_fallbacks(scene)  # characters the style font lacks: drawn in another font, or boxes
    out = {"mp4": str(path), **info, "font": edit.font_used(scene), "font_fallback": edit.font_note(scene)}
    return {**out, "warnings": warnings} if warnings else out


def _closing_plan(sdir: Path, short, tl, mix: Path) -> tuple[dict | None, list[str]]:
    """(where the closing card goes in the edit, with its "png"; or None, warnings). The mix must already hold the
    card's frames (CLI mix lays the closing line over them), else the video and its sound would differ in length."""
    import wave

    from bsa import closing
    from bsa.narrate.run import takes_durations

    cfg, png, warnings = _closing_card(sdir, short)
    if cfg is None:
        return None, warnings
    takes = sdir / "audio" / "vo" if takes_durations(sdir, short) is not None else None
    end = closing.plan(cfg, tl.n_frames + 1, tl.fps, closing.take_seconds(takes))
    if mix.is_file():
        with wave.open(str(mix), "rb") as w:
            frames = round(w.getnframes() / w.getframerate() * tl.fps)
        if abs(frames - (tl.n_frames + end["n_frames"])) > 1:
            raise CliError("audio/mix.wav was made without this closing card (or for another one): run CLI mix SHORT "
                           "again, then the edit")  # fmt: skip
    if cfg["voice_line"] and takes is not None and end["line_f0"] is None:
        warnings.append("the closing line has no take yet, so the closing card is silent: run CLI narrate SHORT "
                        "again (it makes only the missing take), then CLI mix SHORT and the edit")  # fmt: skip
    return {**end, "png": str(png)}, warnings


def cmd_closing(args) -> dict:
    """Make the short's closing card (stills/closing.png) and say what it will be: for the review pages and setup."""
    sdir = _short_dir(args.short)
    short = _load(sdir)
    cfg, png, warnings = _closing_card(sdir, short)
    out = {"closing": None if cfg is None else {**cfg, "png": str(png)}}
    return {**out, "warnings": warnings} if warnings else out


def _label_heroes(sdir: Path, fps: dict, tl_hash: str, need) -> dict:
    """{shot: {"f0", "boxes"}}: the heroes on screen in each shot with a near label, from anchors.json (current, as
    the anchors the labels use). Near labels keep off them; {} (labels placed as before) when none are recorded."""
    out = {}
    for shot_id in sorted({s for s, _, _ in need}):
        try:
            entry = freshness.load_hero_boxes(sdir, fps, tl_hash, shot_id).get(shot_id)
        except freshness.StaleError:
            continue
        if isinstance(entry, dict) and isinstance(entry.get("boxes"), dict):
            out[shot_id] = entry
    return out


def cmd_grab(args) -> dict:
    """Frames of the edited MP4 at full size and its own colours (or a contact sheet), into review/grabs/."""
    sdir = _short_dir(args.short)
    short = _load(sdir)
    mp4 = _mp4(sdir, short, args.preset)
    if not mp4.is_file():
        raise CliError(f"{mp4} does not exist yet: run BLENDER edit SHORT --preset {args.preset} first")
    from bsa.blender import grab
    from bsa.blender.edit import probe

    info = probe(mp4)
    fps, n = float(info["fps"]), int(info["frames"])
    folder = sdir / "review" / "grabs"
    if args.sheet:
        frames = grab.sheet_frames(n, args.count)
        sheet = grab.contact_sheet(mp4, frames, folder / f"{args.preset}-sheet.png", columns=args.columns)
        return {"mp4": str(mp4), "sheet": str(sheet), "frames": frames,
                "seconds": [round((f - 1) / fps, 2) for f in frames]}  # fmt: skip
    try:
        frames = [grab.frame_at(t, fps, n) for t in args.at]
    except ValueError as exc:
        raise CliError(str(exc)) from exc
    grab.grab_frames(mp4, list(dict.fromkeys(frames)), folder, args.preset)
    return {"mp4": str(mp4), "grabs": [{"seconds": t, "frame": f, "path": str(folder / f"{args.preset}-f{f:04d}.png")}
                                      for t, f in zip(args.at, frames, strict=True)]}  # fmt: skip


def cmd_extremes(args) -> dict:
    """Full-resolution stills of each shot's heroes (and their parts) where each animated channel is at its min and
    max, through the shot's camera, into review/extremes/."""
    from bsa.blender.extremes import render_extremes

    sdir = _short_dir(args.short)
    short = _load(sdir)
    tl, tl_hash = _timeline(sdir, short)
    spans = {s.id: s for s in tl.shots}
    data = _open_blend(sdir)
    engine = _engine(sdir)
    folder = sdir / "review" / "extremes"
    shots = []
    for shot in _shots(short, args.shot):
        shot, scene = _scene(data, short, shot.id, tl_hash)
        for old in folder.glob(f"{shot.id}-f*.jpg"):  # a still from an older build must not pass for this one
            old.unlink()
        rows = render_extremes(scene, shot.id, spans[shot.id].f0, spans[shot.id].f1, folder, engine=engine)
        if rows:
            shots.append({"shot": shot.id, "scene": scene.name, "stills": rows})
    out = {"folder": str(folder), "stills": sum(len(s["stills"]) for s in shots), "shots": shots}
    if not shots:
        out["warnings"] = [("no hero moves in these shots: tag the moving subject hero (or hero:<shot ids>) in "
                            "build.py, with its moving parts parented under it")]  # fmt: skip
    return out


# ----- the preview chain: everything after an edit to build.py or short.json, up to a checked preview


def _build_script_mode(sdir: Path) -> Path:
    """Run source/build.py in a fresh background Blender (script mode, exactly as 06-build.md runs it)."""
    import subprocess

    import bpy

    build = sdir / "source" / "build.py"
    if not build.is_file():
        raise CliError(f"{build} does not exist: write it first (06-build.md)")
    cmd = [bpy.app.binary_path, "-b", "--factory-startup", "--python-exit-code", "1", "--python", str(build), "--",
           str(PLUGIN_ROOT), str(sdir)]  # fmt: skip
    proc = subprocess.run(cmd, check=False, capture_output=True, text=True, encoding="utf-8", errors="replace")
    blend = _blend(sdir)
    if proc.returncode != 0 or not blend.is_file():
        lines = [ln.strip() for ln in (proc.stderr or proc.stdout or "").splitlines() if ln.strip()]
        raise CliError("source/build.py failed: " + (lines[-1] if lines else f"exit code {proc.returncode}"))
    return blend


def _reopen(sdir: Path) -> None:
    """Open blend/short.blend again (the build just rewrote it)."""
    import bpy

    bpy.ops.wm.open_mainfile(filepath=str(_blend(sdir)))


def _scenes_current(sdir: Path, short, tl_hash: str) -> bool:
    """blend/short.blend has every scene short.json names, each built for this timing."""
    if not _blend(sdir).is_file():
        return False
    data = _open_blend(sdir)
    for name in {s.scene for s in short.shots}:
        scene = data.scenes.get(name)
        if scene is None or str(scene.get(freshness.TIMELINE_PROP)) != tl_hash:
            return False
    return True


def _marked_after(sdir: Path, stage: str, path: Path) -> bool:
    """`stage` is marked done, and was marked after `path` was last written."""
    from datetime import datetime

    info = jobstate.load_state(sdir)["stages"].get(stage)
    if not info or not path.is_file():
        return False
    try:
        return datetime.fromisoformat(str(info.get("at"))).timestamp() >= int(path.stat().st_mtime)
    except ValueError:
        return False


def _checks_current(sdir: Path, short, tl, tl_hash: str) -> bool:
    """The checks passed on every shot as it is now and this timing (and the checks stage is marked)."""
    try:
        fps = _fingerprints(sdir, short, tl, tl_hash)
        freshness.require_checks(sdir, tl_hash, _check_keys(short, tl, fps, _parts(sdir, fps)))
    except freshness.StaleError:
        return False
    return "checks" in jobstate.load_state(sdir)["stages"]


def cmd_preview(args) -> dict:
    """After an edit: (timeline,) build, mark build done, mix, check, render the preview, frame-check, edit, each
    through the same code as its own command. Steps already current are skipped; it stops at the first failure."""
    import bsa_cli

    sdir = _short_dir(args.short)
    args.short = str(sdir)  # resolved once: a short made meanwhile can't make a number ambiguous mid-chain
    stages = jobstate.load_state(sdir)["stages"]
    missing = [s for s in jobstate.STAGES[: jobstate.STAGES.index("build")] if s not in stages]
    if missing:
        raise CliError("BLENDER preview rebuilds a short that is past narration; this one still needs: "
                       f"{', '.join(missing)} (CLI state SHORT)")  # fmt: skip
    short = _load(sdir)
    from bsa import render_status as rs
    from bsa.review import pages

    page = rs.page_path(sdir, "preview")
    with rs.Run(rs.status_path(sdir, "preview"), short={"slug": short.slug, "title": short.title, "dir": str(sdir)},
                preset="preview", kind="preview", steps=rs.CHAIN_STEPS,
                render_page=lambda status: pages.render_status_page(rs.describe(status), page)) as chain:  # fmt: skip
        return _preview_chain(args, sdir, short, chain, bsa_cli)


def _preview_chain(args, sdir: Path, short, chain, bsa_cli) -> dict:
    """The steps of BLENDER preview, each recorded in `chain` (the live status of the preview) as it runs, is
    skipped or fails."""
    steps: list[dict] = []
    ns = argparse.Namespace

    def run(name, fn, *fn_args, summary=lambda out: {}):
        with chain.step(name):
            try:
                out = fn(*fn_args) or {}
            except (CliError, bsa_cli.CliError, freshness.StaleError) as exc:
                payload = getattr(exc, "payload", None) or {}
                raise CliError(f"stopped at {name}: {exc}", {"steps": steps, "stopped_at": name, **payload}) from exc
            steps.append({"step": name, **summary(out)})
            chain.summarize(name, summary(out))
        return out

    def skip(name, why):
        steps.append({"step": name, "skipped": why})
        chain.skip(name, why)

    def stop(name, message, exc=None):
        chain.begin(name)  # the run ends failed at this step
        raise CliError(message, {"steps": steps, "stopped_at": name}) from exc

    rebuild = args.rebuild
    try:
        tl, tl_hash = _timeline(sdir, short)
        chain.skip("timeline", "the timing is current")
    except freshness.StaleError as exc:
        if args.no_build:
            stop("timeline", f"stopped at timeline: {exc}", exc)
        run("timeline", bsa_cli.cmd_timeline, ns(short=args.short), summary=lambda o: {"n_frames": o.get("n_frames")})
        tl, tl_hash = _timeline(sdir, short)
        rebuild = True
    if rebuild:
        why = "asked" if args.rebuild else "the timing changed"
    elif not _blend(sdir).is_file():
        why = "no blend/short.blend yet"
    elif freshness.build_changed(sdir):
        why = "build.py changed since the scene was built"
    elif not _scenes_current(sdir, short, tl_hash):
        why = "the .blend's scenes are missing or from another timing"
    else:
        why = None
    if why and args.no_build:
        stop("build", f"the scene file is not current ({why}): rebuild it through the MCP (bsa_mcp_build.py), then "
                      "run BLENDER preview SHORT --no-build again")  # fmt: skip
    if why:
        run("build", _build_script_mode, sdir, summary=lambda o: {"why": why})
        _reopen(sdir)
    else:
        skip("build", "the .blend is current")
    if why or not _marked_after(sdir, "build", _blend(sdir)):
        run("build done", bsa_cli.cmd_state, ns(short=args.short, action="done", stage="build", info=None))
    else:
        chain.skip("build done", "already marked after the scene was built")
    run("mix", bsa_cli.cmd_mix, ns(short=args.short),
        summary=lambda o: {"path": o.get("path"), "sfx_notes": (o.get("sfx") or {}).get("notes")})  # fmt: skip
    if _checks_current(sdir, short, tl, tl_hash):
        skip("check", "already passed on every shot as it is now")
    else:
        run("check", cmd_check, ns(short=args.short, every=args.every, full=False, preset="preview"),
            summary=lambda o: {"warnings": o.get("warnings"), "checked": o.get("checked"), "kept": o.get("kept")})
    run("render", cmd_render, ns(short=args.short, preset="preview", shot=None, frames=None, redo_flagged=False,
                                 no_wait=getattr(args, "no_wait", False)),
        summary=_render_summary)  # fmt: skip
    run("frame-check", cmd_frame_check, ns(short=args.short, preset="preview"),
        summary=lambda o: {"warnings": o.get("warnings")})  # fmt: skip
    edit = run("edit", cmd_edit, ns(short=args.short, preset="preview"), summary=lambda o: {"mp4": o.get("mp4")})
    out = {"steps": steps, "mp4": edit.get("mp4"), "checks": str(sdir / "review" / "checks.json"),
           "frame_checks": str(sdir / "review" / "frame-checks-preview.json")}  # fmt: skip
    if edit.get("warnings"):
        out["warnings"] = edit["warnings"]
    return out


def _render_summary(out: dict) -> dict:
    """Which shots the render kept and which it rendered again, and why ("fingerprint changed", "new range"...)."""
    shots = out.get("shots", [])
    return {"frames": sum(s.get("rendered", 0) for s in shots),
            "kept": [s["shot"] for s in shots if s.get("rendered") == 0],
            "rendered": [{"shot": s["shot"], "frames": s.get("rendered"), "why": s.get("why")}
                         for s in shots if s.get("rendered")]}  # fmt: skip


def cmd_probe(args) -> dict:
    from bsa.blender.edit import probe

    mp4 = Path(args.mp4)
    if not mp4.is_file():
        raise CliError(f"{mp4} does not exist")
    return {"mp4": str(mp4.resolve()), **probe(mp4)}


# --------------------------------------------------------------------------- parser


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="bsa_blender.py", description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="command", required=True)

    sp = sub.add_parser("stills", help="a still per shot at its still_at (or one shot to --out)")
    sp.add_argument("short")
    sp.add_argument("--shot")
    sp.add_argument("--out")
    sp.set_defaults(fn=cmd_stills)

    sp = sub.add_parser("anchors", help="where each near label's object is on screen, into source/anchors.json")
    sp.add_argument("short")
    sp.set_defaults(fn=cmd_anchors)

    sp = sub.add_parser("estimate", help="final render time for EEVEE and Cycles, in seconds")
    sp.add_argument("short")
    sp.add_argument("--no-wait", action="store_true", help="fail at once when another command holds the GPU lock")
    sp.set_defaults(fn=cmd_estimate)

    sp = sub.add_parser("render", help="render PNG frames (resumable; only shots that changed render again)")
    sp.add_argument("short")
    sp.add_argument("--preset", required=True, choices=["preview", "final"])
    sp.add_argument("--shot")
    redo = sp.add_mutually_exclusive_group()
    redo.add_argument("--frames", help="with --shot: render just these frames again, e.g. 120-140,200")
    redo.add_argument("--redo-flagged", action="store_true",
                      help="render again the frames the last frame-check flagged (flicker: and one either side)")
    sp.add_argument("--no-wait", action="store_true", help="fail at once when another command holds the GPU lock")
    sp.set_defaults(fn=cmd_render)

    sp = sub.add_parser("check", help="scene checks into review/checks.json (only shots that changed)")
    sp.add_argument("short")
    sp.add_argument("--every", type=int, default=5, help="check every Nth frame (pops use every frame)")
    sp.add_argument("--full", action="store_true", help="check every shot, changed or not")
    sp.add_argument("--preset", choices=("preview", "final"), default="preview",
                    help="the live status page that shows its progress (review/render-<preset>.html)")
    sp.set_defaults(fn=cmd_check)

    sp = sub.add_parser("frame-check", help="checks on rendered frames")
    sp.add_argument("short")
    sp.add_argument("--preset", required=True, choices=["preview", "final"])
    sp.set_defaults(fn=cmd_frame_check)

    sp = sub.add_parser("edit", help="captions, sound and the MP4 encode")
    sp.add_argument("short")
    sp.add_argument("--preset", required=True, choices=["preview", "final"])
    sp.set_defaults(fn=cmd_edit)

    sp = sub.add_parser("grab", help="frames of the edited MP4 at full size (--at SECONDS...) or a contact sheet")
    sp.add_argument("short")
    sp.add_argument("--preset", required=True, choices=["preview", "final"])
    which = sp.add_mutually_exclusive_group(required=True)
    which.add_argument("--at", type=float, nargs="+", metavar="SECONDS")
    which.add_argument("--sheet", action="store_true", help="evenly spaced frames, a quarter size, in one image")
    sp.add_argument("--count", type=int, default=12, help="frames on the contact sheet")
    sp.add_argument("--columns", type=int, default=4, help="contact sheet columns")
    sp.set_defaults(fn=cmd_grab)

    sp = sub.add_parser("extremes", help="full-size stills of each hero at the min/max of each animated channel")
    sp.add_argument("short")
    sp.add_argument("--shot")
    sp.add_argument("--no-wait", action="store_true", help="fail at once when another command holds the GPU lock")
    sp.set_defaults(fn=cmd_extremes)

    sp = sub.add_parser("preview", help="after an edit: build, mark build done, mix, check, render, frame-check, edit")
    sp.add_argument("short")
    sp.add_argument("--rebuild", action="store_true", help="run build.py even when the .blend looks current")
    sp.add_argument("--no-build", action="store_true", help="MCP mode: never run build.py (rebuild through the MCP)")
    sp.add_argument("--every", type=int, default=5, help="check every Nth frame (as BLENDER check)")
    sp.add_argument("--no-wait", action="store_true", help="fail at once when another command holds the GPU lock")
    sp.set_defaults(fn=cmd_preview)

    sp = sub.add_parser("closing", help="make the closing card (stills/closing.png) and say what the short ends on")
    sp.add_argument("short")
    sp.set_defaults(fn=cmd_closing)

    sp = sub.add_parser("probe", help="size, fps, frames and audio of an MP4")
    sp.add_argument("mp4")
    sp.set_defaults(fn=cmd_probe)
    return p


def _one_line(text) -> str:
    return messages.one_line(text, ERROR_LIMIT)


def _warn_stale_build(args, out: dict) -> None:
    """Every command on a short says so (in its result and on one stderr line) when build.py is newer than the .blend.
    check and frame-check already count their findings under "warnings", so theirs go under "build_warnings"."""
    short = getattr(args, "short", None)
    if short is None or not freshness.build_changed(_short_dir(short)):
        return
    key = "build_warnings" if isinstance(out.get("warnings"), int) else "warnings"
    out.setdefault(key, []).append(freshness.BUILD_CHANGED)
    print(f"warning: {freshness.BUILD_CHANGED}", file=sys.stderr, flush=True)


def _emit(obj: dict) -> None:
    print(RESULT_PREFIX + json.dumps(obj, default=str), flush=True)


def main(argv: list[str] | None = None) -> int:
    try:
        args = build_parser().parse_args(argv)
    except SystemExit as exc:  # --help (0) or a usage error, which argparse printed on stderr
        if exc.code:
            _emit({"ok": False, "error": "invalid command line (usage on stderr)"})
        return int(exc.code or 0)
    status = _say_starting(args)
    try:
        if args.command in GPU_COMMANDS - {"render"}:  # render takes it itself, so the preview chain's render does
            sdir = _short_dir(args.short)
            with _gpu(args, sdir, sdir.name, args.command):
                out = args.fn(args)
        else:
            out = args.fn(args)
        _warn_stale_build(args, out)
    except Exception as exc:  # noqa: BLE001 - every failure becomes one plain line for Claude to relay
        plain = isinstance(exc, (freshness.StaleError, CliError))
        message = _one_line(exc if plain else f"{type(exc).__name__}: {exc}")
        _settle_starting(status, message)
        _fail(message, getattr(exc, "payload", None))
        return 1
    except BaseException:  # interrupted before its run started: the "starting" status must not stay running
        _settle_starting(status, "stopped")
        raise
    _settle_starting(status, None)
    _emit(out)
    return 0


RECORDING = {"render", "check", "frame-check", "edit", "preview"}  # the commands that keep a live status
GPU_COMMANDS = {"render", "extremes", "estimate"}  # one at a time per workspace (bsa.gpulock); preview via render


@contextmanager
def _gpu(args, sdir: Path, short: str, command: str, preset: str | None = None):
    """Hold the workspace's GPU lock (<workspace>/.gpu.lock) for a heavy command: wait for another session's job,
    saying so on stderr and in the live status (render-status and its page), or with --no-wait fail at once."""
    from bsa import gpulock
    from bsa import render_status as rs

    status = rs.status_path(sdir, preset) if preset else None
    said = []

    def page(doc: dict) -> None:
        from bsa.review import pages

        pages.render_status_page(rs.describe(doc), rs.page_path(sdir, preset))

    def on_wait(text: str) -> None:
        if not said:
            print(f"{short} {command} is {text}", file=sys.stderr, flush=True)
        said.append(text)
        if status is not None:
            rs.note_waiting(status, text, render_page=page)

    try:
        ctx = gpulock.held(gpulock.lock_path(sdir.parent), short=short, command=command,
                           wait=not getattr(args, "no_wait", False), on_wait=on_wait)  # fmt: skip
        ctx.__enter__()
    except gpulock.GpuBusy as exc:
        raise CliError(str(exc)) from exc
    if said and status is not None:
        rs.note_waiting(status, None, render_page=page)
    try:
        yield
    finally:
        ctx.__exit__(None, None, None)


def _say_starting(args):
    """For a command that records a live status: write "starting" (this process, the command) to it at once,
    before the command reads anything or opens the .blend, so the last run is never shown as current meanwhile.
    Returns (status path, page writer), or None. A status problem never stops the command."""
    if args.command not in RECORDING:
        return None
    try:
        from bsa import render_status as rs

        sdir = _short_dir(args.short)
        preset = getattr(args, "preset", None) or "preview"
        try:
            s = _load(sdir)
            short = {"slug": s.slug, "title": s.title, "dir": str(sdir)}
        except Exception:  # noqa: BLE001 - the command itself reports a bad short.json
            short = {"slug": sdir.name, "title": sdir.name, "dir": str(sdir)}
        page = rs.page_path(sdir, preset)

        def render_page(status, page=page):  # imported here: the status file is written before the page code loads
            from bsa.review import pages

            pages.render_status_page(rs.describe(status), page)

        path = rs.status_path(sdir, preset)
        if rs.starting(path, short=short, preset=preset, kind=args.command, render_page=render_page):
            return path, render_page
    except Exception as exc:  # noqa: BLE001 - status only
        print(f"status: {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
    return None


def _settle_starting(status, error: str | None) -> None:
    if status is None:
        return
    try:
        from bsa import render_status as rs

        rs.settle_starting(status[0], error, render_page=status[1])
    except Exception as exc:  # noqa: BLE001 - status only
        print(f"status: {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)


def _fail(message: str, payload: dict | None = None) -> None:
    """A failure still prints its BSA_RESULT line (ok false, the error, and any payload), so a chain of commands
    that reads only those lines never ends silently; and the one plain line on stderr."""
    _emit({**(payload or {}), "ok": False, "error": message})
    print(message, file=sys.stderr, flush=True)


def _blender_argv() -> list[str]:
    return sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []


if __name__ == "__main__":
    code = main(_blender_argv())
    if code:
        sys.exit(code)
