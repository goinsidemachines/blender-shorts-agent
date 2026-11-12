"""A fast scan of a built scene for what makes it heavy, run right after the build, before any render or check.
Seconds, not minutes: it reads object and modifier data and the first vertices of each mesh, never the evaluated
geometry."""

from __future__ import annotations

from pathlib import Path

from bsa import heavy
from bsa.blender.checks import live_booleans
from bsa.blender.extremes import fcurves

HASH_VERTS = 16  # the first vertices that tell two meshes apart cheaply


def animated(obj) -> bool:
    """The object itself moves: keyframes, drivers, NLA strips, or a constraint."""
    ad = getattr(obj, "animation_data", None)
    if ad is not None and (fcurves(obj) or len(ad.drivers) or len(ad.nla_tracks)):
        return True
    return len(getattr(obj, "constraints", ())) > 0


def moving_reason(obj) -> str | None:
    """Why the object's evaluated shape changes between frames: it, a parent, or a Boolean operand is animated."""
    chain, parent = [obj], obj.parent
    while parent is not None:
        chain.append(parent)
        parent = parent.parent
    for o in chain:
        if animated(o):
            return "an animated object" if o is obj else f"an animated parent ({o.name})"
    for mod in live_booleans(obj):
        operands = [mod.object] if getattr(mod, "operand_type", "OBJECT") == "OBJECT" else list(
            getattr(getattr(mod, "collection", None), "all_objects", []))
        for op in operands:
            if op is not None and animated(op):
                return f"an animated operand ({op.name})"
    return None


def _copy_key(obj):
    mesh = obj.data
    n = len(mesh.vertices)
    verts = mesh.vertices  # index the first few: never build a list of every vertex of a big mesh
    first = [tuple(round(c, 4) for c in verts[i].co) for i in range(min(n, HASH_VERTS))]
    dims = tuple(round(d, 4) for d in obj.dimensions)
    return (n, dims, tuple(first))


def scan(data, blend: Path | str | None = None) -> list[dict]:
    """[{"kind", "severity", "message"}] for the open file's data and its saved .blend."""
    out: list[dict] = []
    for obj in data.objects:
        if obj.type != "MESH" or not len(obj.modifiers):
            continue
        for mod in live_booleans(obj):
            why = moving_reason(obj)
            if why:
                out.append({"kind": "boolean", "severity": "warning",
                            "message": heavy.boolean_message(obj.name, mod.name, why)})  # fmt: skip
    if blend and Path(blend).is_file():
        mb = Path(blend).stat().st_size / (1024 * 1024)
        if mb > heavy.BIG_BLEND_MB:
            out.append({"kind": "size", "severity": "warning", "message": heavy.size_message(mb)})
    groups: dict[tuple, list[str]] = {}
    for obj in data.objects:
        if obj.type == "MESH" and obj.data is not None and obj.data.users == 1:
            groups.setdefault(_copy_key(obj), []).append(obj.name)
    for names in groups.values():
        if heavy.over_copy_limit(len(names)):
            out.append({"kind": "copies", "severity": "warning", "message": heavy.copies_message(len(names), names[0])})
    for sc in data.scenes:
        frames = sc.frame_end - sc.frame_start + 1
        if sc.render.use_freestyle and frames > heavy.FREESTYLE_LONG_FRAMES:
            out.append({"kind": "freestyle", "severity": "info", "message": f"scene {sc.name}: "
                        + heavy.freestyle_message(frames)})  # fmt: skip
    return out
