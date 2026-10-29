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
