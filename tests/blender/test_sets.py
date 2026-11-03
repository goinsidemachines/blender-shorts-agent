import json
import math
import os
import sys
import tempfile
from pathlib import Path

import bpy
import numpy as np
from mathutils import Vector

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE / "fixtures"))

from kitfix import KIT
from sets_short import two_shot

from bsa import colour, looks, setplace
from bsa.blender import anchors, bake, demo, sets, styles
from bsa.blender import scene as scene_mod
from bsa.blender.pixels import read_png
from bsa.blender.scene import setup_scene
from bsa.blender.stills import render_still
from bsa.kit import kit_from
from bsa.review import decisions
from bsa.timeline import build_timeline

scene_mod.use_gpu = lambda: "NONE"  # Cycles on the CPU, as the plan pins tests
PRESET = "bsa test tiny"
scene_mod.PRESETS[PRESET] = {"percent": 10, "cycles": {"samples": 32, "denoise": False}, "eevee": {"samples": 8}}
OUT = Path(os.environ.get("BSA_SETS_TEST_OUT") or tempfile.mkdtemp())
DURATIONS = {"l1": 0.6, "l2": 0.4}


def _kit(style: str = "toy"):
    return kit_from({**KIT, "style": style})


def _rock(pivot) -> None:
    """The lever rocks +-11 degrees over 30 frames."""
    for f, deg in ((1, 11.0), (15, -11.0), (30, 11.0)):
        pivot.rotation_euler = (0.0, math.radians(deg), 0.0)
        pivot.keyframe_insert("rotation_euler", frame=f)


def _build(set_name: str | None = "workbench", engine: str = "eevee", slug: str = "lever", style: str = "toy"):
    """A fresh file: the demo lever (rocking) in the main scene, the set's base, the short's timeline."""
    bpy.ops.wm.read_factory_settings(use_empty=True)
    sc = bpy.context.scene
    short = two_shot(sc.name, slug)
    setup_scene(sc, short, engine, "still")
    kit = _kit(style)
    made = sets.base(sc, kit, set_name, 3.6) if set_name else {}
    built = demo.lever(sc, kit)
    _rock(bpy.data.objects["LeverPivot"])
    tl = build_timeline(short, DURATIONS)
    return sc, kit, short, tl, built, made


def _sdir(name: str = "lever") -> Path:
    sdir = Path(tempfile.mkdtemp()) / name
    (sdir / "source").mkdir(parents=True)
    return sdir


def _shot_frames(tl, shot_id: str, every: int = 1) -> list[int]:
    span = next(s for s in tl.shots if s.id == shot_id)
    return sorted(set(range(span.f0, span.f1 + 1, every)) | {span.f0, span.f1})


def _world_bounds(obj):
    bpy.context.view_layer.update()
    pts = [obj.matrix_world @ v.co for v in obj.data.vertices]
    return [min(p[i] for p in pts) for i in range(3)], [max(p[i] for p in pts) for i in range(3)]


def _top_at(obj, x: float, y: float) -> float:
    """The height of the object's up-facing face over (x, y): the support surface something at (x, y) stands on."""
    bpy.context.view_layer.update()
    mw = obj.matrix_world
    rot = mw.to_3x3().inverted().transposed()
    for poly in obj.data.polygons:
        if (rot @ poly.normal).normalized().z < 0.9:
            continue
        pts = [mw @ obj.data.vertices[i].co for i in poly.vertices]
        if min(p.x for p in pts) <= x <= max(p.x for p in pts) and min(p.y for p in pts) <= y <= max(p.y for p in pts):
            return max(p.z for p in pts)
    raise AssertionError(f"nothing of {obj.name} faces up over ({x}, {y})")


def _no_dot_names():
    for coll in (bpy.data.objects, bpy.data.materials, bpy.data.meshes, bpy.data.worlds, bpy.data.collections):
        bad = [x.name for x in coll if x.name[-4:-3] == "." and x.name[-3:].isdigit()]
        assert not bad, bad


