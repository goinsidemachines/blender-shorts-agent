"""blender-shorts-agent commands that run inside Blender.

    blender -b [BLEND] --factory-startup --python-exit-code 1 --python bsa_blender.py -- CMD ...

Blender prints its own log lines, so the result is one line on stdout starting with `BSA_RESULT ` followed by JSON.
On failure the exit code is non-zero and one plain line on stderr says why. When no .blend was given (the usual way),
the commands that need the scenes open the short's `blend/short.blend` themselves, after the ones that keep a live
status have marked their run as starting there.
"""

from __future__ import annotations

import argparse
import glob
import json
import re
import sys
import time
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


def _short_key(sdir: Path) -> str:
    """What the user types for this short in a command a message suggests (workspace.short_key)."""
    return workspace.short_key(sdir, settings.load_settings().get("workspace"))


def _load(sdir: Path):
    from bsa.short import load_short

    path = sdir / "source" / "short.json"
    if not path.is_file():
        raise CliError(f"{path} does not exist yet")
    return load_short(path)


def _timeline(sdir: Path, short):
    from bsa.narrate.run import takes_durations, takes_onsets
    from bsa.timeline import build_timeline

    durations = takes_durations(sdir, short)
    tl = build_timeline(short, durations, settings.has_end_card(settings.load_settings(), short),
                        onsets=takes_onsets(sdir, short) if durations is not None else None)  # fmt: skip
    return tl, freshness.check_timeline(sdir, tl, _short_key(sdir))  # (timeline, hash): must match timeline.json


def _engine(sdir: Path) -> str | None:
    """The engine approved at the engine stage, else short.json's render.engine, else the saved preference, else
    None (the scene's own)."""
    from bsa import engine_runs

    info = jobstate.load_state(sdir)["stages"].get("engine") or {}
    setting = (settings.load_settings().get("render") or {}).get("engine")
    return engine_runs.pick(info.get("engine"), engine_runs.short_engine(sdir), setting)[0]


def _engine_run(sdir: Path, args):
    """The run `--engine` / `--tag` ask for (bsa.engine_runs): the approved run, or a side run in its own folders."""
    from bsa import engine_runs

    try:
        return engine_runs.resolve(_engine(sdir), getattr(args, "engine", None), getattr(args, "tag", None))
    except ValueError as exc:
        raise CliError(str(exc)) from exc


def _status_key(sdir: Path, args, preset: str) -> str:
    """The name this run's live status, page and thumbnails go under (bsa.engine_runs.status_key): the preset for
    the approved run, <preset>-<tag> for a side run (--engine / --tag), which never touches the approved run's."""
    from bsa import engine_runs

    return engine_runs.status_key(preset, _engine_run(sdir, args))


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


def _mp4(sdir: Path, short, preset: str, run=None) -> Path:
    """The video of `preset`: the approved run's, or a side run's own (`run`, bsa.engine_runs)."""
    from bsa import engine_runs

    return engine_runs.mp4_path(sdir, short.slug, preset, run or engine_runs.EngineRun(None))


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
    from bsa import render_status as rs

    rs.write_atomic(path, json.dumps(data, indent=2) + "\n")  # the page server may read it as it is written
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
    from bsa.overlays import caption_size

    rule_key = rules.scene_key(_hard_rules(short), short)
    size = caption_size(short)
    return {s.id: freshness.shot_check_inputs((fps or {}).get(s.id), spans[s.id], asdict(s), ids,
                                              (parts or {}).get(s.id), rule_key, size) for s in short.shots}  # fmt: skip


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
    from bsa import kit as kit_mod

    brand = settings.brand_for(s, short)
    k = kit_mod.kit_for(s, short) if not cfg["image"] else None  # the user's own image is never restyled
    closing_card.make_card(cfg, brand, png, short.style.get("font") or brand.get("font"), kit=k,
                           card_dir=sdir / "cards")  # fmt: skip
    logo_note = kit_mod.logo_missing(k.logo) if k is not None else None  # drawn without its badge
    if logo_note:
        warnings.append(logo_note)
    warnings += closing.zone_warnings(read_png(png))
    made = {"kit": True} if k is not None else {}
    info.write_text(json.dumps({**cfg, **made, "warnings": warnings}, indent=2, ensure_ascii=False), encoding="utf-8")
    return cfg, png, warnings


def _guide_kit(sdir: Path, short) -> tuple:
    """(the kit the short's edit draws, its card font) or (None, None) without one, as the checks get them."""
    from bsa import cards, kit
    from bsa.fonts import font_caches

    k = kit.kit_for(settings.load_settings(), short)
    return (k, cards.card_font(k, font_caches(sdir))) if k is not None else (None, None)


def _guides(frame: Path, short, shot, kf: tuple) -> Path:
    """`<stem>-guides.png` beside `frame`: a copy with the caption zone, the top cards (with a kit) and the framing
    band outlined (bsa.guides). The frame itself is left as it is."""
    from bsa import guides
    from bsa.blender import pixels

    boxes = guides.rects(short, shot, *kf)
    return guides.write_png(guides.guides_path(frame), guides.draw(pixels.read_png(frame), boxes))


def _no_guides(frame: Path) -> None:
    """Remove an older run's guides copy of `frame` (the frame was just rendered again, without --guides)."""
    from bsa import guides

    guides.guides_path(frame).unlink(missing_ok=True)


# --------------------------------------------------------------------------- commands


def cmd_stills(args) -> dict:
    from bsa.blender.stills import render_still, world_mask

    sdir = _short_dir(args.short)
    short = _load(sdir)
    if args.out and not args.shot:
        raise CliError("--out needs --shot (one still goes to one file)")
    tl, tl_hash = _timeline(sdir, short)
    spans = {s.id: s for s in tl.shots}
    data = _open_blend(sdir)
    engine = _engine(sdir)
    written, drawn = [], []
    kf = _guide_kit(sdir, short) if args.guides else None
    from bsa.colour import background_source, flat_backdrop

    wanted = background_source(short, settings.load_settings())  # no intended background: no mask pass to measure
    for shot in _shots(short, args.shot):
        shot, scene = _scene(data, short, shot.id, tl_hash)
        out = Path(args.out) if args.out else sdir / "stills" / f"{shot.id}.jpg"
        frame = _still_frame(shot, spans[shot.id])
        render_still(scene, frame, out, engine=engine)
        flat = flat_backdrop(scene.world, scene.render.film_transparent)
        mask = None
        if flat and wanted:
            try:
                mask = world_mask(scene, frame, _still_shape(out))
            except Exception:  # noqa: BLE001 - a failed measuring pass never stops the stills: measure the whole still
                mask = None
        written.append((shot.id, out, flat, mask))
        if args.guides:
            drawn.append(_guides(out, short, shot, kf))
        else:
            _no_guides(out)
    result = {"stills": [str(p) for _, p, _, _ in written]}
    if args.guides:
        result["guides"] = [str(p) for p in drawn]
    warnings = _background_warnings(short, written)
    if not args.shot:  # the storyboard shows the closing card too
        _, card, card_warnings = _closing_card(sdir, short)
        if card is not None:
            result["closing"] = str(card)
        warnings += card_warnings
    return {**result, "warnings": warnings} if warnings else result


def _still_shape(path: Path) -> tuple[int, int]:
    """A rendered still's (height, width), from its image header."""
    import bpy

    img = bpy.data.images.load(str(path), check_existing=False)
    try:
        w, h = img.size
    finally:
        bpy.data.images.remove(img)
    return h, w


def _background_warnings(short, stills) -> list[str]:
    """Each still whose background (its most common colour) is far from the intended one, when one is set: the
    short's style.palette.background, else the brand's (unless the short has its own look, "look": "fresh").
    `stills` are (shot id, path, flat, mask): only a still whose scene has a flat backdrop (paper_world or a plain
    colour, bsa.colour.flat_backdrop) is checked. A sky, an HDRI or a landscape is not a paper backdrop. `mask`
    (bsa.blender.stills.world_mask, or None) is where the world shows: only there is measured, and a still that is
    mostly its subject (world under bsa.colour.WORLD_MIN_SHARE) is not judged."""
    from bsa.colour import background_source, background_warning

    found = background_source(short, settings.load_settings())
    flat = [(shot_id, path, mask) for shot_id, path, is_flat, mask in stills if is_flat]
    if not found or not flat:
        return []
    want, whose = found
    from bsa.blender.pixels import read_png

    out = []
    for shot_id, path, mask in flat:
        msg = background_warning(read_png(path), want, f"shot {shot_id}", whose=whose, mask=mask)
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
    """Project every near label's object (its "at" point, if it has one) for its shot and write source/anchors.json,
    each under the label's anchor key (bsa.short.label_anchor), stamped with each shot's fingerprint and the timing;
    shots unchanged since the last anchors keep theirs. Also the screen boxes of the heroes of every shot with a
    near label (labels keep off them) and of the last shot, with the last shot's other meshes during the end card
    ("scenery": the end card keeps off them after the heroes). Each near label's object records its "category" tag
    (bsa.blender.bake.category, or None), read again on every run. Returns its path, how many labels it places and
    which shots it projected again."""
    from bsa.blender.anchors import hero_boxes, project, scenery_boxes
    from bsa.blender.anchors import heroes as hero_names
    from bsa.blender.bake import category
    from bsa.short import near_anchors
    from bsa.timeline import end_card_start

    _check_near_objects(data, short, tl_hash)
    if fps is None:
        fps = _fingerprints(sdir, short, tl, tl_hash, data)
    reuse = freshness.reusable_anchors(sdir, fps, tl_hash)
    spans = {s.id: s for s in tl.shots}
    shots: dict[str, dict] = {}
    projected: set[str] = set()
    for shot_id, _text, name, key, at in near_anchors(short):
        if key in shots.get(shot_id, {}):
            continue
        _, scene = _scene(data, short, shot_id, tl_hash)
        prior = reuse["shots"].get(shot_id) or {}
        if isinstance(prior.get(key), dict):
            entry = dict(prior[key])
        else:
            projected.add(shot_id)
            try:
                entry = project(scene, [name], spans[shot_id].f0, spans[shot_id].f1, at)[name]
            except ValueError as exc:
                raise CliError(str(exc)) from exc
        entry["category"] = category(scene.objects.get(name))  # read fresh every run: a retag needs no projection
        shots.setdefault(shot_id, {})[key] = entry
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


