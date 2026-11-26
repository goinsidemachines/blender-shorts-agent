"""The callout glow (bsa.blender.callouts): only the named object lights up, at the cue frames, the original shader
and its animation untouched, idempotent, and it survives save and reopen."""

import json
import tempfile
from pathlib import Path

import bpy

from bsa import callouts
from bsa.blender import callouts as glow
from bsa.blender.bake import _fcurves
from bsa.short import Line, Short, Shot, save_short
from bsa.timeline import build_timeline, timeline_hash, to_json

TEXT = "The valve opens and the pump runs."


def _short(culprit=False, hold=None):
    call = {"text": "Valve", "near": "Valve", "on": "valve", "culprit": culprit, **({"hold_s": hold} if hold else {})}
    shots = [Shot("s01", "Hook", "pic", "Scene", min_s=3.0, callouts=[call]),
             Shot("s02", "Next", "pic", "Scene", min_s=2.0)]  # fmt: skip
    lines = [Line("l01", TEXT, "s01"), Line("l02", "Nothing else.", "s02")]
    return Short(slug="t", title="T", length_s=10, lines=lines, shots=shots)


def _sdir(short):
    sdir = Path(tempfile.mkdtemp(prefix="bsa-glow-"))
    (sdir / "source").mkdir()
    save_short(short, sdir / "source" / "short.json")
    tl = build_timeline(short, {"l01": 3.0, "l02": 2.0})
    (sdir / "source" / "timeline.json").write_text(json.dumps(to_json(tl)), encoding="utf-8")
    callouts.write(sdir, short, tl, None)
    return sdir, tl


def _cue(sdir, tl):
    return callouts.load(sdir, timeline_hash(json.loads(json.dumps(to_json(tl)))))["s01"][0]


def _scene(emission_keyed=False):
    """Valve and Twin share one mesh and one material: a Principled, or (emission_keyed) an Emission shader straight
    into the output with its Strength keyed."""
    bpy.ops.wm.read_factory_settings(use_empty=True)
    sc = bpy.context.scene
    mat = bpy.data.materials.new("Steel")
    mat.use_nodes = True
    tree = mat.node_tree
    if emission_keyed:
        out = next(n for n in tree.nodes if n.bl_idname == "ShaderNodeOutputMaterial")
        for n in list(tree.nodes):
            if n.name != out.name:
                tree.nodes.remove(n)
        em = tree.nodes.new("ShaderNodeEmission")
        em.name = "Own"
        tree.links.new(em.outputs[0], out.inputs["Surface"])
        for f, v in ((1, 2.0), (50, 4.0)):
            em.inputs["Strength"].default_value = v
            em.inputs["Strength"].keyframe_insert("default_value", frame=f)
    me = bpy.data.meshes.new("Shared")
    me.from_pydata([(0, 0, 0), (1, 0, 0), (1, 1, 0)], [], [(0, 1, 2)])
    me.materials.append(mat)
    for name in ("Valve", "Twin"):
        sc.collection.objects.link(bpy.data.objects.new(name, me))
    return sc


def _emit_curve(mat):
    tree = mat.node_tree
    assert tree.animation_data and tree.animation_data.action
    prefix = f'nodes["{glow.EMISSION}"]'
    curves = [fc for fc in _fcurves(tree.animation_data.action) if fc.data_path.startswith(prefix)]
    assert len(curves) == 1, len(curves)
    return curves[0]


def _valve_material():
    return bpy.data.objects["Valve"].material_slots[0].material


def test_glow_keys_only_the_callout_object():
    sc = _scene()
    sdir, _ = _sdir(_short())
    assert glow.glow(sdir, sc) == []
    valve, twin = bpy.data.objects["Valve"], bpy.data.objects["Twin"]
    assert valve.material_slots[0].link == "OBJECT" and valve.material_slots[0].material.name == "Steel callout Valve"
    assert twin.material_slots[0].link == "DATA" and twin.material_slots[0].material.name == "Steel"
    assert glow.EMISSION not in bpy.data.materials["Steel"].node_tree.nodes
    assert bpy.data.materials["Steel"].node_tree.animation_data is None
    ev = twin.evaluated_get(bpy.context.evaluated_depsgraph_get())
    assert glow.EMISSION not in ev.material_slots[0].material.node_tree.nodes


def test_glow_ramps_at_cue_frames():
    sc = _scene()
    sdir, tl = _sdir(_short(hold=1.0))
    glow.glow(sdir, sc)
    cue = _cue(sdir, tl)
    fc = _emit_curve(_valve_material())
    full = glow.DEFAULT_GLOW
    assert fc.evaluate(cue["f_on"] - 1) == 0.0
    assert abs(fc.evaluate(cue["f_on"] + 6) - full) < 1e-4
    assert abs(fc.evaluate(cue["f_off"]) - full) < 1e-4
    assert fc.evaluate(cue["f_off"] + 10) == 0.0
    assert fc.evaluate(tl.shots[0].f1 + 1) == 0.0  # nothing leaks into the next shot


