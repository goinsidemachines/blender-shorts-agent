"""BLENDER look inside Blender (runs via runner.py): the look preview's three frames with the kit's cards, another
style or set in memory only (the .blend never saved), the engine the user's. A made-up kit (tests/kitfix.py) and the
demo lever in a three-shot short: s1 and s2 in the main scene, s3 in a close-up scene."""

import hashlib
import json
import math
import os
import sys
import tempfile
import types
from pathlib import Path

import bpy
import numpy as np
from mathutils import Vector

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

TMP = Path(tempfile.mkdtemp())
HOME = TMP / "home"
HOME.mkdir()
os.environ["BSA_HOME"] = str(HOME)
WS = TMP / "ws"
SDIR = WS / "lever"
(SDIR / "source").mkdir(parents=True)
(WS / "other" / "source").mkdir(parents=True)  # a sibling short without a look preview

from kitfix import KIT

import bsa_blender
from bsa import colour, settings
from bsa.blender import bake, demo, sets, styles
from bsa.blender import scene as scene_mod
from bsa.blender.pixels import read_png
from bsa.kit import kit_from
from bsa.short import Line, Short, Shot, save_short
from bsa.timeline import build_timeline, timeline_hash, to_json

scene_mod.use_gpu = lambda: "NONE"  # Cycles on the CPU, as the plan pins tests
scene_mod.PRESETS["still"] = {"percent": 10, "cycles": {"samples": 32, "denoise": False}, "eevee": {"samples": 8}}
SETTINGS = HOME / "settings.json"
BLEND = SDIR / "blend" / "short.blend"
BOX = "#1B2A3A"  # the kit's caption box


def _settings(**extra) -> None:
    SETTINGS.write_text(json.dumps({"workspace": str(WS), "brand": KIT, "render": {"engine": "eevee"}, **extra}),
                        encoding="utf-8")  # fmt: skip


def _short() -> Short:
    shots = [Shot(id="s1", title="The lever", picture="the lever rocks", scene="Scene", min_s=1.0, title_card="A lever",
                  labels=[{"text": "Crate", "near": "Crate"}]),
             Shot(id="s2", title="Rocking", picture="it rocks back", scene="Scene", min_s=1.0),
             Shot(id="s3", title="Close", picture="the crate up close", scene="Close", min_s=1.0)]  # fmt: skip
    lines = [Line("l1", "A small weight lifts a heavy crate.", "s1"), Line("l2", "It rocks back.", "s2"),
             Line("l3", "Look closer.", "s3")]  # fmt: skip
    return Short(slug="lever", title="Lever", length_s=3, shots=shots, lines=lines, style={})


def _close_scene(main):
    close = bpy.data.scenes.new("Close")
    r = close.render
    r.resolution_x, r.resolution_y, r.fps = main.render.resolution_x, main.render.resolution_y, main.render.fps
    close.view_settings.view_transform = main.view_settings.view_transform
    for obj in main.objects:
        if not bake.is_set(obj) and obj.type not in ("CAMERA", "LIGHT"):
            close.collection.objects.link(obj)
    crate = bpy.data.objects["Crate"]
    bpy.context.view_layer.update()
    at = crate.matrix_world.translation
    cam = bpy.data.objects.new("Close camera", bpy.data.cameras.new("Close camera"))
    close.collection.objects.link(cam)
    cam.location = at + Vector((0.0, -2.5, 0.6))
    cam.rotation_euler = (at - cam.location).to_track_quat("-Z", "Y").to_euler()
    close.camera = cam
    return close


_BUILT = []


