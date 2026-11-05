"""BLENDER relink: a short's .blend re-pointed from the old workspace root to the new one after a move."""

import os
import shutil
import tempfile
import wave
from pathlib import Path

os.environ["BSA_HOME"] = tempfile.mkdtemp()  # never the user's own settings

import bpy

import bsa_blender

PX = 4


def _png(path: Path) -> None:
    img = bpy.data.images.new("tmp_png", PX, PX)
    img.filepath_raw = str(path)
    img.file_format = "PNG"
    img.save()
    bpy.data.images.remove(img)


def _relink(*argv: str) -> dict:
    args = bsa_blender.build_parser().parse_args(["relink", *argv])
    return args.fn(args)


def _workspace():
    """old/ with tex.png, a sibling old2/ sharing its prefix, and old/s/blend/short.blend that uses old/tex.png by
    absolute path, a //relative image and old2's image; the scene's render settings are recorded."""
    tmp = Path(tempfile.mkdtemp())
    old, sibling = tmp / "Shorts", tmp / "Shorts2"
    for d in (old / "s" / "source", old / "s" / "blend", old / "s" / "art", sibling):
        d.mkdir(parents=True)
    _png(old / "tex.png")
    _png(old / "s" / "art" / "rel.png")
    _png(sibling / "other.png")
    bpy.ops.wm.read_factory_settings(use_empty=True)
    a = bpy.data.images.load(str(old / "tex.png"))
    a.name = "abs"
    rel = bpy.data.images.load(str(old / "s" / "art" / "rel.png"))
    rel.name = "rel"
    rel.filepath = "//../art/rel.png"
    side = bpy.data.images.load(str(sibling / "other.png"))
    side.name = "sibling"
    if os.name == "nt":  # another case of the old root is the same folder on Windows
        shout = bpy.data.images.load(str(old / "tex.png"), check_existing=False)
        shout.name = "shout"
        shout.filepath = str(old).upper() + "\\tex.png"
    for img in bpy.data.images:
        img.use_fake_user = True
    sc = bpy.context.scene or bpy.data.scenes.new("Scene")
    render = (sc.render.engine, sc.render.resolution_x, sc.render.fps)
    blend = old / "s" / "blend" / "short.blend"
    bpy.ops.wm.save_as_mainfile(filepath=str(blend), relative_remap=False)
    bpy.ops.wm.read_factory_settings(use_empty=True)
    new = tmp / "Moved"
    shutil.copytree(old, new)
    return old, new, sibling, render


def test_relink_repoints_an_image_under_the_old_root():
    old, new, sibling, render = _workspace()
    blend = new / "s" / "blend" / "short.blend"
    before = blend.stat().st_mtime_ns
    out = _relink(str(new / "s"), "--from", str(old))
    assert out["saved"] is True and out["missing"] == [], out
    names = sorted(r["name"] for r in out["relinked"])
    assert names == (["abs", "shout"] if os.name == "nt" else ["abs"]), out
    row = next(r for r in out["relinked"] if r["name"] == "abs")
    assert row["type"] == "image" and row["to"] == str(new / "tex.png")
    assert os.path.normcase(row["from"]) == os.path.normcase(str(old / "tex.png"))
    assert blend.stat().st_mtime_ns != before

    bpy.ops.wm.open_mainfile(filepath=str(blend))  # what was saved
    imgs = bpy.data.images
    assert Path(bpy.path.abspath(imgs["abs"].filepath)) == new / "tex.png"
    rel = imgs["rel"].filepath  # already relative to the .blend: left alone
    assert rel.startswith("//") and Path(bpy.path.abspath(rel)).resolve() == new / "s" / "art" / "rel.png", rel
    assert "rel" not in names
    assert Path(imgs["sibling"].filepath) == sibling / "other.png"  # Shorts2 is not inside Shorts
    if os.name == "nt":
        assert Path(imgs["shout"].filepath) == new / "tex.png"
    sc = bpy.context.scene
    assert (sc.render.engine, sc.render.resolution_x, sc.render.fps) == render  # no render setting touched


def _wav(path: Path) -> None:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(8000)
        w.writeframes(b"\0\0" * 800)