def _redo_targets(sdir: Path, args, short, spans, run=None) -> dict[str, list[int]] | None:
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
    from bsa import engine_runs

    run = run or engine_runs.EngineRun(None)
    path = engine_runs.frame_checks_path(sdir, args.preset, run)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise CliError(f"--redo-flagged reads {path.name}, which is missing or unreadable: run BLENDER frame-check "
                       f"SHORT --preset {args.preset}{run.flags()} first") from exc  # fmt: skip
    out: dict[str, set[int]] = {}
    for f in data.get("findings") or []:
        sid = f.get("shot") if isinstance(f, dict) else None
        if sid in spans and (args.shot is None or sid == args.shot):
            span = spans[sid]
            out.setdefault(sid, set()).update(n for n in f.get("redo_frames") or [] if span.f0 <= n <= span.f1)
    return {sid: sorted(frames) for sid, frames in out.items() if frames}


def _recording(sdir: Path, short, preset: str, step: str, key: str | None = None):
    """The live status this step records itself in (review/render-status-<key>.json; `key` defaults to
    the preset, a side run's is _status_key's): inside the preview chain the chain's own step, on its own a one-step
    run that supersedes the last run shown there."""
    from bsa import render_status as rs

    key = key or preset
    return rs.recording(rs.status_path(sdir, key), short={"slug": short.slug, "title": short.title, "dir": str(sdir)},
                        preset=preset, step=step)  # fmt: skip


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
    key = _status_key(sdir, args, args.preset)
    with _recording(sdir, short, args.preset, "render", key) as run, _gpu(args, sdir, short.slug, "render", key):
        out = _cmd_render(args, sdir, short, run)
        run.summarize("render", _render_summary(out))
    return out


def _cmd_render(args, sdir: Path, short, run) -> dict:
    from bsa import engine_runs
    from bsa.blender.render import render_range

    tl, tl_hash = _timeline(sdir, short)
    spans = {s.id: s for s in tl.shots}
    side = _engine_run(sdir, args)  # the approved run, or a side run (--engine / --tag) in its own folder
    root = engine_runs.frames_root(sdir, args.preset, side)
    redo = _redo_targets(sdir, args, short, spans, side)
    shots = _shots(short, args.shot) if redo is None else [s for s in _shots(short, args.shot) if s.id in redo]
    if redo is not None and not shots:
        return {"preset": args.preset, "shots": [], "redo": {},
                "note": f"no flagged frames in review/frame-checks-{args.preset}.json: nothing to render again"}
    data = _open_blend(sdir)
    engine = side.engine
    fps = _fingerprints(sdir, short, tl, tl_hash, data)
    parts = _parts(sdir, fps)
    check_keys = _check_keys(short, tl, fps, parts)
    if args.preset == "final":  # the final render needs checks that passed on every shot as it is now
        freshness.require_checks(sdir, tl_hash, check_keys, "the final render", key=_short_key(sdir))
        failed = None
    else:  # the preview renders anyway; it says what the checks found on this build (the final refuses on it)
        failed = _failed_checks_warning(sdir, short, tl_hash, check_keys)
        if failed:
            print(f"warning: {failed}", file=sys.stderr, flush=True)
    _check_near_objects(data, short, tl_hash)  # before any frame: anchors run after the render
    stamps = {s.id: freshness.frames_stamp(spans[s.id], engine, (fps or {}).get(s.id), args.preset, parts.get(s.id))
              for s in shots}  # fmt: skip
    damaged = _drop_damaged(root, shots, spans, stamps, redo)  # before the plan counts what is kept
    key = engine_runs.status_key(args.preset, side)  # a side run's status, page and thumbnails are its own
    tracker, watch, plan = _render_watch(sdir, short, args.preset, engine, shots, spans, stamps, fps, tl_hash, redo,
                                         run=run, root=root, key=key)  # fmt: skip
    done, warnings = [], [*([failed] if failed else []), *_damaged_warnings(damaged)]
    with watch:
        tracker.start()
        try:
            for shot in shots:
                shot, scene = _scene(data, short, shot.id, tl_hash)
                span = spans[shot.id]
                folder = root / shot.id
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
                if shot.id in damaged:
                    done[-1]["damaged"] = freshness.frames_spec(damaged[shot.id])
            tracker.set_phase("anchors")
            anchors = _write_anchors(sdir, short, tl, tl_hash, data, fps)  # the edit places near labels from these
        except BaseException as exc:
            stopped = watch.cancelled or isinstance(exc, KeyboardInterrupt)
            tracker.finish("stopped" if stopped else "failed", error=_one_line(f"{exc}") or type(exc).__name__)
            raise
        tracker.finish("done")
    from bsa import render_status as rs

    measured = tracker.snapshot()  # the seconds per frame this run measured, for the engine comparison
    engine_runs.record_render(sdir, args.preset, side, frames=len(tracker.durations), seconds=tracker.render_s)
    out = {"preset": args.preset, "engine": engine, "shots": done, "anchors": anchors,
           "status": str(rs.status_path(sdir, key)),
           "s_per_frame": measured.get("avg_s_per_frame"), "engines": engine_runs.compare(sdir, args.preset)}  # fmt: skip
    if side.tag:
        out.update(tag=side.tag, frames_dir=str(root))
    if redo is not None:
        out["redo"] = {sid: freshness.frames_spec(frames) for sid, frames in redo.items()}
    if warnings:
        out["warnings"] = warnings
    return out


def _failed_checks_warning(sdir: Path, short, tl_hash: str, keys: dict, most: int = 3) -> str | None:
    """One line when review/checks.json found errors on this very build and timing (freshness.failed_checks): how
    many, the first `most` of them (overlaps grouped by name family, as overlap_summary does, then the others), and
    that the final render refuses until they are fixed. None when there are none, or the checks are not current."""
    from bsa import findings

    n = freshness.failed_checks(sdir, tl_hash, keys)
    if not n:
        return None
    data = freshness.read_checks(sdir) or {}
    rows = [r for r in [*(data.get("findings") or []), *(data.get("held_back") or [])]
            if isinstance(r, dict) and r.get("severity") == "error" and "capped" not in r]  # fmt: skip
    items = findings.family_lines([r for r in rows if findings.pair_of(r)], [s.id for s in short.shots])
    for r in rows:
        if not findings.pair_of(r):
            text = _one_line(f"{r.get('check')}: {r.get('message')}")
            items.append(text if len(text) <= 160 else text[:157] + "...")
    shown = items[:most] + ([f"and {len(items) - most} more"] if len(items) > most else [])
    what = f" ({'; '.join(shown)})" if shown else ""
    return (f"review/checks.json has {n} error(s) on this build{what}: the final render will refuse until these are "
            f"fixed and checked again (BLENDER check {_short_key(sdir)})")  # fmt: skip


def _drop_damaged(root: Path, shots, spans, stamps, redo) -> dict:
    """{shot id: frames} of kept frames that are not complete PNGs: a crash or power cut mid-render leaves frames cut
    off or zero-filled, which a resume would otherwise keep as finished. They are deleted so the render makes them
    again; in a redo they join the frames it redoes (which deletes them). Empty files are the render's own
    placeholders, and a shot whose frames are stale renders whole anyway: neither is read."""
    from bsa.image_checks import png_problem

    out = {}
    for shot in shots:
        span, folder = spans[shot.id], root / shot.id
        if redo is None and freshness.frames_reason(freshness.read_frames_stamp(folder), stamps[shot.id]) is not None:
            continue
        bad = []
        for f in range(span.f0, span.f1 + 1):
            png = folder / f"{f:04d}.png"
            try:
                if png.stat().st_size > 0 and png_problem(png):
                    bad.append(f)
            except FileNotFoundError:
                pass
        if not bad:
            continue
        out[shot.id] = bad
        if redo is not None:
            redo[shot.id] = sorted(set(redo.get(shot.id) or []) | set(bad))
        else:
            for f in bad:
                (folder / f"{f:04d}.png").unlink(missing_ok=True)
    return out


def _damaged_warnings(damaged: dict) -> list[str]:
    return [f"shot {sid}: frames {freshness.frames_spec(frames)} were not complete PNGs (an interrupted render left "
            "them): deleted and rendered again" for sid, frames in damaged.items()]  # fmt: skip


