import math
import tempfile
from itertools import pairwise
from pathlib import Path

import bmesh
import bpy
from mathutils import Vector

from bsa.blender import bake, checks, look
from bsa.timeline import Span, Timeline

N = 30


def _material(obj, name="mat"):
    mat = bpy.data.materials.get(name) or bpy.data.materials.new(name)
    obj.data.materials.append(mat)
    return mat


def _scene():
    """9:16 camera at y=-12 looking along +Y at a 20 m floor (with a material) at z=0."""
    bpy.ops.wm.read_factory_settings(use_empty=True)
    sc = bpy.context.scene
    sc.frame_start, sc.frame_end = 1, N
    sc.render.resolution_x, sc.render.resolution_y = 1080, 1920
    cam = bpy.data.objects.new("Cam", bpy.data.cameras.new("Cam"))
    sc.collection.objects.link(cam)
    cam.location = (0, -12, 1.5)
    cam.rotation_euler = (math.radians(90), 0, 0)
    sc.camera = cam
    bpy.ops.mesh.primitive_plane_add(size=20, location=(0, 0, 0))
    bpy.context.object.name = "Floor"
    _material(bpy.data.objects["Floor"], "floor")
    return sc


def _timeline():
    return Timeline(30, N, [Span("s1", 1, N)], [], [])


def _cube(name, location, size=1.0, material=True):
    bpy.ops.mesh.primitive_cube_add(size=size, location=location)
    obj = bpy.context.object
    obj.name = name
    if material:
        _material(obj)
    return obj


def _found(sc, check, obj=None, severity=None):
    return [
        f
        for f in checks.run_checks(sc, _timeline())
        if f.check == check and (obj is None or f.obj == obj) and (severity is None or f.severity == severity)
    ]


def _move_x(obj, values):
    bake.keyframe_series(obj, "location", 0, values)


def test_missing_material_found():
    sc = _scene()
    _cube("Bare", (0, 0, 0.5), material=False)
    found = _found(sc, "materials", "Bare", "error")
    assert len(found) == 1, found
    assert not _found(sc, "materials", "Floor")


def test_missing_image_found():
    sc = _scene()
    cube = _cube("Textured", (0, 0, 0.5), material=False)
    mat = _material(cube, "textured")
    mat.use_nodes = True
    nodes, links = mat.node_tree.nodes, mat.node_tree.links
    tex = nodes.new("ShaderNodeTexImage")
    img = bpy.data.images.new("gone", 4, 4)
    img.source = "FILE"
    img.filepath = str(Path(tempfile.mkdtemp()) / "missing_texture.png")
    tex.image = img
    bsdf = next(n for n in nodes if n.type == "BSDF_PRINCIPLED")
    links.new(tex.outputs["Color"], bsdf.inputs["Base Color"])
    found = _found(sc, "images", "Textured", "error")
    assert len(found) == 1 and "missing_texture.png" in found[0].message, found
    good = Path(tempfile.mkdtemp()) / "tex.png"
    ok = bpy.data.images.new("ok", 4, 4)
    ok.filepath_raw = str(good)
    ok.file_format = "PNG"
    ok.save()
    tex.image = bpy.data.images.load(str(good))
    assert not _found(sc, "images")


def test_inside_out_found():
    sc = _scene()
    cube = _cube("Flipped", (0, 0, 0.5))
    bm = bmesh.new()
    bm.from_mesh(cube.data)
    bmesh.ops.reverse_faces(bm, faces=bm.faces)
    bm.to_mesh(cube.data)
    bm.free()
    assert len(_found(sc, "normals", "Flipped", "error")) == 1
    assert not _found(sc, "normals", "Floor")


def test_zfighting_found():
    sc = _scene()
    for name, size, x in (("PlaneA", 2.0, 0.0), ("PlaneB", 1.0, 0.4)):
        bpy.ops.mesh.primitive_plane_add(size=size, location=(x, 0, 0.5))
        bpy.context.object.name = name
        _material(bpy.context.object)
    found = _found(sc, "overlap", severity="error")
    assert len(found) == 1 and "z-fighting" in found[0].message, found
    assert "PlaneA" in found[0].message and "PlaneB" in found[0].message


def test_contact_tag_allows_touching():
    sc = _scene()
    a = _cube("Left", (0, 0, 0.5))
    right = _cube("Right", (0.7, 0.2, 0.6))  # 0.3 m inside Left, no faces coplanar
    found = _found(sc, "overlap", severity="error")
    assert len(found) == 1 and "interpenetrat" in found[0].message, found
    assert "30 cm inside" in found[0].message, found  # how deep, not only that they touch
    right.location = (0.7, 0, 0.5)  # flush and axis-aligned: no face pair crosses, tops z-fight
    messages = " ".join(f.message for f in _found(sc, "overlap", severity="error"))
    assert "interpenetrat" in messages and "z-fighting" in messages, messages
    bake.tag(a, "contact:Right:0.35")  # deep by design, and says how deep it may go
    assert not _found(sc, "overlap")


def test_contact_tag_is_depth_limited():
    """contact:<other> allows a shallow overlap (a ball sunk into its cradle), not a part buried in another."""
    sc = _scene()
    bake.tag(_cube("Left", (0, 0, 0.5)), "contact:Right")
    right = _cube("Right", (0.98, 0.2, 0.6))  # 2 cm in: within the default allowance (5 cm for 1 m parts)
    assert not _found(sc, "overlap")
    right.location = (0.7, 0.2, 0.6)  # 30 cm in
    found = _found(sc, "overlap", severity="error")
    assert len(found) == 1, found
    assert "30 cm inside" in found[0].message and "contact:Right" in found[0].message, found
    right.location = (0.92, 0.2, 0.6)  # 8 cm in: past 5 cm
    assert len(_found(sc, "overlap", severity="error")) == 1


def test_contact_depth_limit_scales_with_thin_parts():
    """A 1 cm rod gets 1 mm of allowed depth (10% of its thickness, at least 2 mm), so a rod pushed 4 cm into a
    block is reported even though it is tagged."""
    sc = _scene()
    _cube("Block", (0, 0, 0.5))
    bpy.ops.mesh.primitive_cylinder_add(radius=0.005, depth=0.5, location=(0, 0, 1.21))  # bottom 4 cm into Block
    rod = bpy.context.object
    rod.name = "Rod"
    _material(rod)
    bake.tag(rod, "contact:Block")
    found = _found(sc, "overlap", severity="error")
    assert len(found) == 1 and "'Rod' is 4 cm inside 'Block'" in found[0].message, found
    rod.location = (0, 0, 1.2495)  # 0.5 mm in: a touch
    assert not _found(sc, "overlap")


