"""The glow of a callout (bsa.callouts): the named part lights up from the frame its word is spoken to the end of the
callout. Nothing of the part's own look is touched. Each callout object gets its own copy of each of its materials
(linked by OBJECT, so shared or instanced mesh data and scattered copies stay as they were), and in the copy an
Emission node is added to the original shader with an Add Shader. Only that node's Strength is keyed (0 outside
callouts), so a part that already emits keeps its own emission exactly. The glow is part of the .blend: its nodes and
keys are in the shot fingerprints, so a re-timed callout re-renders only the shots it changes.

bpy is imported inside the functions that need it: strength_keys and glow_settings are pure."""

from __future__ import annotations

import itertools
import json
import math
from pathlib import Path

from bsa import callouts, colour
from bsa.overlays import caption_highlight, hex_colour

DEFAULT_GLOW = 6.0  # Emission Strength at full glow when brand.callout.glow is not set
RAMP_IN = 6  # frames from 0 to full strength at f_on
RAMP_OUT = 10  # frames from full strength to 0 after f_off
PULSE_LOW = 0.5  # the culprit's pulse never goes below this share of the strength

EMISSION = "bsa_callout_emission"
ADD = "bsa_callout_add"
COPY_PROP = "bsa_callout_of"  # on a material copy: the object it belongs to
NOTES_PROP = "bsa_callout_notes"  # on a scene: the notes glow() returned, for the build step to list
SOURCE_PROP = "bsa_callout_source"  # on a material copy: the name of the material it was copied from


# --------------------------------------------------------------------------- the strength over time (pure)


def _at(points: list[tuple[float, float]], frame: float) -> float:
    """Value of the piecewise-linear `points` at `frame`: 0 outside them."""
    if not points or frame < points[0][0] or frame > points[-1][0]:
        return 0.0
    for (f0, v0), (f1, v1) in itertools.pairwise(points):
        if f0 <= frame <= f1:
            return v1 if f1 == f0 else v0 + (v1 - v0) * (frame - f0) / (f1 - f0)
    return points[-1][1]


def _pulse(strength: float, t: float, half: float) -> float:
    """The culprit's strength `t` frames after the ramp: a triangle wave from full down to PULSE_LOW and back, one
    swing each `half` frames."""
    k, frac = divmod(t / half, 1.0)
    drop = strength * (1.0 - PULSE_LOW) * frac
    return strength - drop if int(k) % 2 == 0 else strength * PULSE_LOW + drop


def cue_points(cue: dict, strength: float, fps: float, f_last: int | None = None) -> list[tuple[float, float]]:
    """The glow of one cue as (frame, strength) corners: 0 at f_on, full at f_on + RAMP_IN (a culprit then pulses
    between PULSE_LOW and 1 of the strength, about one cycle a second), held to f_off, then 0 at f_off + RAMP_OUT.
    `f_last` (the shot's last frame) clips it: nothing past it, and 0 on the very next frame, so no glow leaks into
    the shot after."""
    on = cue["f_on"]
    off = max(cue["f_off"], on)
    up = min(on + RAMP_IN, off)
    pts = [(on, 0.0), (up, strength * (up - on) / RAMP_IN)]
    if cue.get("culprit"):
        half = max(fps / 2.0, 1.0)
        for k in range(1, int((off - up) / half) + 1):
            t = up + k * half
            if t < off:
                pts.append((t, _pulse(strength, k * half, half)))
        if off > pts[-1][0]:
            pts.append((off, _pulse(strength, off - up, half) if off > up else pts[-1][1]))
    else:
        pts.append((off, strength))
    pts.append((off + RAMP_OUT, 0.0))
    if f_last is not None and pts[-1][0] > f_last + 1:
        if f_last < on:
            return [(on, 0.0)]
        value = _at(pts, f_last)
        pts = [p for p in pts if p[0] < f_last] + [(f_last, value), (f_last + 1, 0.0)]
    return pts