def _render_watch(sdir: Path, short, preset: str, engine, shots, spans, stamps, fps, tl_hash: str, redo=None, run=None,
                  root: Path | None = None, key: str | None = None):  # fmt: skip
    """The live status of this render (review/render-status-<key>.json and the thumbnails
    review/render-status/<key>-<shot>.jpg, which the page's Render stage shows), the render handlers that keep it current, and the plan per
    shot ({shot id: total, reused, stale, reason}). `root` is the frames folder (default frames/<preset>; a side
    run's own, bsa.engine_runs); `key` the name the status goes under (default the preset; a side run's own,
    bsa.engine_runs.status_key)."""
    from bsa import render_status as rs
    from bsa.blender.render_watch import Watch

    root = root or sdir / "frames" / preset
    plan = {}
    for shot in shots:
        span = spans[shot.id]
        folder = root / shot.id
        p = rs.frames_plan(folder, span.f0, span.f1, stamps[shot.id])
        if redo is not None:
            total = span.f1 - span.f0 + 1
            on_disk = sum(1 for f in range(span.f0, span.f1 + 1) if f not in redo[shot.id]
                          and (folder / f"{f:04d}.png").is_file())  # fmt: skip
            p = {"total": total, "reused": on_disk, "stale": 0,
                 "reason": f"redoing frames {freshness.frames_spec(redo[shot.id])}"}  # fmt: skip
        plan[shot.id] = p
    key = key or preset
    tracker = rs.Tracker(
        rs.status_path(sdir, key),
        short={"slug": short.slug, "title": short.title, "dir": str(sdir)},
        preset=preset,
        engine=engine,
        shots=[{"id": s.id, "f0": spans[s.id].f0, "f1": spans[s.id].f1, **plan[s.id]} for s in shots],
        stamp={"timeline": tl_hash, "fingerprints": {s.id: (fps or {}).get(s.id) for s in shots}},
        seed_s_per_frame=rs.seed_from_estimate(sdir, engine, preset),
        run=run,
        key=key,
    )
    return tracker, Watch(tracker, sdir, key), plan


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
    preset = getattr(args, "preset", None) or "preview"
    with _recording(sdir, short, preset, "check", _status_key(sdir, args, preset)) as run:
        out = _cmd_check(args, sdir, short, run)
        run.summarize("check", {"warnings": out.get("warnings"), "checked": out.get("checked"), "kept": out.get("kept")})
    return out


def _cap_overlap_rows(rows: list[dict], most: int) -> tuple[list[dict], list[dict]]:
    """(the rows with every finding of the first `most` overlapping pairs over every shot, and one more row saying
    how many pairs were not listed and in which shots; the rows of those pairs, kept apart so a later check that
    keeps their shots still has them)."""
    from bsa import findings

    listed, held, more = findings.cap_pairs(rows, most)
    if not more:
        return rows, []
    shots = sorted({s for r in held for s in r.get("shots") or [r.get("shot")] if s})
    summary = {**asdict(findings.not_listed(more)), findings.CAPPED: more, "shots": shots,
               "shot": shots[0] if shots else None, "scene": held[0].get("scene")}  # fmt: skip
    return [*listed, summary], held


def _write_checks(path: Path, rows: list[dict], stamp: dict | None, shot_order: list[str], most: int, **extra) -> dict:
    """The check rows into review/checks.json: the first `most` overlapping pairs listed, the rest held back
    (_cap_overlap_rows), and every pair, listed or held back, under "overlap_summary" grouped by name family
    (bsa.findings.family_lines; no key when no pair overlaps)."""
    from bsa import findings

    summary = findings.family_lines(rows, shot_order)
    listed, held = _cap_overlap_rows(rows, most)
    return _write_findings(path, listed, stamp, **extra, held_back=held,
                           **({"overlap_summary": summary} if summary else {}))  # fmt: skip


def _cmd_check(args, sdir: Path, short, run) -> dict:
    from bsa import findings
    from bsa.blender.camera import framing_findings
    from bsa.blender.checks import OVERLAP_MAX_FINDINGS, run_checks

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
    to_check = sum(shot.id not in reuse for shot, _ in plan)
    if reuse:
        kept_ids = [shot.id for shot, _ in plan if shot.id in reuse]
        print(f"keeping {len(kept_ids)} shot(s) unchanged since the last checks: {', '.join(kept_ids)}",
              file=sys.stderr, flush=True)  # fmt: skip
    line = None
    hard_rules = _hard_rules(short)  # scene names a rule may forbid are warnings (bsa.rules.name_hits)
    for (shot, span), item in zip(plan, progress, strict=True):
        if shot.id in reuse:
            rows += [{**f, "shots": [shot.id]} for f in reuse[shot.id]]
            kept.append(shot.id)
            continue
        _, scene = _scene(data, short, shot.id, tl_hash)
        item["state"] = "checking"
        errors = sum(r.get("severity") == "error" for r in rows)
        line = findings.progress_line(shot.id, len(checked) + 1, to_check, errors)
        print(line, file=sys.stderr, flush=True)
        where = f"{line} \u00b7 scene {scene.name}"
        run.progress(counted[0], total, current=line, force=True)

        def on_frame(_sid, done, _n, item=item, where=where):
            item["done"] = done
            run.progress(counted[0] + done, total, current=where)

        found = run_checks(scene, replace(tl, shots=[span]), sample_every=args.every, short=short,
                           progress=_progress_cb(on_frame), rules=hard_rules, cap=False)  # fmt: skip
        found += framing_findings(scene, shot.id, span.f0, span.f1)  # the build's framing warnings
        item["state"], item["done"] = "done", item["total"]
        counted[0] += item["total"]
        rows += [_check_row(f, shot.id, scene.name) for f in found]
        checked.append(shot.id)
    run.progress(counted[0], total, current=line if checked else None, force=True)  # the last shot it checked
    out = _write_checks(sdir / "review" / "checks.json", _merge_findings(rows), stamp, [s.id for s in short.shots],
                        OVERLAP_MAX_FINDINGS, checked=checked, kept=kept)  # fmt: skip
    if out["errors"]:
        raise CliError(f"{out['errors']} check error(s); see review/checks.json and fix them before rendering", out)
    jobstate.mark_done(sdir, "checks", warnings=out["warnings"])
    return out


def _rendered(sdir: Path, short, preset: str, tl, fps: dict | None, run=None) -> list:
    """(shot, frame paths) for every shot: exactly its current frames, rendered for this timing, engine and shot
    content (fingerprint). `run` (bsa.engine_runs) reads a side run's own frames, of its own engine."""
    from bsa import engine_runs

    spans = {s.id: s for s in tl.shots}
    run = run or engine_runs.EngineRun(_engine(sdir))
    engine = run.engine
    folder = engine_runs.frames_root(sdir, preset, run)
    parts = _parts(sdir, fps)
    return [(shot, freshness.rendered_frames(folder / shot.id, spans[shot.id], engine, (fps or {}).get(shot.id),
                                             preset, parts.get(shot.id))) for shot in short.shots]  # fmt: skip


REDO_AROUND = {"flicker": 1, "stuck_frame": 1}  # frames either side a redo takes too (flicker: t-1..t+1)


def _redo(sdir: Path, preset: str, finding, span, flags: str = "") -> dict:
    """The frames to render again for one frame finding, and the command that does it (`flags`: a side run's
    --engine / --tag)."""
    if finding.frame is None:
        return {}
    k = REDO_AROUND.get(finding.check, 0)
    lo = finding.frame - k
    hi = finding.frame + (k if finding.check == "flicker" else 0)  # a stuck frame: it and the one it repeats
    frames = list(range(max(span.f0, lo), min(span.f1, hi) + 1))
    spec = freshness.frames_spec(frames)
    return {"redo_frames": frames,
            "redo": f'BLENDER render "{sdir}" --preset {preset} --shot {span.id} --frames {spec}{flags}'}  # fmt: skip


def cmd_frame_check(args) -> dict:
    from bsa.blender.pixels import read_png
    from bsa.image_checks import frame_checks

    sdir = _short_dir(args.short)
    short = _load(sdir)
    with _recording(sdir, short, args.preset, "frame-check", _status_key(sdir, args, args.preset)) as run:
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