def test_only_direct_parent_child_is_exempt_and_only_shallow():
    sc = _scene()
    frame = bpy.data.objects.new("Assembly", None)  # an empty that carries the whole machine
    sc.collection.objects.link(frame)
    a = _cube("SiblingA", (-3, 0, 0.5))
    b = _cube("SiblingB", (-2.7, 0.2, 0.6))  # 30 cm into SiblingA
    a.parent = b.parent = frame
    found = _found(sc, "overlap", severity="error")
    assert len(found) == 1 and "SiblingA" in found[0].message and "SiblingB" in found[0].message, found
    bpy.data.objects.remove(b)
    child = _cube("Child", (-2.02, 0.2, 0.6))  # 2 cm into its parent: allowed
    child.parent = a
    child.matrix_parent_inverse = a.matrix_world.inverted()
    assert not _found(sc, "overlap")
    grandchild = _cube("Grandchild", (-3.3, 0.1, 0.55))  # 30 cm into its grandparent
    grandchild.parent = child
    grandchild.matrix_parent_inverse = child.matrix_world.inverted()
    found = _found(sc, "overlap", severity="error")
    assert any("'Grandchild'" in f.message and "'SiblingA'" in f.message for f in found), found
    bpy.data.objects.remove(grandchild)
    child.location = (-2.7, 0.2, 0.6)  # 30 cm into its parent: too deep even for a parent/child pair
    found = _found(sc, "overlap", severity="error")
    assert len(found) == 1 and "30 cm inside" in found[0].message and "parent" in found[0].message, found


def _two_cubes_mesh(name, offset):
    """One object made of two loose unit-cube parts, the second `offset` metres along X from the first."""
    mesh = bpy.data.meshes.new(name)
    bm = bmesh.new()
    for x in (0.0, offset):
        geom = bmesh.ops.create_cube(bm, size=1.0)
        bmesh.ops.translate(bm, vec=(x, 0, 0.5), verts=geom["verts"])
    bm.to_mesh(mesh)
    bm.free()
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(obj)
    _material(obj)
    return obj


def test_hero_loose_parts_passing_into_each_other_found():
    sc = _scene()
    joined = _two_cubes_mesh("Joined", 0.7)  # the second part is 30 cm inside the first
    assert not _found(sc, "self_overlap")  # only heroes are checked
    bake.tag(joined, "hero")
    found = _found(sc, "self_overlap", "Joined", "warning")
    assert len(found) == 1 and "30 cm inside" in found[0].message, found
    bpy.data.objects.remove(joined)
    bake.tag(_two_cubes_mesh("Touching", 1.0), "hero")  # parts that only touch face to face
    assert not _found(sc, "self_overlap")


def _boxes_mesh(name, boxes):
    """One object made of loose box parts: boxes are (centre, (sx, sy, sz))."""
    mesh = bpy.data.meshes.new(name)
    bm = bmesh.new()
    for centre, size in boxes:
        geom = bmesh.ops.create_cube(bm, size=1.0)
        bmesh.ops.scale(bm, vec=size, verts=geom["verts"])
        bmesh.ops.translate(bm, vec=centre, verts=geom["verts"])
    bm.to_mesh(mesh)
    bm.free()
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(obj)
    _material(obj)
    return obj


def _shape_key_move(obj, box_lo, box_hi, offset):
    """A shape key moving the vertices inside the box [box_lo, box_hi] by offset, animated 0 -> 1 over the shot."""
    obj.shape_key_add(name="Basis")
    key = obj.shape_key_add(name="Move")
    for v, kv in zip(obj.data.vertices, key.data, strict=True):
        if all(box_lo[i] <= v.co[i] <= box_hi[i] for i in range(3)):
            kv.co = v.co + Vector(offset)
    key.value = 0.0
    key.keyframe_insert("value", frame=1)
    key.value = 1.0
    key.keyframe_insert("value", frame=N)


def test_self_overlap_ignores_static_detail_sunk_into_a_big_body():
    """Ledges set into a building's wall are how a building is modelled: a small part sunk into a part at least
    SELF_BODY_RATIO times its volume does not warn on a rigid hero. Two similar parts still do."""
    sc = _scene()
    tower = _boxes_mesh("Tower", [((0, 3, 2), (4, 4, 4)), ((0, 3, 3), (4.6, 4.6, 0.2))])  # a ledge 2 m into the body
    bake.tag(tower, "hero")
    assert not _found(sc, "self_overlap"), _found(sc, "self_overlap")
    bake.tag(_boxes_mesh("Rods", [((-4, 3, 1), (1, 0.1, 0.1)), ((-3.6, 3, 1), (1, 0.1, 0.1))]), "hero")  # one bar
    found = _found(sc, "self_overlap", severity="warning")
    assert [f.obj for f in found] == ["Rods"], found


def test_self_overlap_on_a_deforming_hero_reports_parts_that_move_into_each_other():
    """Parts of one mesh that move against each other (shape keys, an armature) are checked at every sampled frame,
    big body or not; detail that stays put on a deforming hero does not warn."""
    sc = _scene()
    rig = _boxes_mesh("Rig", [((0, 3, 2), (4, 4, 4)), ((3, 3, 2), (0.5, 0.5, 0.5))])
    _shape_key_move(rig, (2.7, 2.7, 1.7), (3.3, 3.3, 2.3), (-1.1, 0, 0))  # the small part ends 35 cm inside
    bake.tag(rig, "hero")
    found = _found(sc, "self_overlap", "Rig", "warning")
    assert len(found) == 1 and "35 cm inside" in found[0].message and found[0].frame > 1, found
    bpy.data.objects.remove(rig)
    still = _boxes_mesh("Still", [((0, 3, 2), (4, 4, 4)), ((0, 3, 3), (4.6, 4.6, 0.2)), ((4, 3, 2), (0.5, 0.5, 0.5))])
    _shape_key_move(still, (3.7, 2.7, 1.7), (4.3, 3.3, 2.3), (0, 0, 1))  # only the free part moves
    bake.tag(still, "hero")
    assert not _found(sc, "self_overlap"), _found(sc, "self_overlap")