def strength_keys(cues: list[dict], strength: float, fps: float, f_last: dict | None = None) -> list[tuple[int, float]]:
    """The keys (frame, Emission strength) of an object with several cues: at every corner of every cue, the largest
    value any of them gives there. `f_last` maps a cue's "shot" to that shot's last frame."""
    curves = [cue_points(c, strength, fps, (f_last or {}).get(c.get("shot"))) for c in cues]
    corners = [f for pts in curves for f, _ in pts]
    frames = sorted({math.floor(f) for f in corners} | {math.ceil(f) for f in corners})
    return [(f, round(max(_at(pts, f) for pts in curves), 6)) for f in frames]


# --------------------------------------------------------------------------- settings


def glow_settings(short, settings: dict) -> tuple[float, str]:
    """(Emission strength at full glow, "#RRGGBB" of the glow): brand.callout.glow (else DEFAULT_GLOW), and
    brand.callout.color else the caption highlight colour (which defaults to the accent)."""
    from bsa.settings import brand_for

    block = brand_for(settings, short).get("callout")
    block = block if isinstance(block, dict) else {}
    glow = block.get("glow")
    strength = float(glow) if isinstance(glow, (int, float)) and not isinstance(glow, bool) else DEFAULT_GLOW
    rgba = hex_colour(block.get("color"), 1.0)
    if rgba is None:
        return strength, caption_highlight(short, settings)
    return strength, "#" + "".join(f"{round(c * 255):02X}" for c in rgba[:3])


# --------------------------------------------------------------------------- the nodes (bpy)


def _drop_curves(action, node_name: str) -> None:
    """Remove the F-curves of node `node_name` from `action` (Blender 4 and 5)."""
    prefix = f'nodes["{node_name}"]'
    if hasattr(action, "fcurves"):
        for fc in [fc for fc in action.fcurves if fc.data_path.startswith(prefix)]:
            action.fcurves.remove(fc)
        return
    for layer in action.layers:
        for strip in layer.strips:
            for bag in strip.channelbags:
                for fc in [fc for fc in bag.fcurves if fc.data_path.startswith(prefix)]:
                    bag.fcurves.remove(fc)


def _output(tree):
    outs = [n for n in tree.nodes if n.bl_idname == "ShaderNodeOutputMaterial"]
    return next((n for n in outs if n.is_active_output), outs[0] if outs else None)


def _can_glow(mat) -> bool:
    """Whether `mat` (or the original `mat` is a copy of) has a surface shader into an output to add to."""
    tree = mat.node_tree
    if tree is None:
        return False
    out = _output(tree)
    return out is not None and bool(out.inputs["Surface"].links)


def _own_copy(obj, mat):
    """`obj`'s own copy of `mat` (made once, reused on a re-run), with its own action so keying never touches the
    original's animation."""
    import bpy

    if mat.get(COPY_PROP) == obj.name:
        return mat
    name = f"{mat.name} callout {obj.name}"
    copy = bpy.data.materials.get(name)
    if copy is None or copy.get(COPY_PROP) != obj.name:
        copy = mat.copy()
        copy.name = name
        copy[COPY_PROP] = obj.name
        copy[SOURCE_PROP] = mat.name
        tree = copy.node_tree
        if tree is not None and tree.animation_data is not None and tree.animation_data.action is not None:
            tree.animation_data.action = tree.animation_data.action.copy()
    return copy


def _remove_ours(tree) -> None:
    """Take the glow nodes and keys out and give the output its original shader back."""
    add, emit = tree.nodes.get(ADD), tree.nodes.get(EMISSION)
    if add is not None:
        original = add.inputs[0].links[0].from_socket if add.inputs[0].links else None
        for link in list(add.outputs[0].links):
            target = link.to_socket
            tree.links.remove(link)
            if original is not None:
                tree.links.new(original, target)
        tree.nodes.remove(add)
    if emit is not None:
        tree.nodes.remove(emit)
    if tree.animation_data is not None and tree.animation_data.action is not None:
        _drop_curves(tree.animation_data.action, EMISSION)