def test_without_a_kit_sets_change_nothing():
    sc, kit, short, tl, *_ = _build(None)
    world = sc.world
    before = (len(bpy.data.objects), len(bpy.data.materials), len(bpy.data.worlds), len(bpy.data.collections))
    assert sets.base(sc, None, "workbench", 3) == {}
    assert sets.base(sc, kit, "none", 3) == {}
    assert sets.base(sc, kit, None, 3) == {}
    assert sets.dress(sc, None, short, tl, _sdir()) == {}
    assert sets.dress(sc, kit, short, tl, _sdir()) == {}  # a kit but no set on the scene
    assert (len(bpy.data.objects), len(bpy.data.materials), len(bpy.data.worlds),
            len(bpy.data.collections)) == before  # fmt: skip
    assert sc.world == world and "bsa_set" not in sc


def test_each_set_puts_its_floor_top_at_zero_and_tags_it():
    for name in setplace.SETS:
        sc, kit, short, tl, _, made = _build(name)
        assert made["set"] == name and sc["bsa_set"] == name
        for stage in ("base", "dress"):
            floors = [o for o in sc.objects if bake.set_role(o) == "floor"]
            assert len(floors) == 1, (name, stage)
            lo, hi = _world_bounds(floors[0])
            assert abs(lo[2] - (-sets.BENCH_T * 3.6 if name == "workbench" else 0.0)) < 1e-5, (name, stage, lo[2])
            assert abs(_top_at(floors[0], 0.0, 0.0)) < 1e-6, (name, stage)  # the support surface under the subject
            if name != "studio":  # the studio's one mesh sweeps up into its back wall
                assert abs(hi[2]) < 1e-6, (name, stage, hi[2])
            roles = {bake.set_role(o) for o in sc.objects if bake.is_set(o)}
            assert roles >= {"floor"} and (name != "outdoor" or "backdrop" in roles), (name, roles)
            if name in ("workbench", "graph"):
                assert "wall" in roles
            assert all(o.name in bpy.data.collections[f"{sc.name} set"].objects or bake.set_role(o) == "prop"
                       for o in sc.objects if bake.is_set(o))  # fmt: skip
            if stage == "base":
                sets.dress(sc, kit, short, tl, None)


def _footprint(sc, frames, size, wall_y, front=None):
    """The views' footprint on z = 0 over `frames` (far rays cut at 30 x size), cut at the wall (and, for a bench,
    in front at `front`): computed here from the camera's own view frame, not from sets."""
    xs, ys = [], []
    reach = sets.FAR_CLAMP * size
    for f in frames:
        sc.frame_set(f)
        cam = sc.camera
        mw = cam.matrix_world
        o = mw.translation
        quad = []
        for corner in cam.data.view_frame(scene=sc):
            d = (mw @ corner - o).normalized()
            quad.append(sets._ground_point(o, d, reach))
        cut = sets._clip_y(quad, wall_y)
        if front is not None:
            cut = [(x, -y) for x, y in sets._clip_y([(x, -y) for x, y in cut], -front)]
        for x, y in cut:
            xs.append(x)
            ys.append(y)
    return min(xs), min(ys), max(xs), max(ys)


def _subject_front(sc, frames) -> float:
    """The nearest y of the demo's rendered meshes over `frames`."""
    dg = bpy.context.evaluated_depsgraph_get()
    ys = []
    for f in frames:
        sc.frame_set(f)
        dg.update()
        for o in sc.objects:
            if o.type == "MESH" and not o.hide_render and not bake.is_set(o):
                ev = o.evaluated_get(dg)
                ys += [(ev.matrix_world @ Vector(c)).y for c in ev.bound_box]
    return min(ys)