def _shading_rows(sdir: Path, short, tl, tl_hash: str, shots, load) -> list[dict]:
    """Shading-gradient warnings (bsa.blender.shading) on each shot's first, middle and last rendered frame: a large
    flat face of a hero that shades strongly from one side to the other. `shots`: (shot, its readable frames). It
    only adds warnings: a shot it can't read is skipped with a note (the others are still checked), and [] when the
    scenes can't be read at all."""
    if not _blend(sdir).is_file():
        return []
    try:
        from bsa.blender.shading import frame_shading

        data = _open_blend(sdir)
    except Exception as exc:  # noqa: BLE001 - the shading check only adds warnings
        print(f"frame-check: shading not checked ({exc})", file=sys.stderr)
        return []
    rows = []
    for shot, frames in shots:
        if not frames:
            continue
        try:
            picked = sorted({frames[0], frames[len(frames) // 2], frames[-1]})
            numbered = [(int(p.stem), p) for p in picked if p.stem.isdigit()]
            scene = _scene(data, short, shot.id, tl_hash)[1]
            rows += [{**asdict(f), "shot": shot.id} for f in frame_shading(scene, numbered, shot.id, load)]
        except Exception as exc:  # noqa: BLE001 - one shot's failure must not drop the others' warnings
            print(f"frame-check: shading not checked for shot {shot.id} ({exc})", file=sys.stderr)
    return rows


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


def _unreadable(frames: list[Path]) -> dict:
    """{frame path: an "unreadable_frame" error} for each frame that is not a complete PNG (an interrupted render
    leaves frames cut off, zero-filled or empty)."""
    from bsa.image_checks import Finding, png_problem

    out = {}
    for p in frames:
        why = png_problem(p)
        if why:
            out[p] = Finding("unreadable_frame", "error", None, int(p.stem), f"{p.name} is not a complete PNG ({why}); "
                             "an interrupted render leaves frames like this: render it again")  # fmt: skip
    return out


def _cmd_frame_check(args, sdir: Path, short, run, frame_checks, read_png) -> dict:
    from bsa import engine_runs
    from bsa.timeline import held_frames

    tl, tl_hash = _timeline(sdir, short)
    spans = {s.id: s for s in tl.shots}
    fps = _fingerprints(sdir, short, tl, tl_hash)
    motion = _motion(sdir, short, tl, tl_hash)
    rows = []
    side = _engine_run(sdir, args)  # a side run (--engine / --tag) keeps its own findings and cache
    shots = _rendered(sdir, short, args.preset, tl, fps, side)
    total = sum(len(frames) for _, frames in shots)
    items = [{"id": shot.id, "total": len(frames), "done": 0, "state": "pending"} for shot, frames in shots]
    run.progress(0, total, items=items, force=True)
    cache_path = engine_runs.frame_check_cache_path(sdir, args.preset, side)
    cache, code = _load_fc_cache(cache_path), _fc_code()
    kept = {}
    readable_shots = []  # (shot, its frames that are complete PNGs): the shading check reads only those
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

            bad = _unreadable(frames)  # never decoded: a loader may read half a frame as a whole one
            readable = [p for p in frames if p not in bad]
            found = list(bad.values())
            if readable:
                found += frame_checks(readable, load=read_png, progress=_progress_cb(on_frame), moving=moving,
                                      held=held, workers=workers)  # Blender's image loader, in threads where it can  # fmt: skip
            item["state"] = "done"
        damaged = {f.frame for f in found if f.check == "unreadable_frame"}  # checked now or kept from the cache
        readable_shots.append((shot, [p for p in frames if not (p.stem.isdigit() and int(p.stem) in damaged)]))
        kept[shot.id] = {"key": key, "findings": [asdict(f) for f in found]}
        for f in found:
            rows.append({**asdict(f), "shot": shot.id, **_redo(sdir, args.preset, f, spans[shot.id], side.flags())})
        before += len(frames)
    _save_fc_cache(cache_path, kept)
    rows += _shading_rows(sdir, short, tl, tl_hash, readable_shots, read_png)
    run.progress(before, total, force=True)
    rows.sort(key=lambda f: f["severity"] != "error")
    flagged = sum(1 for f in rows if f.get("redo_frames"))
    extra = ({"redo_all": f'BLENDER render "{sdir}" --preset {args.preset} --redo-flagged{side.flags()}'}
             if flagged else {})  # fmt: skip
    path = engine_runs.frame_checks_path(sdir, args.preset, side)
    out = _write_findings(path, rows, **extra)
    if out["errors"]:
        raise CliError(f"{out['errors']} frame error(s); see review/{path.name}", out)
    return out


def cmd_edit(args) -> dict:
    from bsa.blender import edit

    sdir = _short_dir(args.short)
    if args.preset == "final":
        freshness.require_current_build(sdir, "the final edit")
    short = _load(sdir)
    with _recording(sdir, short, args.preset, "edit", _status_key(sdir, args, args.preset)) as run:
        out = _cmd_edit(args, sdir, short, run, edit)
        run.summarize("edit", {"mp4": out.get("mp4"), "timeline_hash": out.get("timeline_hash")})
    return out


DROP_WAIT_S = 35.0  # how long the edit waits for a page streaming the outgoing MP4 to let go of it (Windows)


def _while_the_page_reads(fn, mp4: Path):
    """fn(), retried while Windows refuses it with PermissionError because the review page is streaming `mp4` this
    instant (each of its range answers holds the file only a moment). After DROP_WAIT_S, a CliError."""
    deadline = time.monotonic() + DROP_WAIT_S
    while True:
        try:
            return fn()
        except PermissionError as exc:
            if time.monotonic() >= deadline:
                raise CliError(f"{mp4.name} is still open in another program, most likely the review page playing it "
                               f"({exc.strerror or exc}): close the page tab and run the edit again.") from exc  # fmt: skip
            time.sleep(0.25)


def _drop_outgoing(sdir: Path, out_mp4: Path, preset: str, side) -> None:
    """Clear the way for the new video. The approved final's outgoing MP4 is moved into the page's version history
    (bsa.review.history.keep_outgoing_final) so before and after can play it; any other run's is deleted. Both wait
    for a page streaming the file (_while_the_page_reads)."""
    if preset == "final" and not side.tag:
        from bsa.review import history

        _while_the_page_reads(lambda: history.keep_outgoing_final(sdir, out_mp4), out_mp4)
    else:
        _while_the_page_reads(lambda: out_mp4.unlink(missing_ok=True), out_mp4)


def _cmd_edit(args, sdir: Path, short, run, edit) -> dict:
    from bsa import engine_runs, kit

    s = settings.load_settings()  # read once: the whole edit uses the same settings
    kit_errors = kit.errors(s, short, sdir)  # before anything is touched: the kit can't lay it out
    if kit_errors:
        raise CliError("; ".join(kit_errors))
    side = _engine_run(sdir, args)  # a side run (--engine / --tag) edits its own frames into its own MP4
    out_mp4 = _mp4(sdir, short, args.preset, side)
    tl, tl_hash = _timeline(sdir, short)
    fps = _fingerprints(sdir, short, tl, tl_hash, reset_after=True)
    _rendered(sdir, short, args.preset, tl, fps, side)  # every shot's exact frames, from this timing, engine, content
    if args.preset == "final":
        freshness.require_checks(sdir, tl_hash, _check_keys(short, tl, fps, _parts(sdir, fps)), "the final edit",
                                 key=_short_key(sdir))
    from bsa.short import near_anchors

    need = [(shot_id, text, key) for shot_id, text, _name, key, _at in near_anchors(short)]
    anchors = freshness.load_anchors(sdir, fps, tl_hash, need) if need else None
    label_heroes = _label_heroes(sdir, fps, tl_hash, need)
    warnings, heroes = [], None
    if settings.has_end_card(s, short):  # the end card is polish: without anchors it is only warned
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
    # every refusal is behind us: only now does the outgoing video go (a refused edit leaves the postable final in
    # out/), and before the edit is built and encoded, so a stale video never passes for this one if that fails
    _drop_outgoing(sdir, out_mp4, args.preset, side)
    scene = edit.build_edit(short, tl, engine_runs.frames_root(sdir, args.preset, side), mix if mix.is_file() else None,
                            out_mp4,
                            s, preset=args.preset, anchors=anchors, hero_boxes=heroes,
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
    if not side.tag:  # a side run is a comparison: the preview / final stage stays the approved run's
        jobstate.mark_done(sdir, args.preset, mp4=str(path), **info)
    engine_runs.record_mp4(sdir, args.preset, side, path, edit=_edit_record(tl_hash, float(info["fps"]), n_frames, end))
    if edit.end_card_note(scene):
        warnings.append(edit.end_card_note(scene))
    warnings += edit.glyph_fallbacks(scene)  # characters the style font lacks: drawn in another font, or boxes
    k = kit.kit_for(s, short)
    logo_note = kit.logo_missing(k.logo) if k is not None else None
    warnings += [logo_note] if logo_note else []  # the logo file is gone: no badge
    warnings = list(dict.fromkeys(warnings))  # the edit's layout notes a missing logo too: say it once
    out = {"mp4": str(path), "timeline_hash": tl_hash, **info, "font": edit.font_used(scene), "font_fallback": edit.font_note(scene)}
    if edit.kit_cards(scene):
        out["cards"] = edit.kit_cards(scene)  # the brand kit's card images placed
    if side.tag:
        out.update(engine=side.engine, tag=side.tag)
    if end:
        out["closing"] = end["hold"]  # its "note" says when the voice line held the card past closing.seconds
    if args.preset == "final" and not side.tag:  # the caption files, cue for cue with this video
        from bsa import captions

        try:
            out["captions"] = captions.write_files(sdir, short, s)
        except Exception as exc:  # noqa: BLE001 - auxiliary output: never lose the recorded MP4 result
            warnings.append(f"caption files not written: {exc}")
    return {**out, "warnings": warnings} if warnings else out


def _edit_record(tl_hash: str, fps: float, n_frames: int, end: dict | None) -> dict:
    """What an edit was cut from, for the review page's markers: the timeline's hash, the video's fps and frames,
    and where the closing card sits (1-based, inclusive), or None when there is none."""
    closing = {"f0": end["f0"], "f1": end["f1"]} if end else None
    return {"timeline_hash": tl_hash, "fps": fps, "frames": n_frames, "closing": closing}


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
    held = closing.hold(cfg, end, tl.fps)
    info = png.with_name(closing.INFO_FILE)  # the final page shows how long the card holds, and why
    data = json.loads(info.read_text(encoding="utf-8"))
    data.update(held_s=held["seconds"], hold_note=held.get("note"))
    info.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    return {**end, "png": str(png), "hold": held}, warnings


def cmd_closing(args) -> dict:
    """Make the short's closing card (stills/closing.png) and say what it will be: for the review pages and setup."""
    sdir = _short_dir(args.short)
    short = _load(sdir)
    cfg, png, warnings = _closing_card(sdir, short)
    out = {"closing": None if cfg is None else {**cfg, "png": str(png)}}
    return {**out, "warnings": warnings} if warnings else out


def _sample_kit(args):
    """The kit a sample command shows: --kit (a brand object as JSON), else the settings' brand."""
    from bsa import kit as kit_mod

    if args.kit is not None:
        try:
            brand = json.loads(args.kit)
        except ValueError as exc:
            raise CliError(f"--kit is not JSON: {exc}") from exc
        if not isinstance(brand, dict):
            raise CliError("--kit must be a JSON object (the brand)")
        try:
            return kit_mod.kit_from(brand)
        except ValueError as exc:
            raise CliError(str(exc)) from exc
    k = kit_mod.kit_for(settings.load_settings())
    if k is None:
        raise CliError("no brand kit is set: brand.style is missing")
    return k


def cmd_kit_sample(args) -> dict:
    """One sample sheet of a brand kit (every card on the kit's background) to --out, before anything is saved: from
    --kit (a brand object as JSON) or the settings' brand. Writes nothing to the settings; no short, no engine."""
    from bsa.blender import cards as blender_cards
    from bsa.kit import cvd_problems

    k = _sample_kit(args)
    out = Path(args.out)
    warnings = list(blender_cards.render_sample(k, out, settings.bsa_home() / "cards"))
    warnings += cvd_problems(k.categories)
    return {"png": str(out), "warnings": warnings}


def _style_engine(args) -> str:
    """The engine the user chose for style-sample: --engine, else the render.engine setting when it is cycles or eevee.
    Never a default: "ask-always", "ask" or nothing means Claude must ask the user."""
    chosen = getattr(args, "engine", None)
    if chosen is None:
        chosen = (settings.load_settings().get("render") or {}).get("engine")
    if chosen not in ("cycles", "eevee"):
        raise CliError("style-sample needs an engine: pass --engine eevee or --engine cycles, or set render.engine "
                       "(the engine is always yours to pick)")  # fmt: skip
    return chosen


def cmd_style_sample(args) -> dict:
    """The demo machine in each style (--styles a,b; default the kit's own) on a set (--set, default studio, "none" =
    no set) tiled left to right with each style's name under it, to --out; each tile is --scale x 1080x1920. The
    engine is the user's (_style_engine). Writes nothing to the settings; no short."""
    from bsa import setplace
    from bsa.kit import STYLES

    engine = _style_engine(args)
    k = _sample_kit(args)
    names = [n.strip().lower() for n in args.styles.split(",") if n.strip()] if args.styles else [k.style]
    if not names:
        raise CliError("--styles needs at least one style name")
    for n in names:
        if n not in STYLES:
            raise CliError(f"{n!r} is not a style: use one of {', '.join(STYLES)}")
    set_name = (args.set or "studio").strip().lower()
    if set_name != "none" and set_name not in setplace.SETS:
        raise CliError(f"{set_name!r} is not a set: use one of {', '.join(setplace.SETS)} or none")
    scale = args.scale if args.scale is not None else 0.5
    if not 0.05 <= scale <= 1:
        raise CliError("--scale must be from 0.05 to 1")
    from bsa.blender import stylesample

    out = Path(args.out)
    seconds, warnings = stylesample.render_sheet(k, names, set_name, engine, scale, out)
    return {"png": str(out), "engine": engine, "styles": names, "set": set_name, "seconds": seconds,
            "warnings": warnings}  # fmt: skip


def _look_engine(args, sdir: Path) -> str:
    """The engine the user chose for the look preview: --engine, else the short's (the approved engine, short.json's
    render.engine, the render.engine setting). Never a default: without one Claude must ask the user."""
    chosen = getattr(args, "engine", None) or _engine(sdir)
    if chosen not in ("cycles", "eevee"):
        raise CliError("look needs an engine: pass --engine eevee or --engine cycles, or set render.engine (the "
                       "engine is always yours to pick: ask the user)")  # fmt: skip
    return chosen


def cmd_look(args) -> dict:
    """The look preview: the first, middle and last shots in the chosen style (--style) and set (--set), each built
    one by default, with the kit's cards on top, the kit sheet, and stills/look.json. Another style or set is put on
    the scenes in memory only: the .blend is never saved, and the file is reverted afterwards. The engine is the
    user's (_look_engine); the time per frame is a measured estimate."""
    import contextlib

    import bpy

    if not bpy.app.background:  # it changes the scenes and reverts the file: never the user's open session
        raise CliError("look runs in a background Blender (BLENDER look ...), never in the open session")
    from bsa import kit as kit_mod
    from bsa import setplace
    from bsa.kit import STYLES

    sdir = _short_dir(args.short)
    short = _load(sdir)
    k = kit_mod.kit_for(settings.load_settings(), short)
    if k is None:
        raise CliError("the look preview needs a brand kit: brand.style is not set")
    style = args.style.strip().lower() if args.style else None
    if style is not None and style not in STYLES:
        raise CliError(f"{style!r} is not a style: use one of {', '.join(STYLES)}")
    set_name = args.set.strip().lower() if args.set else None
    if set_name is not None and set_name != "none" and set_name not in setplace.SETS:
        raise CliError(f"{set_name!r} is not a set: use one of {', '.join(setplace.SETS)} or none")
    engine = _look_engine(args, sdir)
    tl, tl_hash = _timeline(sdir, short)
    spans = {s.id: s for s in tl.shots}
    data = _open_blend(sdir)
    from bsa.blender import lookframes

    scenes, frames = {}, {}
    for shot in lookframes.look_shots(short):
        shot, scene = _scene(data, short, shot.id, tl_hash)
        scenes[shot.id], frames[shot.id] = scene, _still_frame(shot, spans[shot.id])
    ours = bool(data.filepath) and Path(data.filepath).resolve() == _blend(sdir).resolve()
    try:
        out = lookframes.preview(sdir, short, tl, k, scenes, frames, engine, style, set_name)
    except BaseException:
        if ours:  # whatever was changed in memory goes: the file on disk is the short's
            with contextlib.suppress(Exception):  # a failing revert never hides why the look failed
                bpy.ops.wm.revert_mainfile()
        raise
    if out.pop("changed") and ours:
        bpy.ops.wm.revert_mainfile()
    return {**out, "path": str(sdir / "stills" / lookframes.LOOK_JSON)}


THUMB_TEXT_FIRST = "Write the thumbnail text first: short.json post.thumbnail title (one or two lines)"
MAX_HEROES = 3


def cmd_thumbs(args) -> dict:
    """Thumbnail candidates in both sizes, into review/thumbs/ and review/thumbs.json, with the checks on each."""
    import types

    from bsa.blender import thumbs as blender_thumbs
    from bsa.blender.pixels import read_png

    sdir = _short_dir(args.short)
    short = _load(sdir)
    api = types.SimpleNamespace(render=blender_thumbs.render, read_png=read_png, boxes=_thumb_boxes)
    return _cmd_thumbs(args, sdir, short, api)


def _thumb_boxes(sdir: Path, short, tl_hash: str, shot_id: str, frames: list[int]) -> dict | None:
    """{frame: [the screen box of each of the shot's heroes, or None]} from blend/short.blend; None without it."""
    if not _blend(sdir).is_file():
        return None
    from bsa.blender.anchors import hero_boxes_at, heroes

    _, scene = _scene(_open_blend(sdir), short, shot_id, tl_hash)
    try:
        return hero_boxes_at(scene, heroes(scene, shot_id), frames)
    except ValueError as exc:
        raise CliError(str(exc)) from exc


def _picked_heroes(sdir: Path, short, tl, tl_hash: str, flagged: set[int], api) -> list:
    """The picker's heroes (bsa.thumbs.pick_heroes), with how full each sampled frame is from the .blend."""
    from bsa import thumbs

    fills: dict[int, float] = {}
    for span in tl.shots:
        got = api.boxes(sdir, short, tl_hash, span.id, thumbs.sample_frames(span.f0, span.f1))
        if got is None:  # no .blend: the picker goes by the shots alone
            break
        for frame, boxes in got.items():
            box, warnings = thumbs.hero_box(boxes)
            if not warnings:
                fills[frame] = thumbs.fill(box)
    return thumbs.pick_heroes(list(tl.shots), flagged, fills, short.shots[0].id)


def _only_hero(sdir: Path, n: int, named: list, p: dict):
    """The hero `--only N` remakes candidate N from: --hero, else short.json's Nth hero when candidate N was made from
    short.json's heroes (or there is no candidate N), else candidate N's own (picked by the plugin or named with
    --hero: short.json's list is not the one it came from)."""
    from bsa import thumbs
    from bsa.review.store import StoreDamaged

    if len(named) > 1:
        raise CliError("--only remakes one candidate: give it one --hero at most")
    if named:
        return named[0]
    listed = thumbs.heroes_from_post(p)
    try:
        row = next((c for c in thumbs.load(sdir)["candidates"] if isinstance(c, dict) and c.get("n") == n), None)
    except StoreDamaged as exc:
        if len(listed) >= n:
            return listed[n - 1]
        raise CliError(str(exc)) from exc
    own = row.get("hero") if isinstance(row, dict) else None
    from_post = not isinstance(own, dict) or own.get("why") in (None, thumbs.WHY_POST)
    if len(listed) >= n and from_post:
        return listed[n - 1]
    if row is None:
        raise CliError(f"There is no candidate {n} yet: run BLENDER thumbs SHORT first")
    h = row.get("hero")
    if not (isinstance(h, dict) and isinstance(h.get("shot"), str) and isinstance(h.get("frame"), int)):
        raise CliError(f"{thumbs.THUMBS_JSON} is damaged (candidate {n} has no hero shot and frame): name its frame "
                       "with --hero SHOT@FRAME")  # fmt: skip
    crop = h.get("crop")
    return thumbs.Hero(h["shot"], h["frame"], tuple(crop) if isinstance(crop, list) else None, h.get("why") or "")


def _cmd_thumbs(args, sdir: Path, short, api) -> dict:
    """`api`: render (bsa.blender.thumbs.render), read_png (pixels, top row first) and boxes (_thumb_boxes), so the
    command's plumbing runs without Blender in the tests."""
    from bsa import image_checks, post, thumbs
    from bsa.fonts import font_caches
    from bsa.review.store import StoreDamaged

    s = settings.load_settings()
    errors, _ = post.problems(short, s)
    if errors:
        raise CliError("fix short.json post first: " + "; ".join(errors))
    p = post.fields(short)
    text = thumbs.text_from_post(p)
    if text is None:
        raise CliError(THUMB_TEXT_FIRST)
    tl, tl_hash = _timeline(sdir, short)
    try:
        named = [replace(thumbs.parse_hero(h), why=thumbs.WHY_FLAG) for h in args.hero or []]
    except ValueError as exc:
        raise CliError(str(exc)) from exc
    if len(named) > MAX_HEROES:
        raise CliError(f"{len(named)} --hero given: at most {MAX_HEROES} candidates")
    if args.only is not None and args.only < 1:
        raise CliError("--only takes a candidate number from 1")
    flagged = thumbs.flagged_frames(sdir, thumbs.flag_key(sdir))
    if args.only is not None:
        heroes, numbers = [_only_hero(sdir, args.only, named, p)], [args.only]
    else:
        heroes = named or thumbs.heroes_from_post(p) or _picked_heroes(sdir, short, tl, tl_hash, flagged, api)
        numbers = list(range(1, len(heroes) + 1))
    wrong = thumbs.check_heroes(heroes, {span.id: span for span in tl.shots})
    if wrong:
        raise CliError("; ".join(wrong))
    if not heroes:
        raise CliError("no frame to make a thumbnail from: name one with --hero SHOT@FRAME")
    sources = []
    for h in heroes:  # every refusal before anything is made
        got = thumbs.source_frame(sdir, h.shot, h.frame)
        if got is None:
            raise CliError(f"Render the short first: no frame {h.frame} of {h.shot} in frames/final or frames/preview")
        sources.append(got)
    caches = font_caches(sdir)
    style, notes = thumbs.with_font(thumbs.style_for(short, s), text, caches)  # the layout measures the drawn font
    rows = []
    for n, h, (png, preset) in zip(numbers, heroes, sources, strict=True):
        boxes = (api.boxes(sdir, short, tl_hash, h.shot, [h.frame]) or {}).get(h.frame) or []
        hero, warnings = thumbs.hero_box(boxes)
        if preset == "preview":
            warnings.append(thumbs.FROM_PREVIEW)
        if h.frame in flagged:
            warnings.append(f"frame-check flagged frame {h.frame} of {h.shot}")
        files = thumbs.files_for(n)
        layouts, findings = {}, []
        for size, lay in (("1080x1920", thumbs.vertical(text, hero, style, h.crop)),
                          ("1280x720", thumbs.landscape(text, hero, style, h.crop))):  # fmt: skip
            out = api.render(lay, png, sdir / files[size], style.font, caches, notes=notes, card_dir=sdir / "cards")
            layouts[size] = thumbs.layout_dict(lay)
            findings += image_checks.thumb_findings(api.read_png(out), layouts[size], size, busy=preset == "final")
            warnings += [w for w in lay.warnings if w not in warnings]
        rows.append({"n": n, "hero": {"shot": h.shot, "frame": h.frame, "crop": list(h.crop) if h.crop else None,
                                      "why": h.why},
                     "source": preset, "files": files, "layouts": layouts, "findings": findings,
                     "warnings": warnings})  # fmt: skip
    try:
        thumbs.put_candidates(sdir, rows, only=args.only)
    except StoreDamaged as exc:
        raise CliError(str(exc)) from exc
    if args.only is None:  # all made again: the files of candidates no longer there go
        keep = {(sdir / rel).name for r in rows for rel in r["files"].values()}
        for old in (sdir / thumbs.DIR).glob("*.png"):
            if old.name not in keep:
                old.unlink(missing_ok=True)
    said = [f"candidate {r['n']}: {w}" for r in rows for w in r["warnings"]]
    said += [f"candidate {r['n']}, {f['size']}: {f['message']}" for r in rows for f in r["findings"]
             if f["severity"] != "info"]  # fmt: skip
    return {"candidates": rows, "thumbs": thumbs.THUMBS_JSON, "warnings": said + list(dict.fromkeys(notes))}


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
    side = _engine_run(sdir, args)
    mp4 = _mp4(sdir, short, args.preset, side)
    if not mp4.is_file():
        raise CliError(f"{mp4} does not exist yet: run BLENDER edit SHORT --preset {args.preset}{side.flags()} first")
    from bsa.blender import grab
    from bsa.blender.edit import probe

    info = probe(mp4)
    fps, n = float(info["fps"]), int(info["frames"])
    folder = sdir / "review" / "grabs"
    stem = f"{args.preset}{side.suffix}"  # a side run's grabs never replace the approved run's
    if args.sheet:
        frames = grab.sheet_frames(n, args.count)
        cleared = _fresh_grabs(folder, stem, mp4, clear=args.clear)
        sheet = grab.contact_sheet(mp4, frames, folder / f"{stem}-sheet.png", columns=args.columns)
        out = {"mp4": str(mp4), "sheet": str(sheet), "frames": frames, "cleared": cleared,
               "seconds": [round((f - 1) / fps, 2) for f in frames],
               **_older_grabs(folder, stem, {Path(sheet)})}  # fmt: skip
        if args.guides:
            out["warnings"] = ["--guides draws on --at grabs only, not on the contact sheet"]
        return out
    try:
        frames = [grab.frame_at(t, fps, n) for t in args.at]
    except ValueError as exc:
        raise CliError(str(exc)) from exc
    cleared = _fresh_grabs(folder, stem, mp4, clear=args.clear)
    grab.grab_frames(mp4, list(dict.fromkeys(frames)), folder, stem)
    mine = [folder / f"{stem}-f{f:04d}.png" for f in frames]
    out = {"mp4": str(mp4), "cleared": cleared,
           "grabs": [{"seconds": t, "frame": f, "path": str(p)}
                     for t, f, p in zip(args.at, frames, mine, strict=True)]}  # fmt: skip
    if args.guides:
        out.update(_grab_guides(sdir, short, dict(zip(frames, mine, strict=True)), mp4, args.preset))
    return {**out, **_older_grabs(folder, stem, set(mine))}


def _grab_guides(sdir: Path, short, grabs: dict[int, Path], mp4: Path, preset: str) -> dict:
    """{"guides": [paths]} (plus "warnings"): each grab's guides copy, for the shot its frame is in on the timeline
    the video was edited to (source/timeline.json). A frame past the story (the closing card) gets none. A timeline
    newer than the video (the timing changed after this edit) is warned: the grabs' shots may be off."""
    from bsa.timeline import timeline_from_json

    tl_path = sdir / "source" / "timeline.json"
    try:
        tl = timeline_from_json(json.loads(tl_path.read_text(encoding="utf-8")))
    except (OSError, ValueError, KeyError, TypeError):
        why = ("no guides: source/timeline.json is missing or unreadable, so the grabs' shots are not known (run "
               f"CLI timeline {_short_key(sdir)})")  # fmt: skip
        return {"guides": [], "warnings": [why]}
    warnings = []
    try:
        newer = tl_path.stat().st_mtime_ns > mp4.stat().st_mtime_ns
    except OSError:
        newer = False
    if newer:
        warnings.append(f"source/timeline.json is newer than {mp4.name}: the timing changed after this edit, so a "
                        "grab's shot, and its guides, may be off. Make the video again (render, then BLENDER edit "
                        f"{_short_key(sdir)} --preset {preset}) and grab that")  # fmt: skip
    shots = {s.id: s for s in short.shots}
    kf = _guide_kit(sdir, short)
    drawn = []
    for f, path in grabs.items():
        span = next((s for s in tl.shots if s.f0 <= f <= s.f1), None)
        if span is not None and span.id in shots:
            drawn.append(str(_guides(path, short, shots[span.id], kf)))
    return {"guides": drawn, "warnings": warnings} if warnings else {"guides": drawn}


_GRAB = re.compile(r"-(f\d{4}(-guides)?|sheet)\.png$")  # a frame, its --guides copy, the contact sheet


def _stem_grabs(folder: Path, stem: str) -> list[Path]:
    """This stem's grab PNGs in the folder (the one place the grab file pattern lives). Other presets' and side runs'
    grabs, and anything else in the folder, are not matched."""
    if not folder.is_dir():
        return []
    return [p for p in folder.glob(f"{glob.escape(stem)}-*.png")
            if p.name[len(stem):] and _GRAB.fullmatch(p.name[len(stem):])]  # fmt: skip


def _older_grabs(folder: Path, stem: str, mine: set[Path]) -> dict:
    """{"older": n, "older_note": ...}: this stem's grabs left in the folder that this call did not write. A frame
    and its --guides copy count as one grab."""

    def grab(p: Path) -> str:
        return p.name.replace("-guides.png", ".png")

    n = len({grab(p) for p in _stem_grabs(folder, stem)} - {grab(p) for p in mine})
    out = {"older": n}
    if n:
        out["older_note"] = (f"{n} older grab{'s' if n != 1 else ''} of this video {'are' if n != 1 else 'is'} in "
                             "review/grabs: --clear removes them")
    return out


def _fresh_grabs(folder: Path, stem: str, mp4: Path, clear: bool = False) -> int:
    """Remove this preset's (or side run's) grabs when they came from another video than `mp4` (another edit of it:
    its size or time changed), so frames of an older video never sit beside new ones. The video they came from is
    noted in `<folder>/<stem>.source.json`; grabs with no note are older than the note, so they go too. Returns how
    many files were removed. With `clear`, every grab of this stem goes, whatever video it came from. Other presets'
    and side runs' grabs are left alone."""
    st = mp4.stat()
    source = {"mp4": mp4.name, "size": st.st_size, "mtime_ns": st.st_mtime_ns}
    note = folder / f"{stem}.source.json"
    try:
        same = json.loads(note.read_text(encoding="utf-8")) == source
    except (OSError, ValueError):
        same = False
    removed = 0
    if not same or clear:
        for path in _stem_grabs(folder, stem):
            path.unlink()
            removed += 1
    folder.mkdir(parents=True, exist_ok=True)
    note.write_text(json.dumps(source), encoding="utf-8")
    return removed


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


def _at_spec(text: str) -> tuple[str, float]:
    """("s01", 0.5) from "s01:0.5" (a shot id and a fraction of the shot, 0 to 1)."""
    shot, _, frac = str(text).rpartition(":")
    try:
        value = float(frac)
    except ValueError:
        value = -1.0
    if not shot or not 0.0 <= value <= 1.0:
        raise argparse.ArgumentTypeError(f"{text!r}: give SHOT:FRACTION, like s01:0.5 (a fraction from 0 to 1)")
    return shot, value


def cmd_test_frames(args) -> dict:
    """Single frames at fractions of shots, with the short's current build, engine and look, at a reduced size, into
    review/test-frames/: a quick look at a material or framing before the full preview."""
    from bsa.blender.stills import render_still

    if not 0.05 <= args.scale <= 1.0:
        raise CliError(f"--scale {args.scale}: give a fraction of the full size from 0.05 to 1")
    sdir = _short_dir(args.short)
    short = _load(sdir)
    tl, tl_hash = _timeline(sdir, short)
    spans = {s.id: s for s in tl.shots}
    for shot_id, _ in args.at:
        _shots(short, shot_id)  # every shot exists before anything renders
    data = _open_blend(sdir)
    engine = _engine(sdir)
    folder = sdir / "review" / "test-frames"
    frames, drawn = [], []
    kf = _guide_kit(sdir, short) if args.guides else None
    for shot_id, frac in args.at:
        shot, scene = _scene(data, short, shot_id, tl_hash)
        span = spans[shot.id]
        frame = span.f0 + round(frac * (span.f1 - span.f0))
        out = folder / f"{shot.id}-{round(frac * 100):03d}-f{frame:04d}.png"
        render_still(scene, frame, out, preset="preview", percent=round(args.scale * 100), engine=engine,
                     file_format="PNG")  # fmt: skip
        frames.append({"shot": shot.id, "at": frac, "frame": frame, "path": str(out)})
        if args.guides:
            drawn.append(_guides(out, short, shot, kf))
        else:
            _no_guides(out)
    result = {"folder": str(folder), "engine": engine or "the scene's own", "scale": args.scale,
              "paths": [f["path"] for f in frames], "frames": frames}  # fmt: skip
    if args.guides:
        result["guides"] = [str(p) for p in drawn]
    return result


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


def _framing_notes(sdir: Path) -> list[str]:
    """The framing warnings build.py kept on the scenes of blend/short.blend (bsa.blender.camera.framing_notes), one
    line each: the build step names them, and the checks list them as "framing_call" findings on the review pages."""
    from bsa.findings import framing_notes

    data = _open_blend(sdir)
    return [f"scene {sc.name}: camera {n.get('camera')}: {n.get('message')}" for sc in data.scenes
            for n in framing_notes(sc)]  # fmt: skip


def _build_and_reopen(sdir: Path) -> dict:
    """Build in script mode, open the new blend/short.blend, and name the framing warnings build.py kept on it."""
    blend = _build_script_mode(sdir)
    _reopen(sdir)
    return {"blend": str(blend), "framing": _framing_notes(sdir)}


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
        freshness.require_checks(sdir, tl_hash, _check_keys(short, tl, fps, _parts(sdir, fps)), key=_short_key(sdir))
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
    key = _status_key(sdir, args, "preview")  # a bad --engine / --tag is refused before anything runs
    short = _load(sdir)
    from bsa import render_status as rs

    with rs.Run(rs.status_path(sdir, key), short={"slug": short.slug, "title": short.title, "dir": str(sdir)},
                preset="preview", kind="preview", steps=rs.CHAIN_STEPS) as chain:  # a side run's chain: its own status
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
    elif stale := freshness.stale_build_file(sdir):  # build.py, or a script it imports
        why = freshness.build_changed_message(sdir, stale).removesuffix(": rebuild")
    elif not _scenes_current(sdir, short, tl_hash):
        why = "the .blend's scenes are missing or from another timing"
    else:
        why = None
    if why and args.no_build:
        stop("build", f"the scene file is not current ({why}): rebuild it through the MCP (bsa_mcp_build.py), then "
                      "run BLENDER preview SHORT --no-build again")  # fmt: skip
    if why:
        run("build", _build_and_reopen, sdir, summary=lambda o: {"why": why, "framing": o.get("framing")})
    else:
        skip("build", "the .blend is current")
    if why or not _marked_after(sdir, "build", _blend(sdir)):
        run("build done", bsa_cli.cmd_state, ns(short=args.short, action="done", stage="build", info=None))
    else:
        chain.skip("build done", "already marked after the scene was built")
    run("mix", bsa_cli.cmd_mix, ns(short=args.short),
        summary=lambda o: {"path": o.get("path"), "sfx_notes": (o.get("sfx") or {}).get("notes")})  # fmt: skip
    which = {"engine": getattr(args, "engine", None), "tag": getattr(args, "tag", None)}  # a side run: its own files
    if _checks_current(sdir, short, tl, tl_hash):
        skip("check", "already passed on every shot as it is now")
    else:
        run("check", cmd_check, ns(short=args.short, every=args.every, full=False, preset="preview", **which),
            summary=lambda o: {"warnings": o.get("warnings"), "checked": o.get("checked"), "kept": o.get("kept")})
    from bsa import engine_runs

    side = _engine_run(sdir, args)
    run("render", cmd_render, ns(short=args.short, preset="preview", shot=None, frames=None, redo_flagged=False,
                                 no_wait=getattr(args, "no_wait", False), **which),
        summary=_render_summary)  # fmt: skip
    run("frame-check", cmd_frame_check, ns(short=args.short, preset="preview", **which),
        summary=lambda o: {"warnings": o.get("warnings")})  # fmt: skip
    edit = run("edit", cmd_edit, ns(short=args.short, preset="preview", **which),
               summary=lambda o: {"mp4": o.get("mp4"), **({"closing_note": (o.get("closing") or {}).get("note")}
                                                          if (o.get("closing") or {}).get("note") else {})})  # fmt: skip
    out = {"steps": steps, "mp4": edit.get("mp4"), "checks": str(sdir / "review" / "checks.json"),
           "frame_checks": str(engine_runs.frame_checks_path(sdir, "preview", side)), "engine": side.engine,
           "engines": engine_runs.compare(sdir, "preview")}  # fmt: skip
    if side.tag:
        out["tag"] = side.tag
    if edit.get("closing"):  # its "note" says when the voice line held the card past closing.seconds
        out["closing"] = edit["closing"]
    if edit.get("warnings"):
        out["warnings"] = edit["warnings"]
    return out


def _render_summary(out: dict) -> dict:
    """Which shots the render kept and which it rendered again, and why ("fingerprint changed", "new range"...)."""
    shots = out.get("shots", [])
    return {"frames": sum(s.get("rendered", 0) for s in shots),
            "kept": [s["shot"] for s in shots if s.get("rendered") == 0],
            "rendered": [{"shot": s["shot"], "frames": s.get("rendered"), "why": s.get("why")}
                         for s in shots if s.get("rendered")],
            **({"warnings": out["warnings"]} if out.get("warnings") else {})}  # fmt: skip


def cmd_probe(args) -> dict:
    from bsa.blender.edit import probe

    mp4 = Path(args.mp4)
    if not mp4.is_file():
        raise CliError(f"{mp4} does not exist")
    return {"mp4": str(mp4.resolve()), **probe(mp4)}


# --------------------------------------------------------------------------- parser


def _engine_args(sp) -> None:
    """--engine / --tag: a side run in its own frames folder and MP4 (bsa.engine_runs); the approved run is kept."""
    sp.add_argument("--engine", choices=["cycles", "eevee"], default=None,
                    help="render with this engine instead of the approved one (a side run, tagged with its name)")
    sp.add_argument("--tag", default=None, help="name of a side run: frames/<preset>-TAG/ and out/<slug>-<preset>-TAG.mp4")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="bsa_blender.py", description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="command", required=True)

    sp = sub.add_parser("stills", help="a still per shot at its still_at (or one shot to --out)")
    sp.add_argument("short")
    sp.add_argument("--shot")
    sp.add_argument("--out")
    sp.add_argument("--no-wait", action="store_true", help="fail at once when another command holds the GPU lock")
    sp.add_argument("--guides", action="store_true",
                    help="also write <name>-guides.png beside each frame: the caption zone, the top cards and the "
                    "framing band outlined")
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
    _engine_args(sp)
    sp.set_defaults(fn=cmd_render)

    sp = sub.add_parser("check", help="scene checks into review/checks.json (only shots that changed)")
    sp.add_argument("short")
    sp.add_argument("--every", type=int, default=5, help="check every Nth frame (pops use every frame)")
    sp.add_argument("--full", action="store_true", help="check every shot, changed or not")
    sp.add_argument("--preset", choices=("preview", "final"), default="preview",
                    help="the live status it records its progress in (review/render-status-<preset>.json), which "
                    "the review page's Render stage shows")
    sp.set_defaults(fn=cmd_check)

    sp = sub.add_parser("frame-check", help="checks on rendered frames")
    sp.add_argument("short")
    sp.add_argument("--preset", required=True, choices=["preview", "final"])
    _engine_args(sp)
    sp.set_defaults(fn=cmd_frame_check)

    sp = sub.add_parser("edit", help="captions, sound and the MP4 encode")
    sp.add_argument("short")
    sp.add_argument("--preset", required=True, choices=["preview", "final"])
    _engine_args(sp)
    sp.set_defaults(fn=cmd_edit)

    sp = sub.add_parser("grab", help="frames of the edited MP4 at full size (--at SECONDS...) or a contact sheet")
    sp.add_argument("short")
    sp.add_argument("--preset", required=True, choices=["preview", "final"])
    which = sp.add_mutually_exclusive_group(required=True)
    which.add_argument("--at", type=float, nargs="+", metavar="SECONDS")
    which.add_argument("--sheet", action="store_true", help="evenly spaced frames, a quarter size, in one image")
    sp.add_argument("--count", type=int, default=12, help="frames on the contact sheet")
    sp.add_argument("--columns", type=int, default=4, help="contact sheet columns")
    sp.add_argument("--clear", action="store_true", help="remove this video's older grabs first")
    sp.add_argument("--guides", action="store_true",
                    help="also write <name>-guides.png beside each frame: the caption zone, the top cards and the "
                    "framing band outlined")
    _engine_args(sp)
    sp.set_defaults(fn=cmd_grab)

    sp = sub.add_parser("extremes", help="full-size stills of each hero at the min/max of each animated channel")
    sp.add_argument("short")
    sp.add_argument("--shot")
    sp.add_argument("--no-wait", action="store_true", help="fail at once when another command holds the GPU lock")
    sp.set_defaults(fn=cmd_extremes)

    sp = sub.add_parser("test-frames", help="single frames at fractions of shots, reduced size, into review/test-frames/")
    sp.add_argument("short")
    sp.add_argument("--at", type=_at_spec, nargs="+", required=True, metavar="SHOT:FRACTION", help="e.g. s01:0.5")
    sp.add_argument("--scale", type=float, default=0.4, help="fraction of the full resolution (default 0.4)")
    sp.add_argument("--no-wait", action="store_true", help="fail at once when another command holds the GPU lock")
    sp.add_argument("--guides", action="store_true",
                    help="also write <name>-guides.png beside each frame: the caption zone, the top cards and the "
                    "framing band outlined")
    sp.set_defaults(fn=cmd_test_frames)

    sp = sub.add_parser("preview", help="after an edit: build, mark build done, mix, check, render, frame-check, edit")
    sp.add_argument("short")
    sp.add_argument("--rebuild", action="store_true", help="run build.py even when the .blend looks current")
    sp.add_argument("--no-build", action="store_true", help="MCP mode: never run build.py (rebuild through the MCP)")
    sp.add_argument("--every", type=int, default=5, help="check every Nth frame (as BLENDER check)")
    sp.add_argument("--no-wait", action="store_true", help="fail at once when another command holds the GPU lock")
    _engine_args(sp)
    sp.set_defaults(fn=cmd_preview)

    sp = sub.add_parser("closing", help="make the closing card (stills/closing.png) and say what the short ends on")
    sp.add_argument("short")
    sp.set_defaults(fn=cmd_closing)

    sp = sub.add_parser("thumbs", help="thumbnail candidates in both sizes, checked, into review/thumbs.json")
    sp.add_argument("short")
    sp.add_argument("--hero", action="append", metavar="SHOT@FRAME",
                    help="a frame to make a candidate from (up to 3); else short.json's, else picked")
    sp.add_argument("--only", type=int, metavar="N", help="make candidate N again, the others stay")
    sp.set_defaults(fn=cmd_thumbs)

    sp = sub.add_parser("kit-sample", help="a sample sheet of a brand kit (before it is saved) to --out")
    sp.add_argument("--kit", metavar="JSON", help="a brand object; else the settings' brand")
    sp.add_argument("--out", required=True, metavar="PNG")
    sp.set_defaults(fn=cmd_kit_sample)

    sp = sub.add_parser("style-sample", help="the demo machine in each style on a set, tiled into one PNG at --out")
    sp.add_argument("--kit", metavar="JSON", help="a brand object; else the settings' brand")
    sp.add_argument("--styles", metavar="A,B", help="style names, comma separated; else the kit's own style")
    sp.add_argument("--set", metavar="NAME", help="a scene set (workbench, graph, outdoor, studio) or none; default "
                                                  "studio")  # fmt: skip
    sp.add_argument("--engine", choices=["cycles", "eevee"], default=None,
                    help="the render engine (the user's choice); else the render.engine setting")
    sp.add_argument("--scale", type=float, default=0.5, help="each tile's size as a fraction of 1080x1920 (default 0.5)")
    sp.add_argument("--out", required=True, metavar="PNG")
    sp.set_defaults(fn=cmd_style_sample)

    sp = sub.add_parser("look", help="the look preview: three stills with the kit's cards, the kit sheet, look.json")
    sp.add_argument("short")
    sp.add_argument("--style", metavar="NAME", help="show this style instead of the built one (in memory only)")
    sp.add_argument("--set", metavar="NAME", help="show this set (workbench, graph, outdoor, studio) or none instead "
                                                  "of the built one (in memory only)")  # fmt: skip
    sp.add_argument("--engine", choices=["cycles", "eevee"], default=None,
                    help="the render engine (the user's choice); else the short's approved engine or render.engine")
    sp.add_argument("--no-wait", action="store_true", help="fail at once when another command holds the GPU lock")
    sp.set_defaults(fn=cmd_look)

    sp = sub.add_parser("probe", help="size, fps, frames and audio of an MP4")
    sp.add_argument("mp4")
    sp.set_defaults(fn=cmd_probe)
    return p


def _one_line(text) -> str:
    return messages.one_line(text, ERROR_LIMIT)


def _warn_stale_build(args, out: dict) -> None:
    """Every command on a short says so (in its result and on one stderr line) when build.py, or a local script it
    imports (freshness.build_inputs), is newer than the .blend. check and frame-check already count their findings
    under "warnings", so theirs go under "build_warnings"."""
    short = getattr(args, "short", None)
    if short is None:
        return
    sdir = _short_dir(short)
    stale = freshness.stale_build_file(sdir)
    if stale is None:
        return
    message = freshness.build_changed_message(sdir, stale)
    key = "build_warnings" if isinstance(out.get("warnings"), int) else "warnings"
    out.setdefault(key, []).append(message)
    print(f"warning: {message}", file=sys.stderr, flush=True)


def _add_page_notes(args, out: dict) -> None:
    """A result for a short says what is waiting on its review page (notes, answers Claude hasn't acted on)."""
    from bsa.review import inbox

    try:
        sdir = _short_dir(args.short) if getattr(args, "short", None) else None
    except Exception:  # noqa: BLE001 - a short that doesn't resolve has no page
        sdir = None
    inbox.attach(out, sdir)


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
        place = _gpu_place(args) if args.command in GPU_COMMANDS - {"render"} else None  # render takes it itself
        if place is not None:  # (so the preview chain's render does too)
            with _gpu(args, place, place.name, args.command):
                out = args.fn(args)
        else:
            out = args.fn(args)
        _warn_stale_build(args, out)
        _add_page_notes(args, out)
    except Exception as exc:  # noqa: BLE001 - every failure becomes one plain line for Claude to relay
        plain = isinstance(exc, (freshness.StaleError, CliError, jobstate.StateDamaged))
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
GPU_COMMANDS = {"render", "extremes", "estimate", "test-frames", "look", "style-sample", "stills"}  # one at a time per workspace (gpulock); preview via render


def _gpu_place(args) -> Path | None:
    """Where a heavy command takes the GPU lock (its parent is the workspace): the short's folder, or for a command
    with no short (style-sample) the workspace itself as "<workspace>/style-sample" (None, unlocked, with no
    workspace set)."""
    if getattr(args, "short", None):
        return _short_dir(args.short)
    ws = settings.load_settings().get("workspace")
    return Path(ws) / args.command if ws else None


@contextmanager
def _gpu(args, sdir: Path, short: str, command: str, preset: str | None = None):
    """Hold the workspace's GPU lock (<workspace>/.gpu.lock) for a heavy command: wait for another session's job,
    saying so on stderr and in the live status (render-status and its page), or with --no-wait fail at once."""
    from bsa import gpulock
    from bsa import render_status as rs

    status = rs.status_path(sdir, preset) if preset else None
    said = []
    beat = gpulock.Heartbeat()

    def on_wait(text: str) -> None:
        line = beat.line(text)
        if line is not None:
            print(f"{short} {command} is {line}", file=sys.stderr, flush=True)  # every line says which job waits
        said.append(text)
        if status is not None:
            rs.note_waiting(status, text)

    try:
        ctx = gpulock.held(gpulock.lock_path(sdir.parent), short=short, command=command,
                           wait=not getattr(args, "no_wait", False), on_wait=on_wait)  # fmt: skip
        ctx.__enter__()
    except gpulock.GpuBusy as exc:
        raise CliError(str(exc)) from exc
    if said and status is not None:
        rs.note_waiting(status, None)
    try:
        yield
    finally:
        ctx.__exit__(None, None, None)


def _say_starting(args):
    """For a command that records a live status: write "starting" (this process, the command) to it at once,
    before the command reads anything or opens the .blend, so the last run is never shown as current meanwhile.
    Returns the status path, or None. A status problem never stops the command."""
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
        try:
            key = _status_key(sdir, args, preset)
        except CliError:
            return None  # a bad --engine / --tag: the command refuses it; never write over the approved run's status
        path = rs.status_path(sdir, key)
        if rs.starting(path, short=short, preset=preset, kind=args.command):
            return path
    except Exception as exc:  # noqa: BLE001 - status only
        print(f"status: {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
    return None


def _settle_starting(status, error: str | None) -> None:
    if status is None:
        return
    try:
        from bsa import render_status as rs

        rs.settle_starting(status, error)
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