def test_live_boolean_modifier_warned():
    """A live Boolean can evaluate differently between renders (an opening in the stills gone in the final)."""
    sc = _scene()
    wall = _cube("Holed wall", (0, 0, 1.0), size=2.0)
    cutter = _cube("Cutter", (0, -1, 1.0), size=0.6)
    cutter.hide_render = True
    mod = wall.modifiers.new("Window", "BOOLEAN")
    mod.object = cutter
    found = _found(sc, "boolean", "Holed wall", "warning")
    assert len(found) == 1 and "'Window'" in found[0].message and "apply" in found[0].message, found
    assert "into the mesh" in found[0].message, found
    mod.show_render = False  # not rendered: nothing to warn about
    assert not _found(sc, "boolean")
    wall.modifiers.remove(mod)
    assert not _found(sc, "boolean")


def _empty(sc, name, location=(0, 0, 0)):
    e = bpy.data.objects.new(name, None)
    sc.collection.objects.link(e)
    e.location = location
    return e


def _parent(child, parent):
    bpy.context.view_layer.update()
    child.parent = parent
    child.matrix_parent_inverse = parent.matrix_world.inverted()


def test_contact_parts_with_different_moving_parents_warned():
    """A part that follows a moving parent, in contact with one that does not: a rebuilt part lost its parent and
    the two drift apart or through each other."""
    sc = _scene()
    deck = _empty(sc, "Deck")
    rod = _cube("Rod", (0, 0, 1.0), size=0.1)
    body = _cube("Body", (0, 0, 1.3), size=0.5)  # sits on the rod's top
    _parent(rod, deck)
    bake.tag(rod, "contact:Body")
    _move_x(deck, [0.4 * math.sin(i / 4) for i in range(N)])  # the deck sways and takes the rod along
    found = _found(sc, "contact_parent", severity="warning")
    assert len(found) == 1 and "'Rod'" in found[0].message and "'Body'" in found[0].message, found
    assert "'Deck'" in found[0].message and "parent" in found[0].message, found
    _parent(body, deck)  # both follow the deck: fine
    assert not _found(sc, "contact_parent")


def test_contact_parent_check_allows_sliding_and_hinged_parts():
    sc = _scene()
    rod = _cube("Slider", (0, 0, 1.0), size=0.1)
    _cube("Sleeve", (0, 0, 1.3), size=0.5)
    bake.tag(rod, "contact:Sleeve")
    _move_x(rod, [0.02 * i for i in range(N)])  # the part's own motion: it slides against the other on purpose
    swing = _empty(sc, "Swing", (3, 0, 1))
    bpy.ops.mesh.primitive_cylinder_add(radius=0.02, depth=0.3, location=(3, 0, 1), rotation=(math.radians(90), 0, 0))
    bpy.context.object.name = "Pin"
    _material(bpy.context.object)
    arm = _cube("Arm", (3, 0, 0.775))
    arm.scale = (0.1, 0.05, 0.55)  # its top end runs round the pin
    _parent(arm, swing)
    bake.tag(arm, "contact:Pin")
    bake.keyframe_series(swing, "rotation_euler", 1, [0.5 * math.sin(i / 4) for i in range(N)])  # swings on the pin
    assert not _found(sc, "contact_parent"), _found(sc, "contact_parent")