def test_floor_is_sized_to_the_camera_view_never_larger():
    for name in ("workbench", "graph", "studio", "outdoor"):
        sc, kit, short, tl, *_ = _build(name)
        got = sets.dress(sc, kit, short, tl, None)
        x0, y0, x1, y1 = got["floor"]
        floor = next(o for o in sc.objects if bake.set_role(o) == "floor")
        lo, hi = _world_bounds(floor)
        assert abs(lo[1] - y0) < 1e-4, (name, lo, hi, got)
        if name != "studio":  # the studio's sweep is as wide as the view where it rises into its wall
            assert abs(lo[0] - x0) < 1e-4 and abs(hi[0] - x1) < 1e-4, (name, lo, hi, got)
        frames = _shot_frames(tl, "s1", sets.SAMPLE_EVERY)
        front = None
        if name == "workbench":  # the bench is a strip: from just in front of the subject (as it moves) to the wall
            front = _subject_front(sc, frames) - sets.BENCH_FRONT * 3.6
            assert abs(y0 - front) < 1e-4, (y0, front)
        fx0, fy0, fx1, fy1 = _footprint(sc, frames, 3.6, y1, front)
        print(f"FOOTPRINT {name} view {fx0:.2f} {fy0:.2f} {fx1:.2f} {fy1:.2f} floor {lo[0]:.2f} {lo[1]:.2f} "
              f"{hi[0]:.2f} {hi[1]:.2f}")  # fmt: skip
        assert abs(hi[1] - y1) < 1e-4, name  # the mesh is the floor dress reports (float32 vertices)
        # Blender's camera frame is float32: 1e-5 of the width is that rounding, not a bigger floor.
        assert x1 - x0 <= (fx1 - fx0) * 1.1 * (1 + 1e-5) + 1e-6, name
        assert y1 - y0 <= (fy1 - fy0) * 1.1 * (1 + 1e-5) + 1e-6, name
        assert x0 <= fx0 + 1e-4 and x1 >= fx1 - 1e-4 and y0 <= fy0 + 1e-4 and y1 >= fy1 - 1e-4, name
        assert hi[1] - lo[1] < sets.FAR_CLAMP * 3.6, name  # never the whole clamp: cut at the wall or the hills
        walls = [o for o in sc.objects if bake.set_role(o) == "wall"]
        for wall in walls:
            wlo, whi = _world_bounds(wall)
            assert abs(wlo[1] - hi[1]) < 1e-4 and abs(whi[1] - hi[1]) < 1e-4, name  # at the floor's far edge


def _clear_check(sc, kit, short, tl, frames):
    from bsa import cards, fonts

    font = cards.card_font(kit, fonts.font_caches(None))
    shot = short.shots[0]
    span = next(s for s in tl.shots if s.id == shot.id)
    texts = setplace.text_rects(short, tl, shot, span, kit, font, frames)
    props = [o for o in sc.objects if bake.set_role(o) == "prop"]
    subject = [o for o in sc.objects if o.type == "MESH" and not o.hide_render and not bake.is_set(o)]
    dg = bpy.context.evaluated_depsgraph_get()
    hits = []
    for f in frames:
        sc.frame_set(f)
        dg.update()
        cam = sc.camera.evaluated_get(dg)

        def rect(o, cam=cam):
            b = anchors.screen_box(sc, cam, o, dg)
            return None if b is None else setplace.Rect(b[0], b[1], b[2] - b[0], b[3] - b[1])

        subjects = [r for r in (rect(o) for o in subject) if r is not None]
        for p in props:
            r = rect(p)
            why = r and setplace.collision(r, subjects, texts[f], 0)
            if why:
                hits.append((f, p.name, why))
    return props, hits


