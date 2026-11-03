"""The look preview: three stills of the short (its first shot, a middle one, its last) in the chosen style and set,
each with the kit's cards for that frame on top as the edit draws them, plus the kit's sample sheet, into
stills/look/ and stills/look.json.

A style or set other than the built one is put on the open scenes in memory only: the .blend is never saved, and the
caller reverts the file afterwards. The engine is the caller's (the user's choice), handed on to the still render and
never set here; the time per frame is measured, an estimate only."""

from __future__ import annotations

import contextlib
import dataclasses
import json
import os
import shutil
import tempfile
import time
import uuid
from pathlib import Path

import bpy

from bsa import looks
from bsa.blender import cards as blender_cards
from bsa.blender.stills import render_still
from bsa.kitlayout import KitOverlay
from bsa.layout import H, W

LOOK_JSON = "look.json"  # in stills/
LOOK_DIR = "look"  # stills/look/<shot>.jpg and the kit sheet
SHEET = "kit.png"
SCENE = "LookFrame"


def look_shots(short) -> list:
    """The shots the preview shows: the first, the middle (`len // 2`) and the last, each once."""
    shots = list(short.shots)
    if not shots:
        return []
    picked = [shots[0], shots[len(shots) // 2], shots[-1]]
    return list({s.id: s for s in picked}.values())


@contextlib.contextmanager
def no_new_fonts():
    """Fonts loaded meanwhile (the cards' text strips load theirs) are removed again once nothing uses them."""
    had = set(bpy.data.fonts.keys())
    try:
        yield
    finally:
        for name in [n for n in bpy.data.fonts.keys() if n not in had]:  # noqa: SIM118 - ID collection, not a dict
            font = bpy.data.fonts.get(name)
            if font is not None and font.users == 0:
                bpy.data.fonts.remove(font)


def compose(still_png: Path, ov: KitOverlay, frame: int, out_jpg: Path, cache_dir: Path) -> Path:
    """`still_png` (any 9:16 image) as a full-frame image strip under the kit's cards for `frame` (cards.add_overlay,
    as the edit places them), rendered as one 1080x1920 JPEG (quality 85) to `out_jpg`. A one-frame sequencer scene,
    removed afterwards: no scene strip, no 3D render, no engine. The card images are cached in `cache_dir`."""
    still_png, out_jpg, cache_dir = Path(still_png), Path(out_jpg), Path(cache_dir)
    out_jpg.parent.mkdir(parents=True, exist_ok=True)
    cache_dir.mkdir(parents=True, exist_ok=True)
    scene = bpy.data.scenes.new(f"{SCENE}-{uuid.uuid4().hex[:8]}")
    try:
        with no_new_fonts():
            r = scene.render
            r.resolution_x, r.resolution_y, r.resolution_percentage = W, H, 100
            r.use_sequencer, r.use_compositing = True, False
            scene.view_settings.view_transform = "Standard"
            scene.view_settings.look = "None"
            scene.frame_start = scene.frame_end = scene.frame_current = frame
            seq = scene.sequence_editor_create()
            still = seq.strips.new_image("Still", str(still_png), 1, frame, fit_method="FIT")
            still.frame_final_duration = 1
            blender_cards.add_overlay(seq, ov, cache_dir)
            if hasattr(r.image_settings, "media_type"):
                r.image_settings.media_type = "IMAGE"
            r.image_settings.file_format = "JPEG"
            r.image_settings.color_mode = "RGB"
            r.image_settings.quality = 85
            # A one-frame animation render: a still render of a scene that is not the context's does not evaluate its
            # keyframes in Blender 5.x, so moving near cards and the progress fill would show their last key.
            with tempfile.TemporaryDirectory(prefix="bsa-look-", ignore_cleanup_errors=True) as tmp:
                r.use_file_extension = True
                r.filepath = str(Path(tmp) / "f_")
                bpy.ops.render.render(animation=True, scene=scene.name)
                made = next(Path(tmp).glob("f_*"), None)
                if made is None or made.stat().st_size == 0:
                    raise RuntimeError(f"the look frame for frame {frame} was not written")
                part = out_jpg.with_name(out_jpg.name + ".part")  # copied, then swapped in: tmp may be another drive
                shutil.copyfile(made, part)
                os.replace(part, out_jpg)
    finally:
        bpy.data.scenes.remove(scene)
    return out_jpg


# --- the scenes in another style or set (in memory only) ---------------------------------------------------------


def _size(scene) -> float:
    from bsa.blender import sets, styles

    if sets.SIZE_KEY in scene:
        return float(scene[sets.SIZE_KEY])
    args = scene.get("bsa_style_args")
    if args is not None:
        return float(args["size"])
    return float(styles._bounds(styles._kit_meshes(scene))[1])


def restyle(scene, kit, short, tl, sdir: Path, style: str | None, set_name: str | None) -> tuple[bool, list[str]]:
    """Show `scene` with `set_name` and `style` where they differ from what it was built with (None: as built), in
    memory only (sets.base, which clears the built set, + dress(save=False), then the style applied once). Returns
    (changed, warnings)."""
    from bsa.blender import sets, styles

    warnings: list[str] = []
    changed = False
    built_set = str(scene.get("bsa_set") or "none")
    if set_name is not None and set_name != built_set:
        size = _size(scene)
        # The set's base and dress restyle a styled scene as they go; the style is applied once, below, instead.
        args = scene.get("bsa_style_args")
        args = args.to_dict() if hasattr(args, "to_dict") else args
        if args is not None:
            del scene["bsa_style_args"]
        try:
            sets.base(scene, kit, set_name, size)  # "none": the set taken away, a paper backdrop back
            if set_name != "none":
                got = sets.dress(scene, kit, short, tl, sdir, save=False, replace=True)  # sets.json: the built set's
                warnings += got.get("warnings", [])
        finally:
            if args is not None:
                scene["bsa_style_args"] = args
        changed = True
    built_style = scene.get("bsa_style")
    want = style or (str(built_style) if built_style else kit.style)
    if changed or want != built_style:
        if "bsa_style_args" in scene:
            got = styles.reapply(scene, kit, style=want)
        else:
            got = styles.apply(scene, kit, style=want)
        warnings += got.get("warnings", [])
        changed = True
    return changed, warnings


# --- the near labels' anchors, worked out in memory --------------------------------------------------------------


def _anchors(short, tl, scenes: dict, shown: set[str], warnings: list) -> tuple:
    """(the short with only the shown shots' near labels, anchors, label_heroes) as the edit has them from
    anchors.json, projected now for the shown shots (nothing is written)."""
    from bsa.blender.anchors import hero_boxes, heroes, project
    from bsa.blender.bake import category
    from bsa.short import label_near

    spans = {s.id: s for s in tl.shots}
    anchors: dict[str, dict] = {}
    label_heroes: dict[str, dict] = {}
    shots = []
    for shot in short.shots:
        near = [label_near(lb) for lb in shot.labels if label_near(lb)]
        if not near:
            shots.append(shot)
            continue
        if shot.id not in shown:
            shots.append(dataclasses.replace(shot, labels=[lb for lb in shot.labels if not label_near(lb)]))
            continue
        scene, span = scenes[shot.id], spans[shot.id]
        keep = []
        for name in dict.fromkeys(near):
            try:
                entry = project(scene, [name], span.f0, span.f1)[name]
            except ValueError as exc:
                warnings.append(f"shot {shot.id}: the label near {name!r} is left out of the look ({exc})")
                continue
            entry["category"] = category(scene.objects.get(name))
            anchors.setdefault(shot.id, {})[name] = entry
            keep.append(name)
        labels = [lb for lb in shot.labels if not label_near(lb) or label_near(lb) in keep]
        shots.append(dataclasses.replace(shot, labels=labels))
        try:
            label_heroes[shot.id] = hero_boxes(scene, heroes(scene, shot.id), span.f0, span.f1)
        except ValueError:
            pass  # labels placed without keeping off the heroes, as the edit does without them
    return dataclasses.replace(short, shots=shots), anchors, label_heroes


# --- the preview ------------------------------------------------------------------------------------------------


def _estimates(sdir: Path) -> dict | None:
    try:
        data = json.loads((sdir / "review" / "estimate.json").read_text(encoding="utf-8"))
        return {"eevee_s": float(data["eevee_s"]), "cycles_s": float(data["cycles_s"])}
    except (OSError, ValueError, KeyError, TypeError):
        return None


def first_look(sdir: Path) -> bool:
    """True when no other short in the workspace has a look preview yet."""
    return not any(p.parent.parent != sdir for p in sdir.parent.glob(f"*/stills/{LOOK_JSON}"))


def preview(sdir: Path, short, tl, kit, scenes: dict, still_frames: dict, engine: str | None, style: str | None,
            set_name: str | None) -> dict:
    """Render the look preview of the short in `sdir`: `scenes` {shot id: scene}, `still_frames` {shot id: frame},
    `engine` the user's (None: each scene's own), `style` / `set_name` None for the built ones. Writes
    stills/look/<shot>.jpg, stills/look/kit.png and stills/look.json (atomic); returns what look.json holds, plus
    "changed" (whether any scene was put in another style or set, so the caller reverts the file)."""
    from bsa import cards, fonts
    from bsa.review import store

    sdir = Path(sdir)
    look_dir = sdir / "stills" / LOOK_DIR
    look_dir.mkdir(parents=True, exist_ok=True)
    cache_dir = sdir / "cards"
    warnings: list[str] = []
    shown = look_shots(short)
    first_scene = scenes[shown[0].id]
    built = {"style": first_scene.get("bsa_style"), "set": str(first_scene.get("bsa_set") or "none")}
    built["style"] = str(built["style"]) if built["style"] else None
    first = first_look(sdir)
    frames, seconds, changed = [], [], False
    used_engine = engine
    with no_new_fonts():
        card_short, anchors, label_heroes = _anchors(short, tl, scenes, {s.id for s in shown}, warnings)
        font, note = cards.card_font_and_note(kit, fonts.font_caches(sdir))
        if note:
            warnings.append(note)
        from bsa import kitlayout

        ov = kitlayout.build(card_short, tl, kit, font, anchors, label_heroes)
        warnings += ov.notes
        done: set[str] = set()
        for shot in shown:
            scene = scenes[shot.id]
            if scene.name not in done:
                did, notes = restyle(scene, kit, short, tl, sdir, style, set_name)
                changed |= did
                warnings += notes
                done.add(scene.name)
            frame = still_frames[shot.id]
            out = look_dir / f"{shot.id}.jpg"
            with tempfile.TemporaryDirectory(prefix="bsa-look-still-", ignore_cleanup_errors=True) as tmp:
                still = Path(tmp) / f"{shot.id}.png"
                t0 = time.perf_counter()
                render_still(scene, frame, still, engine=engine, file_format="PNG")
                seconds.append(time.perf_counter() - t0)
                used_engine = used_engine or ("cycles" if scene.render.engine == "CYCLES" else "eevee")
                compose(still, ov, frame, out, cache_dir)
            frames.append({"shot": shot.id, "frame": frame, "file": f"{LOOK_DIR}/{out.name}"})
        warnings += blender_cards.render_sample(kit, look_dir / SHEET, cache_dir)
    listed = {Path(f["file"]).name for f in frames}
    for old in look_dir.glob("*.jpg"):  # frames of shots an earlier run showed and this one doesn't
        if old.name not in listed:
            old.unlink(missing_ok=True)
    data = {
        "style": style or built["style"] or kit.style,
        "set": set_name or built["set"],
        "built": built,
        "frames": frames,
        "sheet": f"{LOOK_DIR}/{SHEET}",
        "swatches": looks.swatches(kit),  # the kit colours the frames and the sheet were rendered with
        "engine": used_engine,
        "seconds_per_frame": round(sum(seconds) / len(seconds), 2) if seconds else None,
        "estimates": _estimates(sdir),
        "first": first,
        "warnings": list(dict.fromkeys(warnings)),
    }
    store.update_json(sdir / "stills" / LOOK_JSON, {}, lambda _old: data)
    return {**data, "changed": changed}