def test_arc_length_path_moves_without_pops():
    """keyframe_path: one easing over the whole polyline, so a long leg and a short leg move at one speed."""
    sc = _scene()
    spark = _cube("Spark", (0, 0, 1), size=0.1)
    points = [(-3, 2, 1), (3, 2, 1), (3, 2, 1.4)]  # 6 m, then a 40 cm leg
    bake.keyframe_path(spark, points, 1, N)
    assert not _found(sc, "pops"), _found(sc, "pops")
    track = []
    for f in range(1, N + 1):
        sc.frame_set(f)
        track.append(spark.matrix_world.translation.copy())
    assert (track[0] - Vector(points[0])).length < 1e-5
    assert (track[-1] - Vector(points[-1])).length < 1e-5
    steps = [(b - a).length for a, b in pairwise(track)]
    mid = steps[N // 4 : 3 * N // 4]
    assert max(mid) / min(mid) < 1.6, steps


def test_every_frame_of_a_moving_object_is_checked():
    """A fast part that crosses a wall between two sampled frames (every 5th) is still caught, with its frames."""
    sc = _scene()
    _cube("Wall", (0, 0, 1.0), size=2.0).scale = (0.1, 1, 1)  # 20 cm thick
    dart = _cube("Dart", (0, -0.5, 1.0), size=0.3)
    # out of the wall except frames 3-4 (sampled frames are 1, 6, 11, ..., 26, 30)
    _move_x(dart, [-0.07 if i in (2, 3) else (-3.0 if i < 2 else 3.0) for i in range(N)])
    found = _found(sc, "overlap", severity="error")
    assert len(found) == 1 and "Dart" in found[0].message and "Wall" in found[0].message, found
    assert "frames 3-4" in found[0].message and found[0].frame in (3, 4), found


def test_every_offending_pair_is_listed_up_to_a_cap():
    sc = _scene()
    for i in range(checks.OVERLAP_MAX_FINDINGS + 4):
        _cube(f"A{i:02d}", (i * 3.0 - 30, 4, 0.5))
        _cube(f"B{i:02d}", (i * 3.0 - 29.7, 4.2, 0.6))  # 30 cm into its A
    found = _found(sc, "overlap")
    pairs = [f for f in found if "interpenetrat" in f.message]
    assert len(pairs) == checks.OVERLAP_MAX_FINDINGS, found
    hidden = [f for f in found if "not listed" in f.message]
    assert len(hidden) == 1 and "4 more" in hidden[0].message, hidden


def test_renamed_duplicates_are_warned():
    sc = _scene()
    _cube("Person", (0, 0, 0.5))
    _cube("Person.001", (2, 0, 0.5))
    found = _found(sc, "names", severity="warning")
    assert [f.obj for f in found] == ["Person.001"], found


def test_tags_must_name_objects_in_the_scene():
    sc = _scene()
    bake.tag(_cube("Rod", (0, 0, 0.5)), "contact:Yoke", "contact:Floor:0.01")
    found = _found(sc, "names", severity="error")
    assert len(found) == 1 and "'Yoke'" in found[0].message and found[0].obj == "Rod", found


def test_near_labels_and_hero_shots_must_exist():
    from types import SimpleNamespace

    sc = _scene()
    bake.tag(_cube("Person.001", (0, 0, 0.5)), "hero:s1,s9")
    shot = SimpleNamespace(id="s1", labels=["plain", {"text": "You", "near": "Person"}])
    short = SimpleNamespace(shots=[shot])
    found = [f for f in checks.run_checks(sc, _timeline(), short=short) if f.check == "names" and f.severity == "error"]
    messages = " ".join(f.message for f in found)
    assert len(found) == 2 and "'Person'" in messages and "'s9'" in messages, found
    shot.labels = [{"text": "You", "near": "Person.001"}]
    bpy.data.objects["Person.001"]["bsa_tags"] = "hero:s1"
    found = [f for f in checks.run_checks(sc, _timeline(), short=short) if f.check == "names" and f.severity == "error"]
    assert found == [], found


def test_near_labels_read_from_the_short_beside_the_blend():
    """With no short given, the checks read source/short.json of the short whose blend/short.blend is open."""
    import shutil

    sc = _scene()
    _cube("Pendulum C bob.001", (0, 0, 0.5))
    short_dir = Path(tempfile.mkdtemp()) / "01-pendulum"
    (short_dir / "blend").mkdir(parents=True)
    (short_dir / "source").mkdir()
    sample = Path(__file__).resolve().parents[1] / "e2e" / "pendulum" / "source" / "short.json"
    shutil.copyfile(sample, short_dir / "source" / "short.json")
    bpy.ops.wm.save_as_mainfile(filepath=str(short_dir / "blend" / "short.blend"))
    tl = Timeline(30, N, [Span("s02", 1, N)], [], [])  # s02 has the label near "Pendulum C bob"
    found = [f for f in checks.run_checks(sc, tl) if f.check == "names" and f.severity == "error"]
    assert len(found) == 1 and "'Pendulum C bob'" in found[0].message, found
    assert "did you mean 'Pendulum C bob.001'" in found[0].message, found
    bpy.data.objects["Pendulum C bob.001"].name = "Pendulum C bob"
    found = [f for f in checks.run_checks(sc, tl) if f.check == "names"]
    assert found == [], found


def test_resting_on_floor_is_not_overlap():
    sc = _scene()
    _cube("Box", (0, 0, 0.5))
    bpy.ops.mesh.primitive_uv_sphere_add(radius=0.5, location=(1.2, 0, 0.5))
    _material(bpy.context.object)
    _cube("Sunk", (-1.2, 0, 0.499))  # 1 mm into the floor: within tolerance
    assert not _found(sc, "overlap")
    _cube("Deep", (0, 2, 0.4))  # 10 cm into the floor
    found = _found(sc, "overlap", severity="error")
    assert len(found) == 1 and "Deep" in found[0].message, found


def test_hero_leaving_frame_found():
    sc = _scene()
    hero = _cube("Hero", (0, 0, 0.5))
    bake.tag(hero, "hero")
    _move_x(hero, [10 * i / (N - 1) for i in range(N)])
    found = _found(sc, "frame_edge", "Hero")
    assert len(found) == 1, found
    assert found[0].frame > 1


def test_per_shot_hero_only_checked_in_its_shots():
    sc = _scene()
    hero = _cube("Hero", (0, 0, 0.5))
    bake.tag(hero, "hero:s2")
    half = N // 2
    _move_x(hero, [10 * i / (half - 1) for i in range(half)] + [0.0] * (N - half))  # leaves the frame in s1 only
    tl = Timeline(30, N, [Span("s1", 1, half), Span("s2", half + 1, N)], [], [])
    assert [f for f in checks.run_checks(sc, tl) if f.check == "frame_edge"] == []
    bake.tag(hero, "hero:s1")  # now a hero in s1 too
    found = [f for f in checks.run_checks(sc, tl) if f.check == "frame_edge"]
    assert len(found) == 1 and "'s1'" in found[0].message, found


def test_pop_found():
    sc = _scene()
    cube = _cube("Popper", (0, 0, 0.5))
    _move_x(cube, [0.02 * i + (2.0 if i >= 14 else 0.0) for i in range(N)])
    found = _found(sc, "pops", "Popper", "error")
    assert [f.frame for f in found] == [15], found


def test_floating_found():
    sc = _scene()
    bake.tag(_cube("Floater", (0, 0, 0.55)), "grounded")
    bake.tag(_cube("Rester", (1.5, 0, 0.5)), "grounded")
    found = _found(sc, "floating", severity="error")
    assert [f.obj for f in found] == ["Floater"], found


def test_faceted_found():
    sc = _scene()
    bpy.ops.mesh.primitive_uv_sphere_add(radius=0.5, location=(0, 0, 0.5))
    ball = bpy.context.object
    ball.name = "Ball"
    _material(ball)
    assert len(_found(sc, "faceted", "Ball", "warning")) == 1
    bpy.ops.object.shade_smooth()
    assert not _found(sc, "faceted")


def test_faceted_reads_the_rendered_normals():
    """A bevel with hardened normals has edges marked sharp in its evaluated mesh, but its custom normals are
    continuous across them: it renders smooth, so it is not faceted. A raw bevel on flat faces is."""
    from bsa.blender import look

    sc = _scene()
    hard = _cube("Hardened", (-1.0, 0, 0.5))
    look.smooth(hard, 30)
    look.bevel(hard, 0.05)
    raw = _cube("Raw", (1.0, 0, 0.5))
    mod = raw.modifiers.new("Bevel", "BEVEL")
    mod.width, mod.segments, mod.limit_method = 0.05, 3, "ANGLE"
    assert [f.obj for f in _found(sc, "faceted")] == ["Raw"]


def test_faceted_ignores_hardened_bevels_on_curved_parts():
    """look.smooth + look.bevel on a cylinder, a cone or a ring renders smooth (the hardened normals at the bevel's
    edge are about 1 degree off the face normals, far less than the edge's angle); flat shading is still found."""
    from bsa.blender import look

    sc = _scene()
    bpy.ops.mesh.primitive_cylinder_add(radius=0.05, depth=1.0, vertices=32, location=(-1.0, 0, 0.5))
    spire = bpy.context.object
    bpy.ops.mesh.primitive_cone_add(radius1=0.3, depth=1.0, vertices=32, location=(0, 0, 0.5))
    cone = bpy.context.object
    bpy.ops.mesh.primitive_torus_add(major_radius=0.3, minor_radius=0.02, location=(1.0, 0, 0.5))
    ring = bpy.context.object
    for obj, width in ((spire, 0.005), (cone, 0.01), (ring, 0.002)):
        _material(obj)
        look.smooth(obj, 30)
        look.bevel(obj, width)
    assert not _found(sc, "faceted"), _found(sc, "faceted")
    bpy.ops.mesh.primitive_cylinder_add(radius=0.05, depth=1.0, vertices=32, location=(2.0, 0, 0.5))
    bpy.context.object.name = "FlatRod"
    _material(bpy.context.object)
    assert [f.obj for f in _found(sc, "faceted")] == ["FlatRod"]


def test_progress_counts_every_frame_and_changes_nothing():
    sc = _scene()
    hero = _cube("Hero", (0.6, 0, 0.5))
    bake.tag(hero, "hero")
    _move_x(hero, [0.6 + 0.4 * i / (N - 1) for i in range(N)])
    _cube("Other", (0.9, 0, 0.5))  # overlaps the hero: some findings to compare
    seen = []
    with_progress = checks.run_checks(sc, _timeline(), progress=lambda *a: seen.append(a))
    assert seen == [("s1", i, N) for i in range(1, N + 1)]
    assert with_progress == checks.run_checks(sc, _timeline())


def test_clean_scene_has_no_errors():
    sc = _scene()
    hero = _cube("Hero", (0.6, 0, 0.5))
    bake.tag(hero, "hero", "grounded")
    _move_x(hero, [0.6 + 0.4 * i / (N - 1) for i in range(N)])
    bpy.ops.mesh.primitive_uv_sphere_add(radius=0.5, location=(-1.0, 0, 0.5))
    ball = bpy.context.object
    _material(ball)
    bpy.ops.object.shade_smooth()
    bake.tag(ball, "grounded")
    label = _text("Label", (0, -2, 2.5))  # a finished text label and a bevelled curve: no findings
    _material(label, "label")
    curve = bpy.data.curves.new("Arc", "CURVE")
    curve.dimensions, curve.bevel_depth = "3D", 0.02
    spline = curve.splines.new("POLY")
    spline.points.add(1)
    spline.points[0].co, spline.points[1].co = (-1.5, -2, 3.2, 1), (1.5, -2, 3.2, 1)
    arc = bpy.data.objects.new("Arc", curve)
    sc.collection.objects.link(arc)
    curve.materials.append(bpy.data.materials["label"])
    sc.frame_set(7)
    found = checks.run_checks(sc, _timeline())
    assert found == [], found
    assert sc.frame_current == 7


def test_findings_sorted_errors_first():
    sc = _scene()
    bpy.ops.mesh.primitive_uv_sphere_add(radius=0.5, location=(0, 0, 0.5))  # faceted: warning
    _material(bpy.context.object)
    _cube("Bare", (1.5, 0, 0.5), material=False)  # error
    found = checks.run_checks(sc, _timeline())
    severities = [f.severity for f in found]
    assert "warning" in severities and "error" in severities
    assert severities == sorted(severities, key=lambda s: s != "error")
    assert checks.Finding is __import__("bsa.image_checks", fromlist=["Finding"]).Finding


def _shells_mesh(name, flip_shell=None, flip_faces=0):
    """One object with two separate cube shells; optionally reverse one whole shell, or flip_faces faces of shell 0."""
    mesh = bpy.data.meshes.new(name)
    bm = bmesh.new()
    shells = []
    for x in (-0.8, 0.8):
        geom = bmesh.ops.create_cube(bm, size=1.0)
        bmesh.ops.translate(bm, vec=(x, 0, 0.5), verts=geom["verts"])
        bm.faces.ensure_lookup_table()
        shells.append([f for f in bm.faces if all(v in geom["verts"] for v in f.verts)])
    if flip_shell is not None:
        bmesh.ops.reverse_faces(bm, faces=shells[flip_shell])
    if flip_faces:
        bmesh.ops.reverse_faces(bm, faces=shells[0][:flip_faces])
    bm.to_mesh(mesh)
    bm.free()
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(obj)
    _material(obj)
    return obj


def test_partly_flipped_found():
    sc = _scene()
    for n in (1, 2):
        cube = _cube("Patchy", (0, 0, 0.5))
        bm = bmesh.new()
        bm.from_mesh(cube.data)
        bm.faces.ensure_lookup_table()
        bmesh.ops.reverse_faces(bm, faces=bm.faces[:n])
        bm.to_mesh(cube.data)
        bm.free()
        found = _found(sc, "normals", "Patchy", "error")
        assert len(found) == 1 and "flipped" in found[0].message, (n, found)
        bpy.data.objects.remove(cube)


def test_inside_out_shell_found():
    sc = _scene()
    _shells_mesh("TwoShells", flip_shell=1)
    found = _found(sc, "normals", "TwoShells", "error")
    assert len(found) == 1 and "inside out" in found[0].message, found
    bpy.data.objects.remove(bpy.data.objects["TwoShells"])
    _shells_mesh("CleanShells")
    assert not _found(sc, "normals")


def test_deleted_image_file_found():
    sc = _scene()
    cube = _cube("Textured", (0, 0, 0.5), material=False)
    mat = _material(cube, "textured")
    mat.use_nodes = True
    tex = mat.node_tree.nodes.new("ShaderNodeTexImage")
    path = Path(tempfile.mkdtemp()) / "soon_gone.png"
    tmp = bpy.data.images.new("tmp", 4, 4)
    tmp.filepath_raw = str(path)
    tmp.file_format = "PNG"
    tmp.save()
    img = bpy.data.images.load(str(path))
    assert len(img.pixels) and img.has_data
    tex.image = img
    bsdf = next(n for n in mat.node_tree.nodes if n.type == "BSDF_PRINCIPLED")
    mat.node_tree.links.new(tex.outputs["Color"], bsdf.inputs["Base Color"])
    assert not _found(sc, "images")
    path.unlink()
    assert img.has_data
    found = _found(sc, "images", "Textured", "error")
    assert len(found) == 1 and "soon_gone.png" in found[0].message, found


def test_checks_use_the_given_scene():
    _scene()  # the context scene: has a material-less cube that must not be reported
    _cube("ContextOnly", (0, 0, 0.5), material=False)
    other = bpy.data.scenes.new("Other")
    other.frame_start, other.frame_end = 1, N
    mesh = bpy.data.meshes.new("OtherBox")
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=1.0)
    bm.to_mesh(mesh)
    bm.free()
    box = bpy.data.objects.new("OtherBox", mesh)
    other.collection.objects.link(box)
    _move_x(box, [0.02 * i + (2.0 if i >= 14 else 0.0) for i in range(N)])
    assert bpy.context.scene != other
    found = checks.run_checks(other, _timeline())
    assert {(f.check, f.obj) for f in found} == {("materials", "OtherBox"), ("pops", "OtherBox")}, found
    assert [f.frame for f in found if f.check == "pops"] == [15]


