"""Check tags on objects (bsa_tags), including per-shot heroes: hero:s03,s04."""

import pytest

from bsa.blender import bake


class Obj(dict):
    """Stands in for a Blender object: tags live in a custom property."""


def test_plain_tags_round_trip():
    o = Obj()
    bake.tag(o, "hero", "grounded", "contact:Stand")
    assert bake.tags(o) == ["hero", "grounded", "contact:Stand"]


def test_per_shot_hero_tag_keeps_its_shot_list():
    o = Obj()
    bake.tag(o, "hero:s03,s04", "contact:Light rod")
    assert bake.tags(o) == ["hero:s03,s04", "contact:Light rod"]
    assert bake.is_hero(o, "s03") and bake.is_hero(o, "s04") and not bake.is_hero(o, "s01")
    assert bake.is_hero(o)  # a hero somewhere
    plain = Obj()
    bake.tag(plain, "hero")
    assert bake.is_hero(plain, "s01") and bake.is_hero(plain, "s99")
    assert not bake.is_hero(Obj(), "s01") and not bake.is_hero(Obj())


def test_contact_tag_with_a_depth_round_trips():
    """contact:<other>:<max depth in metres> for a part that is deep by design (the checks read the depth)."""
    o = Obj()
    bake.tag(o, "contact:Clock backboard:0.015", "contact:Yoke")
    assert bake.tags(o) == ["contact:Clock backboard:0.015", "contact:Yoke"]


def test_overlay_tag():
    o = Obj()
    bake.tag(o, "hero:s01,s02", "overlay")
    assert bake.tags(o) == ["hero:s01,s02", "overlay"]
    assert bake.is_overlay(o) and not bake.is_overlay(Obj())
    assert not bake.may_exit(o)


def test_overlay_may_exit_tag():
    """A falling stream that leaves the frame on purpose: still an overlay, but frame_edge does not warn."""
    o = Obj()
    bake.tag(o, "hero:s01,s02", "overlay:may-exit", "grounded")
    assert bake.tags(o) == ["hero:s01,s02", "overlay:may-exit", "grounded"]  # not read as another shot id
    assert bake.is_overlay(o) and bake.may_exit(o) and bake.is_hero(o, "s02") and not bake.is_hero(o, "overlay")


def test_every_tag_is_documented():
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent / "skills" / "make-short" / "playbook"
    checks, build = (root / "07-checks.md").read_text(encoding="utf-8"), (root / "06-build.md").read_text(encoding="utf-8")
    for tag in bake.TAGS:
        assert f"`{tag}" in checks and f'"{tag}' in build, tag


def test_other_tags_still_refuse_commas():
    with pytest.raises(ValueError):
        bake.tag(Obj(), "contact:a,b")
    with pytest.raises(ValueError):
        bake.tag(Obj(), "hero:")


def test_category_tag_parses_after_a_hero_list():
    """category:<name> starts a new tag after a hero:<shot ids> list; bake.category reads its name, lower case."""
    o = Obj()
    bake.tag(o, "hero:s01,s02", "category:Spark")
    assert bake.tags(o) == ["hero:s01,s02", "category:Spark"]
    assert bake.category(o) == "spark" and bake.is_hero(o, "s02") and not bake.is_hero(o, "category:Spark")
    assert bake.category(Obj()) is None
    o2 = Obj()
    o2["bsa_tags"] = "hero:s01,s02,category:spark"
    assert bake.tags(o2) == ["hero:s01,s02", "category:spark"] and bake.category(o2) == "spark"
    o3 = Obj()
    o3["bsa_tags"] = "category: , grounded"  # an empty name is no category
    assert bake.category(o3) is None


def test_set_tags_parse_and_set_objects_cannot_be_heroes():
    """set:<role> marks a set's scenery (floor, wall, backdrop, prop); lines:off keeps a part out of the ink lines.
    A set object is never a hero, whichever order the tags come in."""
    for role in ("floor", "wall", "backdrop", "prop"):
        o = Obj()
        bake.tag(o, f"set:{role}", "grounded")
        assert bake.tags(o) == [f"set:{role}", "grounded"]
        assert bake.set_role(o) == role and bake.is_set(o)
    assert bake.set_role(Obj()) is None and not bake.is_set(Obj())
    for t in ("set:floor", "set:wall", "set:backdrop", "set:prop", "lines:off"):
        assert t in bake.TAGS
    o = Obj()
    bake.tag(o, "hero:s01,s02", "lines:off")
    assert bake.tags(o) == ["hero:s01,s02", "lines:off"] and not bake.is_set(o)
    o2 = Obj()
    o2["bsa_tags"] = "set:prop,lines:off"
    assert bake.tags(o2) == ["set:prop", "lines:off"] and bake.set_role(o2) == "prop"
    o3 = Obj()
    o3["bsa_tags"] = "hero:s01,s02,lines:off"  # lines:off starts a tag: not read as another shot id
    assert bake.tags(o3) == ["hero:s01,s02", "lines:off"] and not bake.is_hero(o3, "lines:off")
    with pytest.raises(ValueError, match="set objects are scenery, never heroes"):
        floor = Obj()
        bake.tag(floor, "set:floor")
        bake.tag(floor, "hero")
    with pytest.raises(ValueError, match="set objects are scenery, never heroes"):
        bake.tag(Obj(), "hero:s01", "set:wall")
    with pytest.raises(ValueError, match="floor"):
        bake.tag(Obj(), "set:ceiling")