def test_props_stay_clear_of_the_moving_subject_and_cards():
    for name in ("workbench", "graph", "outdoor"):
        sc, kit, short, tl, *_ = _build(name)
        got = sets.dress(sc, kit, short, tl, None)
        print(f"DRESS {name} kept {got['kept']} dropped {got['dropped']}")
        props, hits = _clear_check(sc, kit, short, tl, _shot_frames(tl, "s1"))
        assert props and len(props) == len(got["kept"]), name
        assert not hits, (name, hits)
        names = got["kept"] + [d["name"] for d in got["dropped"]]
        assert sorted(names) == sorted(p.name for p in setplace.props(name, 3.6)), name


def _entry(sdir, scene="Scene"):
    return json.loads((sdir / "source" / "sets.json").read_text(encoding="utf-8"))["scenes"][scene]


def test_dress_is_deterministic_per_slug():
    entries = []
    for slug in ("lever", "lever", "pulley"):
        sc, kit, short, tl, *_ = _build("workbench", slug=slug)
        sdir = _sdir(slug)
        sets.dress(sc, kit, short, tl, sdir)
        data = json.loads((sdir / "source" / "sets.json").read_text(encoding="utf-8"))
        assert data["slug"] == slug
        entries.append(_entry(sdir))
    assert entries[0] == entries[1]
    assert entries[0]["props"] != entries[2]["props"]


def _counts(sc):
    return {"objects": sorted(o.name for o in sc.objects), "data objects": len(bpy.data.objects),
            "meshes": len(bpy.data.meshes), "materials": len(bpy.data.materials),
            "collections": len(bpy.data.collections), "worlds": len(bpy.data.worlds)}  # fmt: skip


def test_dress_twice_is_the_same_as_once():
    for name in ("workbench", "outdoor"):
        sc, kit, short, tl, built, _ = _build(name)
        sdir = _sdir()
        sets.dress(sc, kit, short, tl, sdir)
        styles.apply(sc, kit, **{k: built[k] for k in ("target", "size", "key")})
        once = _counts(sc)
        first = _entry(sdir)
        sets.dress(sc, kit, short, tl, sdir)
        assert _counts(sc) == once, name
        assert _entry(sdir) == first
        _no_dot_names()


def _close_scene(main):
    """A second scene "Close" showing the same demo objects through a camera 0.5 m in front of the crate."""
    close = bpy.data.scenes.new("Close")
    close.render.resolution_x, close.render.resolution_y = main.render.resolution_x, main.render.resolution_y
    for obj in main.objects:
        if not bake.is_set(obj) and obj.type != "CAMERA":
            close.collection.objects.link(obj)
    crate = bpy.data.objects["Crate"]
    bpy.context.view_layer.update()
    at = crate.matrix_world.translation
    cam = bpy.data.objects.new("Close camera", bpy.data.cameras.new("Close camera"))
    close.collection.objects.link(cam)
    cam.location = at + Vector((0.0, -(0.31 + 0.5), 0.0))
    cam.rotation_euler = (at - cam.location).to_track_quat("-Z", "Y").to_euler()
    close.camera = cam
    return close


def test_close_up_scene_drops_props_only_there():
    sc, kit, short, tl, *_ = _build("workbench")
    sdir = _sdir()
    main = sets.dress(sc, kit, short, tl, sdir)
    first = _entry(sdir)
    close = _close_scene(sc)
    sets.base(close, kit, "workbench", 3.6)
    got = sets.dress(close, kit, short, tl, sdir)
    print(f"CLOSE kept {got['kept']} dropped {got['dropped']}")
    assert got["dropped"] and all(d["why"] for d in got["dropped"])
    assert len(got["kept"]) < len(main["kept"])
    names = got["kept"] + [d["name"] for d in got["dropped"]]
    assert sorted(names) == sorted(p.name for p in setplace.props("workbench", 3.6))
    assert _entry(sdir, "Close")["dropped"] == got["dropped"]
    assert _entry(sdir) == first  # the main scene keeps its own
    assert [o.name for o in close.objects if bake.set_role(o) == "prop"] == [f"Close {n}" for n in got["kept"]]


