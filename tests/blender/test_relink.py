"""BLENDER relink: a short's .blend re-pointed from the old workspace root to the new one after a move."""

import os
import shutil
import tempfile
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