def _fixture() -> None:
    """The short folder and its blend/short.blend (built once), opened fresh."""
    _settings()
    if not _BUILT:
        short = _short()
        save_short(short, SDIR / "source" / "short.json")
        tl = build_timeline(short, None, settings.has_end_card(settings.load_settings(), short))
        saved = json.loads(json.dumps(to_json(tl)))
        (SDIR / "source" / "timeline.json").write_text(json.dumps(saved), encoding="utf-8")
        bpy.ops.wm.read_factory_settings(use_empty=True)
        sc = bpy.context.scene
        sc.name = "Scene"
        scene_mod.setup_scene(sc, short, "eevee", "still")
        kit = kit_from(KIT)
        sets.base(sc, kit, "workbench", 3.6)
        built = demo.lever(sc, kit)
        pivot = bpy.data.objects["LeverPivot"]
        for f, deg in ((1, 11.0), (30, -11.0), (60, 11.0), (90, -11.0)):
            pivot.rotation_euler = (0.0, math.radians(deg), 0.0)
            pivot.keyframe_insert("rotation_euler", frame=f)
        aim = {k: built[k] for k in ("target", "size", "key")}
        styles.apply(sc, kit, **aim)
        close = _close_scene(sc)
        sets.base(close, kit, "workbench", 3.6)
        styles.apply(close, kit, **aim)
        for scene in (sc, close):
            sets.dress(scene, kit, short, tl, SDIR)
            scene["bsa_timeline"] = timeline_hash(saved)
        BLEND.parent.mkdir(parents=True, exist_ok=True)
        bpy.ops.wm.save_as_mainfile(filepath=str(BLEND))
        _BUILT.append(True)
    bpy.ops.wm.open_mainfile(filepath=str(BLEND))


def _args(**kw):
    return types.SimpleNamespace(**{"short": str(SDIR), "style": None, "set": None, "engine": None, **kw})


def _look() -> dict:
    return json.loads((SDIR / "stills" / "look.json").read_text(encoding="utf-8"))


def _caption_boxes() -> dict[int, list]:
    """{frame: [the caption cards' box rects showing then]}, where the kit's layout puts them (kitlayout.build, as
    the edit and the look do)."""
    import dataclasses

    from bsa import cards, fonts, kitlayout
    from bsa import kit as kit_mod

    short = bsa_blender._load(SDIR)
    tl, _ = bsa_blender._timeline(SDIR, short)
    kit = kit_mod.kit_for(settings.load_settings(), short)
    font, _ = cards.card_font_and_note(kit, fonts.font_caches(SDIR))
    bare = dataclasses.replace(short, shots=[dataclasses.replace(s, labels=[]) for s in short.shots])
    ov = kitlayout.build(bare, tl, kit, font)
    caps = [p for p in ov.cards if p.channel == kitlayout.CH["caption"] and p.at is not None]
    return {f: [(p.at.x, p.at.y, p.spec.w, p.spec.box_h) for p in caps if p.f0 <= f <= p.f1]
            for f in range(1, tl.n_frames + 1)}


def _box_region(px: np.ndarray, box) -> np.ndarray:
    """The pixels of the caption box (x, y, w, box height: not its shadow), 12 px in from its rounded edges."""
    x, y, w, h = box
    return px[round(y) + 12 : round(y + h) - 12, round(x) + 12 : round(x + w) - 12, :3]