def test_glow_clipped_at_the_shot_end():
    sc = _scene()
    sdir, tl = _sdir(_short())  # no hold_s: to the end of the shot
    glow.glow(sdir, sc)
    fc = _emit_curve(_valve_material())
    assert fc.evaluate(tl.shots[0].f1) > 0.0
    assert fc.evaluate(tl.shots[1].f0) == 0.0


def test_existing_emission_untouched():
    sc = _scene(emission_keyed=True)
    original = bpy.data.materials["Steel"]
    before = len(_fcurves(original.node_tree.animation_data.action))
    sdir, _ = _sdir(_short())
    glow.glow(sdir, sc)
    assert len(_fcurves(original.node_tree.animation_data.action)) == before  # the original's keys are as they were
    copy = _valve_material()
    own = next(fc for fc in _fcurves(copy.node_tree.animation_data.action) if fc.data_path.startswith('nodes["Own"]'))
    orig = next(fc for fc in _fcurves(original.node_tree.animation_data.action) if fc.data_path.startswith('nodes["Own"]'))
    for f in (1, 10, 25, 50, 90):
        assert own.evaluate(f) == orig.evaluate(f)
    assert copy.node_tree.nodes[glow.ADD].inputs[0].links[0].from_node.name == "Own"


def test_glow_is_idempotent():
    sc = _scene()
    sdir, tl = _sdir(_short())
    glow.glow(sdir, sc)
    glow.glow(sdir, sc)
    copy = _valve_material()
    assert [n.name for n in copy.node_tree.nodes].count(glow.EMISSION) == 1
    assert len([n for n in copy.node_tree.nodes if n.bl_idname == "ShaderNodeAddShader"]) == 1
    assert len(_emit_curve(copy).keyframe_points) == len(glow.strength_keys([_cue(sdir, tl)], glow.DEFAULT_GLOW, tl.fps))
    assert sum(1 for m in bpy.data.materials if "callout" in m.name) == 1


def test_glow_survives_save_and_reopen():
    sc = _scene()
    sdir, tl = _sdir(_short())
    glow.glow(sdir, sc)
    cue = _cue(sdir, tl)
    path = str(Path(tempfile.mkdtemp(prefix="bsa-glow-")) / "g.blend")
    bpy.ops.wm.save_as_mainfile(filepath=path)
    bpy.ops.wm.open_mainfile(filepath=path)
    assert bpy.data.objects["Valve"].material_slots[0].link == "OBJECT"
    assert abs(_emit_curve(_valve_material()).evaluate(cue["f_on"] + 6) - glow.DEFAULT_GLOW) < 1e-4


def test_culprit_pulses():
    sc = _scene()
    sdir, tl = _sdir(_short(culprit=True))
    glow.glow(sdir, sc)
    cue = _cue(sdir, tl)
    fc = _emit_curve(_valve_material())
    values = [fc.evaluate(f) for f in range(cue["f_on"] + 6, min(cue["f_off"], cue["f_on"] + 60))]
    assert max(values) > min(values) >= glow.DEFAULT_GLOW * glow.PULSE_LOW - 1e-4


def test_part_without_a_shader_gets_a_note():
    sc = _scene()
    mat = bpy.data.materials["Steel"]
    for n in list(mat.node_tree.nodes):
        mat.node_tree.nodes.remove(n)
    sdir, _ = _sdir(_short())
    assert glow.glow(sdir, sc) == ["Valve: no Principled/output shader, no glow"]
    assert list(sc[glow.NOTES_PROP]) == ["Valve: no Principled/output shader, no glow"]  # the build step lists it


def test_changed_cue_frames_change_the_shot_fingerprint():
    from bsa.blender.fingerprint import fingerprints

    sc = _scene()
    sdir, tl = _sdir(_short(hold=1.0))
    glow.glow(sdir, sc)
    span = tl.shots[0]
    first = fingerprints([(sc, [span])])[0][span.id]
    again = fingerprints([(sc, [span])])[0][span.id]
    assert first == again
    short = _short(hold=2.0)  # re-timed: another f_off
    (sdir / "source" / "short.json").unlink()
    save_short(short, sdir / "source" / "short.json")
    callouts.write(sdir, short, tl, None)
    glow.glow(sdir, sc)
    assert fingerprints([(sc, [span])])[0][span.id] != first