def test_hollow_shell_is_not_inside_out():
    sc = _scene()
    mesh = bpy.data.meshes.new("Hollow")
    bm = bmesh.new()
    bmesh.ops.create_uvsphere(bm, u_segments=24, v_segments=12, radius=1.0)
    inner = bmesh.ops.create_uvsphere(bm, u_segments=24, v_segments=12, radius=0.8)
    bm.faces.ensure_lookup_table()
    cavity = [f for f in bm.faces if all(v in inner["verts"] for v in f.verts)]
    bmesh.ops.reverse_faces(bm, faces=cavity)  # the cavity's normals point into the hollow, by design
    bm.to_mesh(mesh)
    bm.free()
    obj = bpy.data.objects.new("Hollow", mesh)
    obj.location = (0, 0, 1)
    sc.collection.objects.link(obj)
    _material(obj)
    assert not _found(sc, "normals"), _found(sc, "normals")


def _text(name, location):
    data = bpy.data.curves.new(name, "FONT")
    data.body = "2.0 s"
    obj = bpy.data.objects.new(name, data)
    obj.location = location
    obj.rotation_euler = (math.radians(90), 0, 0)
    bpy.context.scene.collection.objects.link(obj)
    return obj


def _pink_material(name):
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    tex = mat.node_tree.nodes.new("ShaderNodeTexImage")
    img = bpy.data.images.new(name + "_img", 4, 4)
    img.source = "FILE"
    img.filepath = str(Path(tempfile.mkdtemp()) / f"{name}_gone.png")
    tex.image = img
    bsdf = next(n for n in mat.node_tree.nodes if n.type == "BSDF_PRINCIPLED")
    mat.node_tree.links.new(tex.outputs["Color"], bsdf.inputs["Base Color"])
    return mat