def test_look_writes_three_frames_with_cards():
    _fixture()
    fonts_before = set(bpy.data.fonts.keys())
    stale = SDIR / "stills" / "look" / "s9.jpg"  # a shot an earlier run showed, gone from short.json since
    stale.parent.mkdir(parents=True, exist_ok=True)
    stale.write_bytes(b"old")
    out = bsa_blender.cmd_look(_args())
    assert not stale.exists(), "a stale look frame was kept"
    data = _look()
    assert [f["shot"] for f in data["frames"]] == ["s1", "s2", "s3"], data
    assert data["style"] == "toon" and data["set"] == "workbench", data
    assert data["built"] == {"style": "toon", "set": "workbench"}, data
    assert data["swatches"] and data["swatches"][0]["name"] == "background", data  # the colours rendered with
    assert data["engine"] == "eevee" and data["seconds_per_frame"] > 0, data
    assert data["estimates"] is None and data["first"] is True and data["sheet"] == "look/kit.png", data
    assert out["frames"] == data["frames"]
    assert not [w for w in data["warnings"] if "label" in w], data["warnings"]  # the near label was placed
    assert (SDIR / "stills" / "look" / "kit.png").is_file()
    boxes = _caption_boxes()
    for f in data["frames"]:
        jpg = SDIR / "stills" / f["file"]
        assert jpg.is_file(), jpg
        px = read_png(jpg)
        assert px.shape[:2] == (1920, 1080), px.shape
        assert px[:1000, :, :3].reshape(-1, 3).std(axis=0).max() > 5, f"{jpg.name}: no picture under the cards"
        rects = boxes[f["frame"]]
        assert len(rects) == 1, (f, rects)
        got = colour.dominant_colour(_box_region(px, rects[0]))
        assert colour.delta_e(got, BOX) <= 4, (jpg.name, colour.srgb_to_hex(got))
    assert not [s for s in bpy.data.scenes if s.name.startswith(("LookFrame", "KitSample"))]
    assert set(bpy.data.fonts.keys()) == fonts_before, list(bpy.data.fonts.keys())
    print("LOOK FRAMES", [str(SDIR / "stills" / f["file"]) for f in data["frames"]])


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_look_with_another_style_leaves_the_blend_unchanged():
    _fixture()
    bsa_blender.cmd_look(_args())
    toon = read_png(SDIR / "stills" / "look" / "s1.jpg").astype(int)
    mtime, digest = BLEND.stat().st_mtime_ns, _digest(BLEND)
    sets_json = (SDIR / "source" / "sets.json").read_bytes()
    bsa_blender.cmd_look(_args(style="pencil", set="outdoor"))
    data = _look()
    assert data["style"] == "pencil" and data["set"] == "outdoor", data
    assert data["built"] == {"style": "toon", "set": "workbench"}, data
    pencil = read_png(SDIR / "stills" / "look" / "s1.jpg").astype(int)
    assert np.abs(pencil[:1000] - toon[:1000]).mean() > 3, "the preview did not change style"
    assert BLEND.stat().st_mtime_ns == mtime and _digest(BLEND) == digest  # never saved
    assert (SDIR / "source" / "sets.json").read_bytes() == sets_json  # the built arrangement is kept
    for name in ("Scene", "Close"):  # the open file is the saved one again
        sc = bpy.data.scenes[name]
        assert sc["bsa_style"] == "toon" and sc["bsa_set"] == "workbench", name
    print("PENCIL FRAME", SDIR / "stills" / "look" / "s1.jpg")


def test_look_never_changes_the_engine():
    _fixture()
    engines = {s.name: s.render.engine for s in bpy.data.scenes}
    _settings(render={"engine": "cycles"})
    before = SETTINGS.read_bytes()
    bsa_blender.cmd_look(_args())
    assert _look()["engine"] == "cycles"
    assert {s.name: s.render.engine for s in bpy.data.scenes} == engines
    sibling = WS / "other" / "stills" / "look.json"
    sibling.parent.mkdir(parents=True, exist_ok=True)
    sibling.write_text("{}", encoding="utf-8")
    try:
        bsa_blender.cmd_look(_args(engine="eevee"))
    finally:
        sibling.unlink()
    assert _look()["engine"] == "eevee" and _look()["first"] is False
    assert {s.name: s.render.engine for s in bpy.data.scenes} == engines
    assert SETTINGS.read_bytes() == before
    _settings(render={"engine": "ask-always"})
    try:
        bsa_blender.cmd_look(_args())
    except bsa_blender.CliError as exc:
        assert "needs an engine" in str(exc), exc
    else:
        raise AssertionError("no engine chosen: look must refuse")


def test_look_without_a_kit_refuses():
    _fixture()
    SETTINGS.write_text(json.dumps({"workspace": str(WS), "brand": {"mode": "fresh"}, "render": {"engine": "eevee"}}),
                        encoding="utf-8")  # fmt: skip
    try:
        bsa_blender.cmd_look(_args())
    except bsa_blender.CliError as exc:
        assert str(exc) == "the look preview needs a brand kit: brand.style is not set", exc
    else:
        raise AssertionError("without a kit look must refuse")
