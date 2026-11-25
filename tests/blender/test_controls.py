"""Animated geometry-nodes values on control Empties (bsa.blender.controls): signed, re-evaluated every frame, and
carried through the plugin's own frame render (render_range) after the blend is saved and reopened."""

import tempfile
from pathlib import Path

import bpy

from bsa.blender import controls, render
from bsa.blender.bake import _fcurves

FRAMES = [1, 2, 3, 4, 5]
CHANNELS = ["Dx", "Dy", "Dz", "Dw"]  # four channels: two Empties
VALUES = {
    "Dx": [-1.0, -2.0, -0.5, 0.0, 1.5],
    "Dy": [0.5, -0.5, -1.5, 2.0, -2.0],
    "Dz": [-3.0, -2.0, -1.0, 0.0, 1.0],
    "Dw": [1.0, -1.0, 1.0, -1.0, 1.0],
}


def _scene():
    """A triangle moved by a geometry-nodes group by (Dx, Dy, Dz + Dw), all four read from control Empties."""
    bpy.ops.wm.read_factory_settings(use_empty=True)
    sc = bpy.context.scene
    sc.frame_start, sc.frame_end = 1, len(FRAMES)
    ng = bpy.data.node_groups.new("Move", "GeometryNodeTree")
    it = ng.interface
    it.new_socket("Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
    it.new_socket("Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    cs = controls.ControlSet("Tri", CHANNELS)
    out = cs.wire(ng)
    nodes, links = ng.nodes, ng.links
    gin = nodes.new("NodeGroupInput")
    gout = nodes.new("NodeGroupOutput")
    add = nodes.new("ShaderNodeMath")
    comb = nodes.new("ShaderNodeCombineXYZ")
    tr = nodes.new("GeometryNodeTransform")
    links.new(out["Dz"], add.inputs[0])
    links.new(out["Dw"], add.inputs[1])
    links.new(out["Dx"], comb.inputs[0])
    links.new(out["Dy"], comb.inputs[1])
    links.new(add.outputs[0], comb.inputs[2])
    links.new(gin.outputs["Geometry"], tr.inputs["Geometry"])
    links.new(comb.outputs[0], tr.inputs["Translation"])
    links.new(tr.outputs[0], gout.inputs["Geometry"])
    me = bpy.data.meshes.new("Tri")
    me.from_pydata([(0, 0, 0), (1, 0, 0), (1, 1, 0)], [], [(0, 1, 2)])
    ob = bpy.data.objects.new("Tri", me)
    sc.collection.objects.link(ob)
    mod = ob.modifiers.new("Move", "NODES")
    mod.node_group = ng
    cs.bind(mod, ng)
    for name in CHANNELS:
        cs.key(name, FRAMES, VALUES[name])
    return sc, ob, cs


def _vertex0(ob):
    e = ob.evaluated_get(bpy.context.evaluated_depsgraph_get())
    return tuple(round(c, 4) for c in e.data.vertices[0].co)


def _expect(i):
    return (VALUES["Dx"][i], VALUES["Dy"][i], round(VALUES["Dz"][i] + VALUES["Dw"][i], 4))


def test_four_channels_use_two_empties_and_signs_survive():
    sc, ob, cs = _scene()
    assert len(cs.empties) == 2
    for i, f in enumerate(FRAMES):
        sc.frame_set(f)
        assert _vertex0(ob) == _expect(i), (f, _vertex0(ob), _expect(i))
    sc.frame_set(1)
    assert _vertex0(ob)[0] < 0 and _vertex0(ob)[2] == -2.0


def test_values_between_keys_are_linear_and_only_location_is_keyed():
    sc, _ob, cs = _scene()
    ctl = controls.control("Sparse")
    controls.key_channel(ctl, 0, [1, 5], [-2.0, 2.0])  # a sign change between two keys
    for f, want in ((1, -2.0), (2, -1.0), (3, 0.0), (5, 2.0)):
        sc.frame_set(f)
        assert abs(ctl.location[0] - want) < 1e-5, (f, ctl.location[0])
    paths = {(f.data_path, f.array_index) for f in _fcurves(cs.empties[0].animation_data.action)}
    assert paths == {("location", 0), ("location", 1), ("location", 2)}
    assert tuple(cs.empties[0].scale) == (1.0, 1.0, 1.0)


def test_saved_blend_reopened_and_rendered_by_render_range_animates_every_frame():
    sc, _ob, _cs = _scene()
    cam = bpy.data.objects.new("Cam", bpy.data.cameras.new("Cam"))
    sc.collection.objects.link(cam)
    cam.data.type = "ORTHO"
    cam.data.ortho_scale = 14
    cam.location = (0, -10, 10)
    cam.rotation_euler = (0.785398, 0, 0)
    sc.camera = cam
    sc.render.resolution_x = sc.render.resolution_y = 200
    sc.render.film_transparent = True
    tmp = Path(tempfile.mkdtemp())
    blend = tmp / "t.blend"
    bpy.ops.wm.save_as_mainfile(filepath=str(blend))
    bpy.ops.wm.open_mainfile(filepath=str(blend))  # what a worker Blender does with build.py's saved blend
    sc = bpy.context.scene
    render.render_range(sc, 1, len(FRAMES), tmp / "frames", "still", percent=100, engine="eevee")
    sx = []
    for f in FRAMES:
        im = bpy.data.images.load(str(tmp / "frames" / f"{f:04d}.png"))
        w, h = im.size
        px = im.pixels[:]
        xs = [x for y in range(h) for x in range(w) if px[(y * w + x) * 4 + 3] > 0.5]
        ys = [y for y in range(h) for x in range(w) if px[(y * w + x) * 4 + 3] > 0.5]
        assert xs, f"frame {f}: nothing rendered"
        sx.append((sum(xs) / len(xs), sum(ys) / len(ys)))
        bpy.data.images.remove(im)
    # Dx goes -1, -2, -0.5, 0, 1.5: the triangle's screen x follows it (and is not stuck on frame 1)
    mx = [p[0] for p in sx]
    assert mx[1] < mx[0] and mx[2] > mx[1] and mx[3] > mx[2] and mx[4] > mx[3], mx
    assert len({round(p[1], 1) for p in sx}) > 3, sx  # the height moves too
