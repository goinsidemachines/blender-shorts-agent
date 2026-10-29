"""Is each stage's output still current? The saved timeline, each scene's timeline stamp, each shot's rendered frames
(`.render.json`), the label anchors and the scene checks (`review/checks.json`) carry stamps; a stale one fails with
a plain message saying what to run.

Frames, anchors and checks are stamped per shot with the shot's content fingerprint (bsa.blender.fingerprint: a hash
of everything that shot renders), not with the .blend's save time, so a rebuild that changes one shot leaves every
other shot's frames, anchors and checks current. `blend/short.fingerprints.json` caches the fingerprints of one
saved .blend (by its size and time, the files it reads, and the Blender and render code it was made with) for the
commands that do not open it, with each fingerprint's parts (data per type, animation, code) so a refusal can say
what changed. Only what a stage really reads goes into its stamp: the post text, sound levels and settings never
make frames or checks stale. Pure: no bpy."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from bsa.timeline import Span, Timeline, timeline_hash, to_json

TIMELINE_PROP = "bsa_timeline"  # custom property build.py sets on every scene it builds


class StaleError(Exception):
    """An output is from an older timing, build or engine; the message says what to run again."""


# --------------------------------------------------------------------------- timeline


def check_timeline(short_dir: Path, tl: Timeline) -> str:
    """The hash of source/timeline.json, which must equal the timeline recomputed from short.json and the takes
    (else build.py animated a different timing from the one shots, captions and narration now use)."""
    path = Path(short_dir) / "source" / "timeline.json"
    if not path.is_file():
        raise StaleError("source/timeline.json is missing: run CLI timeline SHORT, then rebuild (source/build.py)")
    try:
        saved = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise StaleError(f"source/timeline.json is not valid JSON ({exc}): run CLI timeline SHORT, then rebuild") from exc
    if saved != json.loads(json.dumps(to_json(tl))):
        raise StaleError("timing changed: run CLI timeline SHORT, then rebuild (source/build.py)")
    return timeline_hash(saved)


def check_scene_stamp(scene_name: str, stamp, want: str) -> None:
    if stamp is None:
        raise StaleError(f"scene {scene_name!r} has no timeline stamp: rebuild with source/build.py (it must set "
                         f'scene["{TIMELINE_PROP}"] = timeline_hash(tl), see 06-build.md)')  # fmt: skip
    if str(stamp) != want:
        raise StaleError(f"the .blend was built for another timing (scene {scene_name!r}): rebuild with "
                         "source/build.py")  # fmt: skip


# --------------------------------------------------------------------------- the scene file

BUILD_CHANGED = "build.py changed since the scene was built: rebuild"


def build_changed(short_dir: Path) -> bool:
    """source/build.py was edited after blend/short.blend was saved: the scenes are not what build.py now makes."""
    build, blend = Path(short_dir) / "source" / "build.py", Path(short_dir) / "blend" / "short.blend"
    return build.is_file() and blend.is_file() and build.stat().st_mtime_ns > blend.stat().st_mtime_ns


def require_current_build(short_dir: Path, command: str) -> None:
    """The final render and edit refuse a scene file older than build.py; `command` names which ("the final edit")."""
    if build_changed(short_dir):
        raise StaleError(f"{BUILD_CHANGED} (run source/build.py) before {command}")


# --------------------------------------------------------------------------- shot fingerprints

FINGERPRINT_VERSION = 1  # bump when what goes into a fingerprint changes (bsa.blender.fingerprint)
FINGERPRINTS = "short.fingerprints.json"


def _blend_stat(short_dir: Path) -> list | None:
    return _file_stat(Path(short_dir) / "blend" / "short.blend")


def _file_stat(path) -> list | None:
    try:
        st = Path(path).stat()
    except OSError:
        return None
    return [st.st_size, st.st_mtime_ns]


_RENDER_CODE = Path(__file__).resolve().parent / "blender" / "scene.py"  # goes into every fingerprint


def fingerprint_code(blender: str | None) -> str:
    """What a fingerprint depends on besides the .blend: FINGERPRINT_VERSION, the Blender version and the render
    presets' code (bsa/blender/scene.py), as bsa.blender.fingerprint hashes them. A cache made under another one
    would differ from a fresh fingerprint, so it is not used."""
    try:
        code = hashlib.sha256(_RENDER_CODE.read_bytes()).hexdigest()[:16]
    except OSError:
        code = "?"
    return f"{FINGERPRINT_VERSION}:{blender}:{code}"


def save_fingerprints(short_dir: Path, tl_hash: str, shots: dict, files: dict | None = None, *, code: str | None = None,
                      parts: dict | None = None) -> None:  # fmt: skip
    """Cache the fingerprints of blend/short.blend as it is on disk now, for this timing (and `code`,
    fingerprint_code). `parts` ({shot id: {part: hash}}) records what each fingerprint is made of, so a later
    refusal can say which part changed."""
    blend = _blend_stat(short_dir)
    if blend is None:
        return
    path = Path(short_dir) / "blend" / FINGERPRINTS
    data = {"version": FINGERPRINT_VERSION, "blend": blend, "timeline": tl_hash, "shots": shots,
            "files": files or {}, **({"code": code} if code else {}), **({"parts": parts} if parts else {})}  # fmt: skip
    path.write_text(json.dumps(data, indent=1) + "\n", encoding="utf-8")


def _read_cache(short_dir: Path) -> dict | None:
    try:
        data = json.loads((Path(short_dir) / "blend" / FINGERPRINTS).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def load_fingerprints(short_dir: Path, tl_hash: str, code: str | None = None) -> dict | None:
    """The cached fingerprints ({shot id: fingerprint}) when they are of this very .blend, timing, files and (when
    both say) fingerprint code; else None (compute them in Blender)."""
    data = _read_cache(short_dir)
    if data is None or data.get("version") != FINGERPRINT_VERSION or data.get("timeline") != tl_hash:
        return None
    if data.get("blend") != _blend_stat(short_dir) or not isinstance(data.get("shots"), dict):
        return None
    if code is not None and data.get("code") not in (None, code):  # a cache from before the code was recorded holds
        return None
    for path, stat in (data.get("files") or {}).items():
        if _file_stat(path) != stat:
            return None
    return data["shots"]


def load_fingerprint_parts(short_dir: Path, fingerprints: dict | None) -> dict:
    """{shot id: {part: hash}} from the cache, for the shots whose cached fingerprint is the one given; {} for a
    cache from before parts were recorded."""
    data = _read_cache(short_dir) or {}
    shots, parts = data.get("shots"), data.get("parts")
    if not isinstance(shots, dict) or not isinstance(parts, dict):
        return {}
    return {sid: parts[sid] for sid, fp in (fingerprints or {}).items()
            if fp is not None and shots.get(sid) == fp and isinstance(parts.get(sid), dict)}  # fmt: skip


_PART_WORDS = {"Object": "objects", "Camera": "camera", "Material": "materials", "Mesh": "meshes",
               "World": "world", "Scene": "scene settings", "Image": "images", "VectorFont": "fonts",
               "TextCurve": "text", "Curve": "curves", "Collection": "collections", "Texture": "textures",
               "ParticleSettings": "particles", "Armature": "armatures", "Key": "shape keys",
               "animation": "animation", "drivers": "drivers", "tags": "tags"}  # fmt: skip
RENDER_CODE_CHANGED = "the plugin's render code or Blender version changed"


def changed_parts(old, new) -> list[str] | None:
    """The parts of a fingerprint that differ between two {part: hash} records; None when either is missing."""
    if not isinstance(old, dict) or not isinstance(new, dict) or not old or not new:
        return None
    return sorted(k for k in set(old) | set(new) if old.get(k) != new.get(k))


def _part_word(part: str) -> str:
    kind = part.split(":", 1)[-1]
    if kind in _PART_WORDS:
        return _PART_WORDS[kind]
    if kind.endswith("Light"):
        return "lights"
    if kind.endswith("NodeTree"):
        return "node groups"
    return kind.lower()


def describe_change(shot_id: str, parts: list[str] | None) -> str:
    """"shot s01's camera/objects changed" from changed_parts; a plain "scene content" when unknown."""
    if parts and "code" in parts:
        return f"{RENDER_CODE_CHANGED} (shot {shot_id})"
    words = sorted({_part_word(p) for p in parts or ()})
    if not words:
        return f"shot {shot_id}'s scene content changed"
    return f"shot {shot_id}'s {'/'.join(words)} changed"


# --------------------------------------------------------------------------- label anchors

ANCHORS = "anchors.json"
_ANCHORS_AGAIN = "run BLENDER anchors SHORT"


def anchors_stamp(fingerprints: dict | None, tl_hash: str) -> dict:
    """{"timeline", "shots": {shot id: fingerprint}}: a shot's anchors stay current while its fingerprint does."""
    return {"timeline": tl_hash, "shots": dict(fingerprints or {})}