def test_approved_arrangement_is_reused():
    sc, kit, short, tl, *_ = _build("workbench")
    sdir = _sdir()
    first = sets.dress(sc, kit, short, tl, sdir)
    assert first["reused"] is False
    saved = _entry(sdir)
    decisions.record(sdir, "storyboard", 1, "approve", "page", current=1)
    real = setplace.storyboard_version
    setplace.storyboard_version = lambda s: 1  # the storyboard as approved (the fixture has no stills to version)
    try:
        bpy.data.objects["StandBase"].location.x += 0.4  # the subject moves: the approved props stay
        again = sets.dress(sc, kit, short, tl, sdir)
        assert again["reused"] is True and again["kept"] == first["kept"], again
        assert _entry(sdir)["props"] == saved["props"]
        for p in saved["props"]:
            obj = bpy.data.objects[f"{sc.name} {p['name']}"]
            if setplace.props("workbench", 3.6)[[q.name for q in setplace.props("workbench", 3.6)].index(p["name"])].zone == "floor":
                assert (obj.matrix_world.translation - Vector(p["at"])).length < 1e-5
        # The storyboard has changed since it was approved: placed again, and the warning says why.
        setplace.storyboard_version = lambda s: 2
        moved = sets.dress(sc, kit, short, tl, sdir)
        assert moved["reused"] is False and any("approv" in w for w in moved["warnings"])
        # replace=[name]: the rest reused, that one placed again.
        decisions.record(sdir, "storyboard", 2, "approve", "page", current=2)
        some = sets.dress(sc, kit, short, tl, sdir, replace=["toolbox"])
        assert some["reused"] is True
    finally:
        setplace.storyboard_version = real


def test_studio_backdrop_shows_the_kit_background_under_the_style_view():
    for engine in ("eevee", "cycles"):
        sc, kit, short, tl, built, _ = _build("studio", engine=engine)
        sets.dress(sc, kit, short, tl, None)
        styles.apply(sc, kit, **{k: built[k] for k in ("target", "size", "key")})
        assert colour.flat_backdrop(sc.world) is True
        png = OUT / f"studio_toy_{engine}.png"
        render_still(sc, 1, png, preset=PRESET, engine=engine, file_format="PNG")
        got = colour.dominant_colour(read_png(png))
        want = looks.backdrop("toy", kit)
        de = colour.delta_e(want, got)
        print(f"STUDIO {engine} {colour.srgb_to_hex(got)} want {want} dE {de:.2f}")
        assert de <= 2, (engine, de)


def test_set_worlds_are_environments_but_the_studio():
    for name in ("workbench", "graph", "outdoor"):
        sc, *_ = _build(name)
        assert sc.world.get("bsa_backdrop") == "set" and colour.flat_backdrop(sc.world) is False, name
        assert set(sc.world["bsa_strength"]) == {"Lit"}


def test_sets_and_styles_never_change_the_engine():
    for engine, want in (("eevee", None), ("cycles", "CYCLES")):
        for name in setplace.SETS:
            sc, kit, short, tl, built, _ = _build(name, engine=engine)
            before = sc.render.engine
            assert want is None or before == want
            sets.dress(sc, kit, short, tl, None)
            styles.apply(sc, kit, **{k: built[k] for k in ("target", "size", "key")})
            styles.reapply(sc, kit, style="toon")
            sets.dress(sc, kit, short, tl, None)
            assert sc.render.engine == before, (engine, name)
            assert sets.clear(sc) > 0 and "bsa_set" not in sc
            assert not [o for o in sc.objects if bake.is_set(o)]
            assert sc.render.engine == before