def test_grey_text_label_found():
    sc = _scene()
    _text("Label", (0, -2, 2))  # no material: renders default grey
    found = _found(sc, "materials", "Label", "error")
    assert len(found) == 1, found


def test_pink_curve_found():
    sc = _scene()
    label = _text("Label", (0, -2, 2))
    label.data.materials.append(_pink_material("pinktext"))
    found = _found(sc, "images", "Label", "error")
    assert len(found) == 1 and "pinktext_gone.png" in found[0].message, found


def test_instanced_pink_model_found():
    """An instanced collection (a linked or appended asset placed as an instance) is checked too."""
    sc = _scene()
    asset = bpy.data.collections.new("Asset")
    sc.collection.children.link(asset)
    model = _cube("Model", (0, 0, 0.5), material=False)
    for coll in model.users_collection:
        coll.objects.unlink(model)
    asset.objects.link(model)
    model.data.materials.append(_pink_material("pinkmodel"))
    bare = _cube("BareModel", (1.5, 0, 0.5), material=False)
    for coll in bare.users_collection:
        coll.objects.unlink(bare)
    asset.objects.link(bare)
    bpy.context.view_layer.layer_collection.children["Asset"].exclude = True  # only the instance renders
    inst = bpy.data.objects.new("AssetInstance", None)
    inst.instance_type, inst.instance_collection = "COLLECTION", asset
    inst.location = (0, 0, 0)
    sc.collection.objects.link(inst)
    found = checks.run_checks(sc, _timeline())
    pink = [f for f in found if f.check == "images" and f.severity == "error"]
    assert len(pink) == 1 and "pinkmodel_gone.png" in pink[0].message and "AssetInstance" in pink[0].obj, found
    bare_found = [f for f in found if f.check == "materials" and f.severity == "error"]
    assert len(bare_found) == 1 and "BareModel" in bare_found[0].obj, found
    partial = [f for f in found if f.check == "instances" and f.severity == "warning"]
    assert len(partial) == 1 and "AssetInstance" in partial[0].obj, found


def _material_boxes(name, boxes, location=(0, 3, 0)):
    """One object made of loose box parts, each with its own material: boxes are (centre, (sx, sy, sz), material)."""
    mesh = bpy.data.meshes.new(name)
    slots = []
    bm = bmesh.new()
    for centre, size, mat in boxes:
        if mat not in slots:
            slots.append(mat)
        geom = bmesh.ops.create_cube(bm, size=1.0)
        bmesh.ops.scale(bm, vec=size, verts=geom["verts"])
        bmesh.ops.translate(bm, vec=centre, verts=geom["verts"])
        for f in {f for v in geom["verts"] for f in v.link_faces}:
            f.material_index = slots.index(mat)
    bm.to_mesh(mesh)
    bm.free()
    for mat in slots:
        mesh.materials.append(bpy.data.materials.get(mat) or bpy.data.materials.new(mat))
    obj = bpy.data.objects.new(name, mesh)
    obj.location = location
    bpy.context.scene.collection.objects.link(obj)
    return obj


def _ledge(name, body_mat, ledge_mat, location=(0, 3, 0)):
    """A 2 m body with a 3 m ledge whose top face lies on the body's top face: flush caps in one mesh."""
    return _material_boxes(name, [((0, 0, 1), (2, 2, 2), body_mat), ((0, 0, 1.9), (3, 3, 0.2), ledge_mat)], location)


def test_coplanar_faces_inside_one_mesh_found():
    """Joined parts whose faces share a plane flicker: an error when the materials differ, a warning when not."""
    sc = _scene()
    _ledge("Tower", "white", "glass")
    found = _found(sc, "self_zfight", "Tower")
    assert len(found) == 1 and found[0].severity == "error", found
    msg = found[0].message
    assert "'white'" in msg and "'glass'" in msg and "4.0 m²" in msg and "(0.00, 3.00, 2.00)" in msg, msg
    assert not _found(sc, "overlap", severity="error"), _found(sc, "overlap")
    bpy.data.objects.remove(bpy.data.objects["Tower"])
    _ledge("Plain", "white", "white")
    found = _found(sc, "self_zfight", "Plain")
    assert len(found) == 1 and found[0].severity == "warning" and "'white'" in found[0].message, found


