import re
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent

SCANNED = ("styles", "sets", "look", "demo", "lookframes")
WRITES_ENGINE = re.compile(r"\.engine\s*=(?!=)")
ENGINE_LITERAL = re.compile(r"(?<![=!<>])=\s*[\"'](?:BLENDER_EEVEE\w*|CYCLES)[\"']")


def test_no_style_or_set_code_writes_the_render_engine():
    """The engine is always the user's choice: no style, set or preview code assigns it."""
    found = [ROOT / "bsa" / "blender" / f"{name}.py" for name in SCANNED]
    found = [p for p in found if p.exists()]
    assert {p.stem for p in found} >= {"styles", "look", "demo"}
    for path in found:
        text = path.read_text(encoding="utf-8")
        assert not WRITES_ENGINE.search(text), f"{path.name} writes an engine"
        assert not ENGINE_LITERAL.search(text), f"{path.name} assigns an engine id"


def test_the_scan_catches_an_engine_write():
    assert WRITES_ENGINE.search('scene.render.engine = "CYCLES"')
    assert WRITES_ENGINE.search("r.engine=x")
    assert not WRITES_ENGINE.search('scene.render.engine == "CYCLES"')
    assert ENGINE_LITERAL.search('engine = "BLENDER_EEVEE_NEXT"')
    assert not ENGINE_LITERAL.search('if engine == "CYCLES":')


@pytest.mark.blender
def test_styles_in_blender(blender_run, bsa_home):
    result = blender_run(HERE / "blender" / "test_styles.py", timeout=1200)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "user decrement error" not in result.stdout + result.stderr  # a removed lineset left its collection alone
    assert "strokes set empty" not in result.stdout + result.stderr  # the patch probes render no Freestyle
    for name in (
        "test_lit_colour_matches_the_kit_in_both_engines",
        "test_lit_colour_matches_without_a_floor_too",
        "test_parts_take_the_style_whatever_their_kind_and_sets_stay_matte",
        "test_a_key_copy_goes_on_the_key_object_only",
        "test_without_a_kit_styles_change_nothing",
        "test_apply_twice_is_the_same_as_once",
        "test_reapply_switches_style_and_back",
        "test_materials_not_from_kit_material_are_kept_and_named",
        "test_match_engine_follows_the_render_engine_not_the_build",
        "test_engine_is_never_changed_by_apply",
        "test_toon_shadow_side_is_the_fixed_dark_step",
        "test_toon_has_a_material_output_per_engine_and_blueprint_one",
        "test_line_styles_reapplied_keep_one_lineset",
        "test_lines_add_no_geometry",
        "test_blueprint_needs_no_lights",
        "test_blueprint_without_a_key_warns",
        "test_style_switches_leave_no_trace",
        "test_paper_moves_no_surface",
        "test_paper_facets_and_pencil_hatching_read_object_space",
        "test_pencil_hatching_is_fixed_to_the_object",
        "test_pencil_cycles_shadow_is_hatched_or_noted",
        "test_pencil_lines_are_sketchy_and_reapplied_once",
    ):
        assert f"PASS {name}" in result.stdout