def test_clear_takes_the_set_away():
    sc, kit, short, tl, *_ = _build("workbench")
    sets.dress(sc, kit, short, tl, None)
    n = len([o for o in sc.objects if bake.is_set(o)])
    assert sets.clear(sc) == n
    assert bpy.data.collections.get(f"{sc.name} set") is None
    assert bpy.data.collections.get(f"{sc.name} set props") is None
    assert not [m for m in bpy.data.materials if m.name.startswith(f"{sc.name} set ")]
    sets.base(sc, kit, "graph", 3.6)  # another set after: no leftovers, no .001
    _no_dot_names()


def test_storyboard_version_can_be_worked_out_in_blender():
    """setplace.reusable reads the storyboard's version through the review snapshot inside Blender's Python: a short
    folder without a short.json can't be versioned, so a saved arrangement there is placed again with the reason."""
    from bsa.review import snapshot  # noqa: F401 - importable in Blender

    sdir = _sdir()
    setplace.save_scene(sdir, "Scene", {"set": "workbench", "floor": [0, 0, 1, 1], "props": [], "dropped": []})
    entry, why = setplace.reusable(sdir, "Scene", "workbench", 3.6)
    assert entry is None and why, why


def _floor_colour(sc, point, engine: str, tag: str) -> np.ndarray:
    """The median sRGB of a 5x5 patch round the world `point` in a 25% render."""
    from bpy_extras.object_utils import world_to_camera_view

    png = OUT / f"{tag}_{engine}.png"
    render_still(sc, 1, png, preset=PRESET, percent=25, engine=engine, file_format="PNG")
    img = read_png(png)[..., :3].astype(np.float64) / 255.0
    h, w = img.shape[:2]
    uv = world_to_camera_view(sc, sc.camera, Vector(point))
    x, y = round(uv.x * w - 0.5), round((1.0 - uv.y) * h - 0.5)
    return np.median(img[y - 2 : y + 3, x - 2 : x + 3].reshape(-1, 3), axis=0)


def test_toon_set_floors_match_between_engines():
    """The toon dark step of a set surface is never brighter than its colour in any channel, so Cycles' cel (which can
    only add light over the step) shows a saturated wood or grass floor as EEVEE does."""
    for name in ("workbench", "outdoor"):
        got = {}
        for engine in ("eevee", "cycles"):
            sc, kit, _short, _tl, built, _ = _build(name, engine=engine, style="toon")
            styles.apply(sc, kit, **{k: built[k] for k in ("target", "size", "key")})
            got[engine] = _floor_colour(sc, (-2.4, -2.0, 0.0), engine, f"toon_floor_{name}")
        de = colour.delta_e(got["eevee"], got["cycles"])
        print(f"TOON FLOOR {name} {colour.srgb_to_hex(got['eevee'])} {colour.srgb_to_hex(got['cycles'])} dE {de:.2f}")
        assert de <= 2, (name, de)


def _hue(rgb) -> float:
    _l, a, b = colour.srgb_to_lab(rgb)
    return math.degrees(math.atan2(b, a))


def test_outdoor_ground_keeps_its_colour_in_the_lit_styles():
    """The outdoor sky is for camera rays only: what lights the set is near-neutral, so the grass under toy and clay
    reads as the ground colour in both engines (not shifted toward the sky's blue), not teal. Toy is held to its room
    limit too; clay's grass is paler than that (EEVEE about 12.5, Cycles 14.4) because the approved AgX look
    desaturates a saturated green: under the Standard view the same grass is 2.2 off, so the hue is what clay pins."""
    for style in ("toy", "clay"):
        for engine in ("eevee", "cycles"):
            sc, kit, _short, _tl, built, _ = _build("outdoor", engine=engine, style=style)
            styles.apply(sc, kit, **{k: built[k] for k in ("target", "size", "key")})
            got = _floor_colour(sc, (-2.4, -2.0, 0.0), engine, f"outdoor_ground_{style}")
            want = looks.lit_expect(style, colour.set_colours(kit)["ground"], kit, False)
            de = colour.delta_e(want, got)
            turn = abs(_hue(got) - _hue(colour.hex_to_srgb(want)))
            print(f"GROUND {style} {engine} {colour.srgb_to_hex(got)} want {want} dE {de:.2f} hue {turn:.1f}")
            assert style == "clay" or de <= looks.lit_limit(style, engine, room=True), (style, engine, de)
            assert turn <= 10, (style, engine, turn)