def _rich_workspace():
    """A compressed short.blend whose other paths are under old/: a sound, an external text, an image strip's folder,
    the render output, a File Output node, a cloth point cache and a fluid cache."""
    tmp = Path(tempfile.mkdtemp())
    old = tmp / "Shorts"
    s = old / "s"
    for d in (s / "source", s / "blend", s / "frames", s / "audio"):
        d.mkdir(parents=True)
    _png(s / "frames" / "0001.png")
    _wav(s / "audio" / "hum.wav")
    (s / "source" / "notes.py").write_text("# notes\n", encoding="utf-8")
    bpy.ops.wm.read_factory_settings(use_empty=True)
    sc = bpy.context.scene
    snd = bpy.data.sounds.load(str(s / "audio" / "hum.wav"))
    snd.name, snd.use_fake_user = "hum", True
    bpy.data.texts.load(str(s / "source" / "notes.py")).name = "notes"
    se = sc.sequence_editor_create()
    strips = se.strips if hasattr(se, "strips") else se.sequences
    strips.new_image("frames", str(s / "frames" / "0001.png"), 1, 1)
    sc.render.filepath = str(s / "renders" / "f_")
    tree = bpy.data.node_groups.new("Comp", "CompositorNodeTree")
    if hasattr(sc, "compositing_node_group"):
        sc.compositing_node_group = tree
    out = tree.nodes.new("CompositorNodeOutputFile")
    out.name = "Out"
    if hasattr(out, "directory"):
        out.directory = str(s / "comp") + os.sep
    else:
        out.base_path = str(s / "comp") + os.sep
    ob = bpy.data.objects.new("Cloth", bpy.data.meshes.new("m"))
    sc.collection.objects.link(ob)
    ob.modifiers.new("cloth", "CLOTH").point_cache.filepath = str(s / "cache")
    dom = bpy.data.objects.new("Domain", bpy.data.meshes.new("d"))
    sc.collection.objects.link(dom)
    fluid = dom.modifiers.new("fluid", "FLUID")
    fluid.fluid_type = "DOMAIN"
    fluid.domain_settings.cache_directory = str(s / "fluid")
    render = (sc.render.engine, sc.render.resolution_x, sc.render.fps, sc.render.image_settings.file_format)
    bpy.ops.wm.save_as_mainfile(filepath=str(s / "blend" / "short.blend"), relative_remap=False, compress=True)
    bpy.ops.wm.read_factory_settings(use_empty=True)
    new = tmp / "Moved"
    shutil.copytree(old, new)
    return old, new, render


def test_relink_repoints_strips_texts_outputs_and_caches():
    old, new, render = _rich_workspace()
    s = new / "s"
    blend = s / "blend" / "short.blend"
    out = _relink(str(s), "--from", str(old))
    assert out["saved"] is True and out["missing"] == [], out
    kinds = sorted(r["type"] for r in out["relinked"])
    assert kinds == ["file_output", "fluid_cache", "point_cache", "render_output", "sound", "strip", "text"], out
    assert not (s / "blend" / "short.blend1").exists()  # no backup beside it
    assert blend.read_bytes()[:4] == b"\x28\xb5\x2f\xfd"  # still compressed

    bpy.ops.wm.open_mainfile(filepath=str(blend))
    sc = bpy.context.scene
    assert Path(bpy.data.sounds["hum"].filepath) == s / "audio" / "hum.wav"
    assert Path(bpy.data.texts["notes"].filepath) == s / "source" / "notes.py"
    se = sc.sequence_editor
    strip = (se.strips_all if hasattr(se, "strips_all") else se.sequences_all)["frames"]
    assert Path(strip.directory) == s / "frames" and strip.directory.endswith(os.sep)
    assert sc.render.filepath == str(s / "renders" / "f_")
    node = bpy.data.node_groups["Comp"].nodes["Out"]
    folder = node.directory if hasattr(node, "directory") else node.base_path
    assert Path(folder) == s / "comp"
    assert Path(bpy.data.objects["Cloth"].modifiers["cloth"].point_cache.filepath) == s / "cache"
    assert Path(bpy.data.objects["Domain"].modifiers["fluid"].domain_settings.cache_directory) == s / "fluid"
    got = (sc.render.engine, sc.render.resolution_x, sc.render.fps, sc.render.image_settings.file_format)
    assert got == render  # only the output path changed


def test_relink_saves_nothing_when_nothing_points_at_the_old_root():
    old, new, _sibling, _render = _workspace()
    _relink(str(new / "s"), "--from", str(old))
    blend = new / "s" / "blend" / "short.blend"
    before = blend.stat().st_mtime_ns
    out = _relink(str(new / "s"), "--from", str(old))  # a second run: everything already points at new
    assert out == {"relinked": [], "missing": [], "saved": False}
    assert blend.stat().st_mtime_ns == before


def test_relink_lists_a_repointed_file_that_is_not_there():
    old, new, _sibling, _render = _workspace()
    (new / "tex.png").unlink()
    out = _relink(str(new / "s"), "--from", str(old), "--to", str(new))
    assert out["saved"] is True
    assert str(new / "tex.png") in out["missing"]


def test_relink_needs_the_short_blend():
    tmp = Path(tempfile.mkdtemp())
    (tmp / "s" / "source").mkdir(parents=True)
    try:
        _relink(str(tmp / "s"), "--from", str(tmp / "old"))
    except bsa_blender.CliError as exc:
        assert "short.blend" in str(exc)
    else:
        raise AssertionError("relink without a .blend should fail")
