from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent


@pytest.mark.blender
def test_look_in_blender(blender_run):
    result = blender_run(HERE / "blender" / "test_look.py", timeout=600)
    assert result.returncode == 0, result.stdout + result.stderr
    for name in (
        "test_a_plain_world_renders_grey_under_agx",
        "test_toy_water_is_tinted_and_see_through_in_eevee",
        "test_paper_world_renders_the_brand_colour",
        "test_paper_world_with_a_look_and_exposure",
        "test_brand_view_and_bad_look",
        "test_paper_world_reaches_a_dark_colour_under_punchy",
        "test_paper_world_says_when_the_view_cant_reach_the_colour",
        "test_a_fresh_short_keeps_the_view_even_when_build_py_omits_the_short",
        "test_kit_material_is_stamped_and_renders_its_colour",
        "test_kit_lights_stamp_both_engines_and_replace_the_old_rig",
        "test_ink_lines_add_no_geometry_and_exclude_set_surfaces",
        "test_match_engine_reads_the_engine_and_never_writes_it",
        "test_render_still_matches_the_engine_it_renders_with",
        "test_every_render_entry_point_matches_the_engine",
        "test_paper_world_stamps_its_colour",
        "test_ink_lines_off_puts_the_users_freestyle_back_exactly",
        "test_a_stamp_error_never_hides_the_renders_own_error",
    ):
        assert f"PASS {name}" in result.stdout