def _sample_framing(sc) -> None:
    """The approved samples' framing: the lever alone (the demo's stand and gears hidden), about 60% of the frame's
    width, seen from about 16 degrees above."""
    for name in ("StandBase", "StandPlate", "GearBig", "GearSmall", "Axle0", "Axle1"):
        bpy.data.objects[name].hide_render = True
    cam = sc.camera
    cam.data.shift_y = 0.0
    cam.location = (0.15, -8.0, 3.0)
    cam.rotation_euler = (Vector((0.15, 0.0, 0.6)) - cam.location).to_track_quat("-Z", "Y").to_euler()


def test_a_sample_framing_gets_a_dressed_set():
    """At the framing the approved samples use, the workbench keeps tools and bench-end objects and the graph set
    keeps its tools: a normal short gets a dressed set, not an empty one."""
    for name, floor_least, least in (("workbench", 2, 4), ("graph", 2, 2)):
        sc, kit, short, tl, *_ = _build(name)
        _sample_framing(sc)
        got = sets.dress(sc, kit, short, tl, None)
        floor = [n for n in got["kept"] if next(p for p in setplace.props(name, 3.6) if p.name == n).zone == "floor"]
        print(f"SAMPLE {name} kept {got['kept']} dropped {got['dropped']}")
        assert len(floor) >= floor_least and len(got["kept"]) >= least, (name, got)
        _props, hits = _clear_check(sc, kit, short, tl, _shot_frames(tl, "s1"))
        assert not hits, (name, hits)


def test_set_none_takes_a_set_away():
    for styled in (False, True):
        sc, kit, short, tl, built, _ = _build("workbench")
        sets.dress(sc, kit, short, tl, None)
        if styled:
            styles.apply(sc, kit, **{k: built[k] for k in ("target", "size", "key")})
        assert sets.base(sc, kit, "none", 3.6) == {}
        assert not [o for o in sc.objects if bake.is_set(o)] and "bsa_set" not in sc
        assert sc.world.get("bsa_backdrop") == "paper" and colour.flat_backdrop(sc.world) is True
        _no_dot_names()


def test_taking_a_set_away_leaves_no_world_behind():
    """A set's world ("<scene> set world") goes when nothing uses it any more: after base("none") (the style's paper
    backdrop back, styled or not) and after another set's studio paper replaces it."""
    for styled in (False, True):
        sc, kit, _short, _tl, built, _ = _build("outdoor")
        if styled:
            styles.apply(sc, kit, **{k: built[k] for k in ("target", "size", "key")})
        assert bpy.data.worlds.get(f"{sc.name} set world") is not None
        sets.base(sc, kit, "none", 3.6)
        assert bpy.data.worlds.get(f"{sc.name} set world") is None, styled
    sc, kit, *_ = _build("graph")
    sets.base(sc, kit, "studio", 3.6)
    assert bpy.data.worlds.get(f"{sc.name} set world") is None


def test_clear_puts_film_transparent_back():
    """A set makes the film opaque (its world is the background); clear puts back what the scene had."""
    for was in (True, False):
        bpy.ops.wm.read_factory_settings(use_empty=True)
        sc = bpy.context.scene
        setup_scene(sc, two_shot(sc.name, "lever"), "eevee", "still")
        sc.render.film_transparent = was
        kit = _kit()
        for name in ("outdoor", "workbench"):  # a second set over the first keeps the first one's stamp
            sets.base(sc, kit, name, 3.6)
            assert sc.render.film_transparent is False
        sets.clear(sc)
        assert sc.render.film_transparent is was, was