def test_coplanar_check_ignores_flat_surfaces_shared_edges_and_bevels():
    """A flat surface split into quads, parts that only share an edge, parts stacked face to face (their contact
    faces face opposite ways, hidden inside) and a bevelled box are fine."""
    sc = _scene()
    bpy.ops.mesh.primitive_grid_add(x_subdivisions=12, y_subdivisions=9, size=2, location=(-2, 2, 0.01))
    _material(bpy.context.object)
    _material_boxes("Row", [((0, 0, 0.5), (1, 1, 1), "a"), ((1, 0, 0.5), (1, 1, 1), "b")])  # flush side by side
    _material_boxes("Step", [((0, 0, 0.5), (1, 1, 1), "a"), ((1, 0, 1.5), (1, 1, 1), "b")], (3, 3, 0))  # an edge
    _material_boxes("Stack", [((0, 0, 0.5), (1, 1, 1), "a"), ((0, 0, 1.5), (0.6, 0.6, 1), "b")], (-3, 3, 0))
    box = _cube("Bevelled", (0, 6, 0.5))
    look.bevel(box, 0.05)
    assert not _found(sc, "self_zfight"), _found(sc, "self_zfight")


def test_coplanar_check_is_scale_aware_and_covers_heroes_and_meshes_in_view():
    """0.1 mm is measured in the world: a 1 mm step scaled down to 0.05 mm is coplanar. Meshes out of every shot
    camera's view are not checked unless they are heroes."""
    sc = _scene()
    stepped = [((0, 0, 1), (2, 2, 2), "white"), ((0, 0, 1.901), (3, 3, 0.2), "glass")]  # the ledge 1 mm higher
    big = _material_boxes("Stepped", stepped)
    assert not _found(sc, "self_zfight")
    big.scale = (0.05, 0.05, 0.05)
    found = _found(sc, "self_zfight", "Stepped", "error")
    assert len(found) == 1 and "100 cm²" in found[0].message, found
    bpy.data.objects.remove(big)
    hidden = _ledge("Behind", "white", "glass", location=(0, -20, 0))  # behind the camera
    assert not _found(sc, "self_zfight")
    bake.tag(hidden, "hero")
    assert [f.obj for f in _found(sc, "self_zfight")] == ["Behind"]


def _count_pairs():
    """Wrap checks._pair to count the measurements of each pair of objects: (counts, restore)."""
    counts: dict[tuple[str, str], int] = {}
    original = checks._pair

    def counted(ga, gb, *args):
        key = tuple(sorted((ga.name, gb.name)))
        counts[key] = counts.get(key, 0) + 1
        return original(ga, gb, *args)

    checks._pair = counted
    return counts, lambda: setattr(checks, "_pair", original)


def test_rigid_assembly_far_from_origin_is_measured_once():
    """A room that sways as a whole high up a tower: its parts keep their places relative to each other, so each
    such pair is measured once, although matrix_world (single precision) jitters at 450 m. A part that moves inside
    the room is still measured on every frame it moves."""
    sc = _scene()
    frame = _empty(sc, "Tower frame", (0, 0, 450))
    room = _cube("Room", (0, 0, 450), size=4.0)
    room.scale = (1, 1, 0.1)  # a 40 cm slab, its top at 450.2
    block = _cube("Block", (0, 0, 450.449), size=0.5)  # sunk 1 mm into the slab's top
    bob = _cube("Bob", (1.2, 0, 450.349), size=0.3)
    for part in (room, block, bob):
        _parent(part, frame)
    bake.tag(block, "contact:Room")
    bake.tag(bob, "contact:Room")
    _move_x(frame, [0.8 * math.sin(i / 3) for i in range(N)])
    bake.keyframe_series(frame, "rotation_euler", 1, [0.05 * math.sin(i / 4) for i in range(N)])
    _move_x(bob, [1.2 + 0.1 * math.sin(i / 2) for i in range(N)])  # slides on the slab, against the room
    counts, restore = _count_pairs()
    try:
        found = checks.run_checks(sc, _timeline())
    finally:
        restore()
    assert not [f for f in found if f.check == "overlap"], found
    assert counts[("Block", "Room")] == 1, counts
    assert counts[("Bob", "Room")] >= N - 2, counts  # the moving part: every frame


def test_inside_rays_far_from_origin_cross_each_face_once():
    """Ray parity at 450 m: a fixed micrometre step past a hit does not move a single-precision point, so the ray
    would hit the same face up to 10000 times. Each face is crossed once."""
    _scene()
    cube = _cube("Far", (0, 0, 450), size=1.0)
    bpy.context.view_layer.update()
    geo = checks._world_geo(cube, bpy.context.evaluated_depsgraph_get())
    tree = checks._bvh(geo.tris)
    inside = (0.01, 0.02, 450.03)
    assert [checks._crossings(tree, Vector(inside), d) for d in checks._RAYS] == [1, 1, 1]
    assert checks._parity(tree, inside)
    assert not checks._parity(tree, (0.01, 0.02, 451.0))


def test_overlay_objects_skip_physical_checks_but_stay_in_frame():
    """A glow halo or a distance line is an overlay: it may pass through things, sit on a plane, float and jump,
    but it must still stay inside the frame."""
    sc = _scene()
    _cube("Pivot", (0, 0, 0.5))
    halo = _cube("Halo", (0.3, 0.2, 0.6), size=0.8)  # inside the pivot
    bpy.ops.mesh.primitive_plane_add(size=1.0, location=(0.0, 0, 1.0))  # on the pivot's top face: z-fights
    bpy.context.object.name = "Line A"
    _material(bpy.context.object)
    bpy.ops.mesh.primitive_plane_add(size=1.0, location=(0.4, 0, 1.0))  # overlaps Line A: z-fights
    bpy.context.object.name = "Line B"
    _material(bpy.context.object)
    _move_x(halo, [0.3 + (2.0 if i >= 14 else 0.0) for i in range(N)])  # jumps: a pop
    bake.tag(halo, "grounded")  # and it would float
    before = {f.check for f in checks.run_checks(sc, _timeline())}
    assert {"overlap", "pops", "floating"} <= before, before
    for name in ("Halo", "Line A", "Line B"):
        bake.tag(bpy.data.objects[name], "overlay")
    found = checks.run_checks(sc, _timeline())
    assert not [f for f in found if f.check in ("overlap", "pops", "floating", "self_zfight")], found
    _move_x(halo, [0.3 + 12 * i / (N - 1) for i in range(N)])  # leaves the frame
    edge = [f for f in checks.run_checks(sc, _timeline()) if f.check == "frame_edge"]
    assert [f.obj for f in edge] == ["Halo"], edge
    assert bake.tags(halo) == ["grounded", "overlay"]