def _insert_emission(tree, rgb: tuple, keys: list[tuple[int, float]]) -> None:
    """Add Emission + Add Shader between the surface shader and the output, and key the Emission's Strength."""
    out = _output(tree)
    link = out.inputs["Surface"].links[0]
    source = link.from_socket
    emit = tree.nodes.new("ShaderNodeEmission")
    emit.name = emit.label = EMISSION
    emit.inputs["Color"].default_value = (*rgb, 1.0)
    add = tree.nodes.new("ShaderNodeAddShader")
    add.name = add.label = ADD
    emit.location = (out.location.x - 420, out.location.y - 260)
    add.location = (out.location.x - 200, out.location.y)
    tree.links.remove(link)
    tree.links.new(source, add.inputs[0])
    tree.links.new(emit.outputs["Emission"], add.inputs[1])
    tree.links.new(add.outputs["Shader"], out.inputs["Surface"])
    socket = emit.inputs["Strength"]
    for frame, value in keys:
        socket.default_value = value
        socket.keyframe_insert("default_value", frame=frame)
    socket.default_value = 0.0
    action = tree.animation_data.action if tree.animation_data else None
    if action is not None:
        from bsa.blender.bake import _fcurves

        for fc in _fcurves(action):
            if fc.data_path.startswith(f'nodes["{EMISSION}"]'):
                for kp in fc.keyframe_points:
                    kp.interpolation = "LINEAR"


def _glow_object(obj, rgb: tuple, keys: list) -> list[str]:
    slots = [s for s in obj.material_slots if s.material is not None]
    if not slots:
        return [f"{obj.name}: no material, no glow"]
    notes: list[str] = []
    copies: dict[str, object | None] = {}
    for slot in slots:
        mat = slot.material
        if mat.name not in copies:
            if not _can_glow(mat):
                copies[mat.name] = None
                notes.append(f"{obj.name}: no Principled/output shader, no glow")
            else:
                copy = _own_copy(obj, mat)
                _remove_ours(copy.node_tree)
                _insert_emission(copy.node_tree, rgb, keys)
                copies[mat.name] = copy
        copy = copies[mat.name]
        if copy is None:
            continue
        if slot.link != "OBJECT":
            slot.link = "OBJECT"
        slot.material = copy
    return notes


def glow(short_dir, scene=None) -> list[str]:
    """Key the glow of every callout of the short at `short_dir` into the open .blend; returns notes (a part that
    cannot glow: "Valve body: no Principled/output shader, no glow"). `scene` (default the current one) gives the
    frame rate when the timeline has none. Safe to run again: it replaces its own nodes and keys. StaleError when
    source/callouts.json is missing or was made for another timeline than source/timeline.json (re-narrate or run
    CLI timeline, then rebuild)."""
    import bpy

    from bsa.settings import load_settings
    from bsa.short import load_short
    from bsa.timeline import timeline_from_json, timeline_hash

    sdir = Path(short_dir)
    short = load_short(sdir / "source" / "short.json")
    if not any(callouts.callouts_of(s) for s in short.shots):
        return []
    raw = json.loads((sdir / "source" / "timeline.json").read_text(encoding="utf-8"))
    cues = callouts.load(sdir, timeline_hash(raw))
    tl = timeline_from_json(raw)
    last = {s.id: s.f1 for s in tl.shots}
    scene = scene or bpy.context.scene
    fps = float(tl.fps or scene.render.fps)
    strength, hex_ = glow_settings(short, load_settings())
    rgb = tuple(float(c) for c in colour.srgb_to_linear(colour.hex_to_srgb(hex_)))
    per_object: dict[str, list[dict]] = {}
    for shot_id, rows in cues.items():
        for row in rows:
            if row.get("glow", True) is not False:
                per_object.setdefault(row["near"], []).append({**row, "shot": shot_id})
    notes: list[str] = []
    for near in sorted(per_object):
        obj = bpy.data.objects.get(near)
        if obj is None:
            notes.append(f"{near}: not in the scene, no glow")
            continue
        notes += _glow_object(obj, rgb, strength_keys(per_object[near], strength, fps, last))
    if notes:  # kept on the scene, as the framing notes are: the build step (bsa_blender) names them to the user
        scene[NOTES_PROP] = list(notes)
    elif NOTES_PROP in scene:
        del scene[NOTES_PROP]
    return notes