def _anchors_current(stamp, fingerprints: dict | None, tl_hash: str, shots) -> bool:
    if not isinstance(stamp, dict) or stamp.get("timeline") != tl_hash or not isinstance(stamp.get("shots"), dict):
        return False
    return all(stamp["shots"].get(s) == (fingerprints or {}).get(s) for s in shots)


def read_anchors(short_dir: Path) -> dict | None:
    try:
        data = json.loads((Path(short_dir) / "source" / ANCHORS).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def reusable_anchors(short_dir: Path, fingerprints: dict | None, tl_hash: str) -> dict:
    """{"shots": {shot: {...}}, "heroes": {shot: {...}}} from the existing anchors.json for shots that are unchanged
    (same timing and fingerprint): the anchors are projected again only for the others."""
    data = read_anchors(short_dir) or {}
    out: dict = {"shots": {}, "heroes": {}}
    for key in ("shots", "heroes"):
        entries = data.get(key)
        for shot, entry in entries.items() if isinstance(entries, dict) else ():
            if _anchors_current(data.get("stamp"), fingerprints, tl_hash, [shot]):
                out[key][shot] = entry
    return out


def _read_anchors(short_dir: Path, fingerprints: dict | None, tl_hash: str, shots) -> dict:
    data = read_anchors(short_dir)
    if data is None:
        raise StaleError(f"labels placed near objects need source/{ANCHORS}: {_ANCHORS_AGAIN} (render runs it too)")
    if not _anchors_current(data.get("stamp"), fingerprints, tl_hash, shots):
        raise StaleError(f"source/{ANCHORS} is from an older build or timing: {_ANCHORS_AGAIN}")
    return data


def load_hero_boxes(short_dir: Path, fingerprints: dict | None, tl_hash: str, shot: str) -> dict:
    """source/anchors.json's "heroes" ({shot: {"f0", "boxes": {object: [[x0, y0, x1, y1] or None, ...]}}}, the last
    shot's heroes on screen), which must be from this timing and `shot`'s current fingerprint; {} when it holds none
    (or not a mapping)."""
    heroes = _read_anchors(short_dir, fingerprints, tl_hash, [shot]).get("heroes")
    return heroes if isinstance(heroes, dict) else {}


def load_anchors(short_dir: Path, fingerprints: dict | None, tl_hash: str, need=()) -> dict:
    """source/anchors.json's shots ({shot: {object: {"f0", "points"}}}), which must be from this timing and each
    needed shot's current fingerprint, and hold every (shot id, label text, object name) in `need`."""
    shots = _read_anchors(short_dir, fingerprints, tl_hash, sorted({s for s, _, _ in need})).get("shots") or {}
    for shot_id, text, name in need:
        if name not in (shots.get(shot_id) or {}):
            raise StaleError(f"no anchors for label {text!r} near {name!r} in shot {shot_id}: {_ANCHORS_AGAIN}")
    return shots


# --------------------------------------------------------------------------- rendered frames


_STAMP_KEYS = ("f0", "f1", "engine", "preset", "fingerprint")  # what decides whether frames are current


def frames_stamp(span: Span, engine: str | None, fingerprint: str | None, preset: str | None = None,
                 parts: dict | None = None) -> dict:  # fmt: skip
    """What a shot's frames were rendered from: its frame range, the engine, the preset and its fingerprint (and,
    as information only, the fingerprint's parts: a later refusal names the part that changed)."""
    stamp = {"f0": span.f0, "f1": span.f1, "engine": engine, "preset": preset, "fingerprint": fingerprint}
    return {**stamp, "parts": parts} if parts else stamp


def read_frames_stamp(folder: Path) -> dict | None:
    try:
        had = json.loads((Path(folder) / ".render.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return had if isinstance(had, dict) else None


def _core(stamp: dict | None) -> dict | None:
    return {k: v for k, v in stamp.items() if k not in ("patched", "parts")} if isinstance(stamp, dict) else None


OLD_FRAMES = "rendered by an older plugin version, before per-shot fingerprints"


def frames_reason(had: dict | None, stamp: dict) -> str | None:
    """Why frames stamped `had` can't be kept for `stamp` ("new range", "engine changed", "fingerprint changed"...),
    or None when they can."""
    had, stamp = _core(had), _core(stamp)
    if had == stamp:
        return None
    if had is None:
        return "not rendered yet"
    if (had.get("f0"), had.get("f1")) != (stamp.get("f0"), stamp.get("f1")):
        return "new range"
    if had.get("engine") != stamp.get("engine"):
        return "engine changed"
    if not had.get("fingerprint"):
        return OLD_FRAMES  # before the preset check: stamps that old have no preset either
    if had.get("preset") != stamp.get("preset"):
        return "preset changed"
    return "fingerprint changed"


def prepare_frames(folder: Path, stamp: dict) -> str | None:
    """Keep frames from an earlier (or interrupted) render of the same shot content and range; clear anything else.
    Returns why the frames were cleared, or None when they were kept."""
    folder = Path(folder)
    had = read_frames_stamp(folder)
    reason = frames_reason(had, stamp)
    if reason is not None:
        for png in folder.glob("*.png"):
            png.unlink()
        folder.mkdir(parents=True, exist_ok=True)
        (folder / ".render.json").write_text(json.dumps(stamp), encoding="utf-8")
    elif stamp.get("parts") and had.get("parts") != stamp["parts"]:  # kept: record the parts it now has
        keep = {"patched": had["patched"]} if had.get("patched") else {}
        (folder / ".render.json").write_text(json.dumps({**stamp, **keep}), encoding="utf-8")
    return reason


def parse_frames(spec: str) -> list[int]:
    """'120-140,200' -> [120, ..., 140, 200]."""
    out: set[int] = set()
    for part in str(spec).replace(" ", "").split(","):
        if not part:
            continue
        lo, sep, hi = part.partition("-")
        try:
            a = int(lo)
            b = int(hi) if sep else a
        except ValueError:
            raise ValueError(f"--frames {spec!r}: use frame numbers and ranges, e.g. 120-140,200") from None
        if b < a:
            raise ValueError(f"--frames {spec!r}: range {part} runs backwards")
        out.update(range(a, b + 1))
    if not out:
        raise ValueError("--frames is empty: give frame numbers or ranges, e.g. 120-140,200")
    return sorted(out)


def frames_spec(frames) -> str:
    """[120, 121, 122, 200] -> '120-122,200'."""
    frames = sorted(set(frames))
    parts, i = [], 0
    while i < len(frames):
        j = i
        while j + 1 < len(frames) and frames[j + 1] == frames[j] + 1:
            j += 1
        parts.append(str(frames[i]) if i == j else f"{frames[i]}-{frames[j]}")
        i = j + 1
    return ",".join(parts)


def redo_frames(folder: Path, span: Span, stamp: dict, frames) -> dict:
    """Delete these frames of one shot so the render makes them again; the shot's other frames stay. The frames on
    disk must be of this range, engine and preset. When only the shot's fingerprint changed (the scene was edited
    since), the other frames stay from the earlier build: the stamp takes the new fingerprint and records the patch
    under "patched" (the caller warns). Returns {"frames": [...], "mixed": bool}."""
    folder = Path(folder)
    bad = [f for f in frames if not span.f0 <= f <= span.f1]
    if bad:
        raise StaleError(f"--frames {frames_spec(bad)} are outside shot {span.id} (frames {span.f0}-{span.f1})")
    had = read_frames_stamp(folder)
    reason = frames_reason(had, stamp)
    if reason not in (None, "fingerprint changed", OLD_FRAMES):
        raise StaleError(f"shot {span.id}'s frames can't be patched ({reason}): render the whole shot "
                         f"(BLENDER render SHORT --preset {stamp.get('preset')} --shot {span.id})")  # fmt: skip
    for f in frames:
        (folder / f"{f:04d}.png").unlink(missing_ok=True)
    patched = (had or {}).get("patched")
    mixed = reason is not None or bool(patched)
    new = dict(stamp)
    if mixed:
        old = patched if isinstance(patched, dict) else {"from": (had or {}).get("fingerprint"), "frames": []}
        new["patched"] = {"from": old.get("from"), "frames": sorted(set(old.get("frames") or []) | set(frames))}
    (folder / ".render.json").write_text(json.dumps(new), encoding="utf-8")
    return {"frames": sorted(frames), "mixed": mixed}


def rendered_frames(folder: Path, span: Span, engine: str | None, fingerprint: str | None, preset: str,
                    parts: dict | None = None) -> list[Path]:  # fmt: skip
    """Exactly frames span.f0..span.f1 of one shot, rendered for this timing, engine and shot content; never
    padded. A None fingerprint (no .blend to fingerprint) skips that part. `parts` (the fingerprint's parts) lets a
    refusal name what changed."""
    folder = Path(folder)
    again = f"run BLENDER render SHORT --preset {preset} again"
    raw = read_frames_stamp(folder)
    had = _core(raw)
    if had is None:
        raise StaleError(f"no {preset} frames for shot {span.id}: run BLENDER render SHORT --preset {preset} first")
    if (had.get("f0"), had.get("f1")) != (span.f0, span.f1):
        raise StaleError(f"shot {span.id}'s {preset} frames are from an older timing (frames {had.get('f0')}-"
                         f"{had.get('f1')}, now {span.f0}-{span.f1}): {again}")  # fmt: skip
    if had.get("engine") != engine:
        raise StaleError(f"shot {span.id}'s {preset} frames were rendered with another engine "
                         f"({had.get('engine')}, now {engine}): {again}")  # fmt: skip
    if fingerprint is not None and not had.get("fingerprint"):
        raise StaleError(f"shot {span.id}'s {preset} frames were {OLD_FRAMES} (nothing in the scene needs to have "
                         f"changed): {again} (once; after that only changed shots render again)")  # fmt: skip
    if fingerprint is not None and had.get("fingerprint") != fingerprint:
        which = changed_parts((raw or {}).get("parts"), parts)
        what = (describe_change(span.id, which) if which is not None
                else f"shot {span.id}'s scene content or animation changed")  # fmt: skip
        raise StaleError(f"{what} since its {preset} frames were rendered (fingerprint changed): {again} (only "
                         "changed shots render again)")  # fmt: skip
    want = [folder / f"{f:04d}.png" for f in range(span.f0, span.f1 + 1)]
    bad = [p.name for p in want if not p.is_file() or p.stat().st_size == 0]
    if bad:
        raise StaleError(f"shot {span.id} is missing {preset} frames or has empty ones ({', '.join(bad[:5])}"
                         f"{', ...' if len(bad) > 5 else ''}): {again} (it resumes)")  # fmt: skip
    extra = sorted(p.name for p in set(folder.glob("*.png")) - set(want))
    if extra:
        raise StaleError(f"shot {span.id} has {preset} frames outside its range ({', '.join(extra[:5])}): {again}")
    return want


# --------------------------------------------------------------------------- scene checks

CHECKS_VERSION = 1  # bump when the scene checks change what they find: every shot is checked again once
_CHECKS_CODE = Path(__file__).resolve().parent / "blender" / "checks.py"


def _checks_codes() -> tuple[str, str]:
    """(the code hash, the hash of the file's raw bytes as stamps before line endings were ignored used)."""
    try:
        raw = _CHECKS_CODE.read_bytes()
    except OSError:
        return f"{CHECKS_VERSION}:?", f"{CHECKS_VERSION}:?"
    norm = raw.replace(b"\r\n", b"\n")
    return (f"{CHECKS_VERSION}:{hashlib.sha256(norm).hexdigest()[:16]}",
            f"{CHECKS_VERSION}:{hashlib.sha256(raw).hexdigest()[:16]}")  # fmt: skip


def checks_code() -> str:
    """CHECKS_VERSION plus a hash of the checks' code (line endings ignored: a CRLF checkout is the same code): any
    change to them means a full re-check."""
    return _checks_codes()[0]


def _code_current(code) -> bool:
    return code in _checks_codes()


CHECKED_FIELDS = ("scene", "labels")  # the short.json shot fields the scene checks read (bsa.blender.checks)


def shot_check_key(fingerprint: str | None, span: Span, shot: dict) -> str:
    """The key stamps used before the check inputs were recorded: a hash of the fingerprint, the frames and the
    whole short.json entry. Still accepted for checks.json files written then (shot_check_inputs "legacy")."""
    text = json.dumps([fingerprint, span.f0, span.f1, shot], sort_keys=True, default=str)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:32]


def shot_check_inputs(fingerprint: str | None, span: Span, shot: dict, shot_ids=(), parts: dict | None = None,
                      rules: list | None = None) -> dict:  # fmt: skip
    """What one shot's scene checks depend on, and nothing else: its content (fingerprint), its frames, the fields
    of its short.json entry the checks read (CHECKED_FIELDS: not its title, picture, still_at or badge), the shot
    ids (hero tags name them) and, only when a hard rule scans scene names, those rules (bsa.rules.scene_key). Post
    text, sound levels and other settings are not in it. `parts` is information (which part of the fingerprint
    changed), not part of the key, except its "tags" part: the objects' tags (contact, hero...) change what the checks
    find but are not in the fingerprint, since they change no pixel."""
    out = {"fingerprint": fingerprint, "frames": [span.f0, span.f1],
           "entry": {k: shot.get(k) for k in CHECKED_FIELDS}, "shot_ids": sorted(shot_ids),
           "parts": dict(parts or {}), "legacy": shot_check_key(fingerprint, span, shot)}  # fmt: skip
    if rules:
        out["rules"] = rules
    if parts and parts.get("tags") is not None:
        out["tags"] = parts["tags"]
    return out


def _input_key(inputs: dict) -> str:
    core = {k: v for k, v in inputs.items() if k not in ("parts", "legacy")}
    text = json.dumps(core, sort_keys=True, default=str)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:32]


def _key(now) -> str:
    return _input_key(now) if isinstance(now, dict) else now


def checks_stamp(tl_hash: str, shots: dict, every: int | None = None) -> dict:
    """{"code", "timeline", "every", "shots": {shot id: key}, "inputs": {shot id: shot_check_inputs}}; `shots`
    maps each shot id to its shot_check_inputs (or a plain key)."""
    inputs = {sid: dict(now) for sid, now in shots.items() if isinstance(now, dict)}
    return {"code": checks_code(), "timeline": tl_hash, "every": every,
            "shots": {sid: _key(now) for sid, now in shots.items()}, **({"inputs": inputs} if inputs else {})}  # fmt: skip


def _shot_current(old: dict, sid: str, now) -> bool:
    keys = old.get("shots") if isinstance(old.get("shots"), dict) else {}
    if sid not in keys:
        return False
    if isinstance(now, dict) and not isinstance(old.get("inputs"), dict):  # a stamp from before the inputs
        return keys[sid] == now.get("legacy")
    return keys[sid] == _key(now)


def _what_changed(old: dict, sid: str, now) -> str:
    """Which input of shot `sid`'s checks changed, in words."""
    return "; ".join(_changes(old, sid, now))


def _changes(old: dict, sid: str, now) -> list[str]:
    """Which inputs of shot `sid`'s checks changed, one phrase each (a change every shot shares, like the hard
    rules or the list of shots, is worded the same for each, so require_checks can say it once)."""
    keys = old.get("shots") if isinstance(old.get("shots"), dict) else {}
    if sid not in keys:
        return [f"shot {sid} is new"]
    if not isinstance(old.get("inputs"), dict) and isinstance(now, dict):
        return [(f"shot {sid} changed (the checks ran on an older plugin version that did not record what they "
                 "depend on, so any change to the shot's content or its short.json entry counts)")]  # fmt: skip
    had = (old.get("inputs") or {}).get(sid) if isinstance(old.get("inputs"), dict) else None
    if not isinstance(had, dict) or not isinstance(now, dict):
        return [f"shot {sid} changed"]
    out = []
    if had.get("fingerprint") != now.get("fingerprint"):
        out.append(describe_change(sid, changed_parts(had.get("parts"), now.get("parts"))))
    if had.get("tags") != now.get("tags"):
        out.append(f"shot {sid}'s tags changed (contact, hero, grounded, overlay or surface)")
    if had.get("frames") != now.get("frames"):
        a, b = had.get("frames") or ["?", "?"], now["frames"]
        out.append(f"shot {sid}'s frames changed ({a[0]}-{a[1]}, now {b[0]}-{b[1]})")
    entry_a, entry_b = had.get("entry") or {}, now.get("entry") or {}
    fields = sorted(k for k in set(entry_a) | set(entry_b) if entry_a.get(k) != entry_b.get(k))
    if fields:
        out.append(f"short.json shot {sid} entry changed (fields: {', '.join(fields)})")
    ids_a, ids_b = set(had.get("shot_ids") or ()), set(now.get("shot_ids") or ())
    if ids_a != ids_b:
        diff = [f"added {', '.join(sorted(ids_b - ids_a))}" if ids_b - ids_a else "",
                f"removed {', '.join(sorted(ids_a - ids_b))}" if ids_a - ids_b else ""]  # fmt: skip
        out.append(f"the shots in short.json changed ({'; '.join(d for d in diff if d)})")
    if had.get("rules") != now.get("rules"):
        out.append("the hard rules the scene names are checked against changed (rules_check, cleared words "
                   "included)")  # fmt: skip
    return out or [f"shot {sid} changed"]


def _summarise(changes: dict[str, list[str]], n_shots: int) -> str:
    """Each phrase once, in first-seen order; one that several shots share says which: "(all 7 shots)" or
    "(shots s01, s02)"."""
    seen: dict[str, list[str]] = {}
    for sid, phrases in changes.items():
        for p in phrases:
            seen.setdefault(p, []).append(sid)
    out = []
    for p, sids in seen.items():
        if len(sids) > 1:
            p += f" (all {n_shots} shots)" if len(sids) == n_shots else f" (shots {', '.join(sids)})"
        out.append(p)
    return "; ".join(out)


def read_checks(short_dir: Path) -> dict | None:
    try:
        data = json.loads((Path(short_dir) / "review" / "checks.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def reusable_checks(short_dir: Path, stamp: dict) -> dict[str, list]:
    """{shot id: its findings} from review/checks.json for every shot whose checks still hold for `stamp` (same
    checks code, timing, --every and shot key): `BLENDER check` re-checks only the others. A finding listed once
    for several shots ("shots") goes to each of them; findings the overlap cap held back ("held_back") count too,
    and the row that says how many it held back does not (the cap runs again)."""
    data = read_checks(short_dir) or {}
    old = data.get("stamp")
    if not isinstance(old, dict) or not _code_current(old.get("code")) or stamp.get("code") != checks_code():
        return {}
    if any(old.get(k) != stamp.get(k) for k in ("timeline", "every")):
        return {}
    inputs = stamp.get("inputs") if isinstance(stamp.get("inputs"), dict) else {}
    out: dict[str, list] = {sid: [] for sid, key in (stamp.get("shots") or {}).items()
                            if _shot_current(old, sid, inputs.get(sid, key))}  # fmt: skip
    for f in [*(data.get("findings") or []), *(data.get("held_back") or [])]:
        if not isinstance(f, dict) or "capped" in f:
            continue
        shots = f.get("shots") if isinstance(f.get("shots"), list) else [f.get("shot")]
        for sid in shots:
            if sid in out:
                out[sid].append(f if f.get("shot") == sid else {**f, "shot": sid})
    return out


def require_checks(short_dir: Path, tl_hash: str, shots: dict, command: str = "the final render") -> None:
    """`command` (the final render or edit) needs checks that ran on every shot as it is now (`shots`: {shot id:
    shot_check_inputs}) and on this timing and checks code, and found no errors. A refusal says which input
    changed."""
    again = f"run BLENDER check SHORT again before {command}"
    data = read_checks(short_dir)
    if data is None:
        raise StaleError(f"the scene checks have not run on this build: run BLENDER check SHORT before {command}")
    stamp = data.get("stamp")
    if not isinstance(stamp, dict):
        raise StaleError(f"review/checks.json is from an older plugin version and can't say what it checked, so "
                         f"anything may have changed since the checks ran: {again}")  # fmt: skip
    if stamp.get("timeline") != tl_hash:
        raise StaleError(f"the timing changed since the checks ran (source/timeline.json is not the one they checked): "
                         f"{again}")  # fmt: skip
    if not _code_current(stamp.get("code")):
        raise StaleError(f"the checks code changed since the checks ran (the plugin was updated): {again} (it checks "
                         "every shot once)")  # fmt: skip
    changed = {sid: _changes(stamp, sid, now) for sid, now in shots.items() if not _shot_current(stamp, sid, now)}
    if changed:
        raise StaleError(f"{_summarise(changed, len(shots))} since the checks ran: {again} (it re-checks only those)")
    n = int(data.get("errors") or 0)
    if n:
        raise StaleError(f"review/checks.json has {n} error(s): fix them and run BLENDER check SHORT again before "
                         f"{command}")  # fmt: skip