def test_hard_rules_flag_object_mesh_material_and_collection_names():
    """Under a no-people rule, scene names with person words warn (answered words in rules_check are cleared);
    without rules nothing is scanned."""
    from types import SimpleNamespace

    sc = _scene()
    _cube("Worker", (0, 0, 0.5))
    hand = _cube("HourHand", (2, 0, 0.5))
    desk = _cube("Desk", (-2, 0, 0.5), material=False)
    _material(desk, "Skin")
    gear = _cube("Gear", (4, 0, 0.5))
    gear.data.name = "FaceMesh"
    coll = bpy.data.collections.new("Crowd")
    sc.collection.children.link(coll)
    box = _cube("Box", (-4, 0, 0.5))
    for c in list(box.users_collection):
        c.objects.unlink(box)
    coll.objects.link(box)
    assert not [f for f in checks.run_checks(sc, _timeline()) if f.check == "hard_rule"]
    rule = {"id": "r1", "text": "No people", "kind": "no_humans", "scope": "every short"}
    short = SimpleNamespace(shots=[], rules=[], rules_check={"r1": {"kept": "x", "cleared": {"hand": "clock hand"}}})
    found = [f for f in checks.run_checks(sc, _timeline(), short=short, rules=[rule]) if f.check == "hard_rule"]
    assert {f.obj for f in found} == {"Worker", "Desk", "Gear", "Box"}, found
    assert all(f.severity == "warning" and "r1" in f.message for f in found), found
    assert not [f for f in found if f.obj == hand.name]


def test_cradle_swing_and_collision_are_not_pops_but_a_teleport_is():
    """A ball swings down fast (about 19 mm a frame), stops dead against the next one, then hangs almost still; the
    struck ball sets off at the arriving speed. Neither is a pop (the old median rule called the fall one); a
    teleport of a third ball still is."""
    sc = _scene()
    first = _cube("Striker", (0, 0, 1.0), size=0.025)
    struck = _cube("Struck", (0.5, 0, 1.0), size=0.025)
    jumper = _cube("Jumper", (-0.5, 0, 1.0), size=0.025)
    v = 0.019
    _move_x(first, [-v * (10 - i) for i in range(10)] + [0.0002 * math.sin(i) for i in range(N - 10)])
    _move_x(struck, [0.5] * 10 + [0.5 + v * (0.4 + i) for i in range(N - 10)])
    _move_x(jumper, [-0.5 + 0.001 * i + (0.05 if i >= 15 else 0.0) for i in range(N)])
    found = _found(sc, "pops")
    assert [(f.obj, f.frame) for f in found] == [("Jumper", 16)], found


def test_zfight_tolerance_follows_what_the_camera_can_see():
    """A 6.4 mm ring face 0.05 mm inside another part: fine 30 cm from the camera, z-fighting 12 m away. Faces that
    share a plane exactly z-fight however close. The finding says where, how far apart and how big both are."""
    sc = _scene()
    for name, size, z in (("Ring", 0.0064, 1.5), ("Cap", 0.004, 1.50005)):
        bpy.ops.mesh.primitive_plane_add(size=size, location=(0.001, 0, z))
        bpy.context.object.name = name
        _material(bpy.context.object)
    found = _found(sc, "overlap", severity="error")  # the camera 12 m away
    assert len(found) == 1 and "z-fighting" in found[0].message, found
    msg = found[0].message
    assert "'Ring' (6.4 x 6.4 x 0 mm)" in msg and "'Cap' (4 x 4 x 0 mm)" in msg, msg
    assert "0.05 mm apart" in msg and "1.50" in msg, msg
    sc.camera.location = (0, -0.3, 1.5)  # close up: the depth buffer tells them apart
    assert not _found(sc, "overlap", severity="error"), _found(sc, "overlap")
    bpy.data.objects["Cap"].location.z = 1.5  # flush: z-fights at any distance
    found = _found(sc, "overlap", severity="error")
    assert len(found) == 1 and "flush" in found[0].message, found


def test_contact_moves_tag_allows_designed_motion_but_keeps_the_depth_limit():
    """contact:<other>:moves: the parts touch at some point and move against each other by design (a pendulum ball
    and its neighbour, a pawl on a turning ring, a tyre on a scrolling floor): no contact_parent warning, but the
    overlap depth limit (and a stated depth) still holds."""
    sc = _scene()
    deck = _empty(sc, "Deck")
    rod = _cube("Rod", (0, 0, 1.0), size=0.1)
    _cube("Body", (0, 0, 1.3), size=0.5)
    _parent(rod, deck)
    _move_x(deck, [0.4 * math.sin(i / 4) for i in range(N)])
    bake.tag(rod, "contact:Body:moves")
    assert not _found(sc, "contact_parent"), _found(sc, "contact_parent")
    assert not _found(sc, "names"), _found(sc, "names")
    assert not _found(sc, "overlap"), _found(sc, "overlap")  # still a contact pair: touching is fine
    rod["bsa_tags"] = ""
    bake.tag(rod, "contact:Body:0.001:moves")  # depth and qualifier together, either order
    assert checks._contact_target("Body:0.001:moves", ()) == "Body"
    assert checks._contact_target("Body:moves:0.001", ()) == "Body"
    assert checks._contact_target("Body:moves", ()) == "Body"
    assert not _found(sc, "contact_parent")
    sunk = _cube("Sunk", (3, 0, 0.5), size=1.0)
    pin = _cube("Pin", (3, 0, 1.0), size=0.3)  # 15 cm into Sunk: deeper than any allowance
    bake.tag(pin, "contact:Sunk:moves")
    found = _found(sc, "overlap", severity="error")
    assert any("'Pin'" in f.message or f.obj == "Pin" for f in found), found
    assert sunk.name == "Sunk"
