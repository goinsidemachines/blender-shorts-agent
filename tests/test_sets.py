from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent


@pytest.mark.blender
def test_sets_in_blender(blender_run, bsa_home):
    result = blender_run(HERE / "blender" / "test_sets.py", timeout=1800)
    assert result.returncode == 0, result.stdout + result.stderr
    for name in (
        "test_without_a_kit_sets_change_nothing",
        "test_each_set_puts_its_floor_top_at_zero_and_tags_it",
        "test_floor_is_sized_to_the_camera_view_never_larger",
        "test_props_stay_clear_of_the_moving_subject_and_cards",
        "test_dress_is_deterministic_per_slug",
        "test_dress_twice_is_the_same_as_once",
        "test_close_up_scene_drops_props_only_there",
        "test_approved_arrangement_is_reused",
        "test_studio_backdrop_shows_the_kit_background_under_the_style_view",
        "test_set_worlds_are_environments_but_the_studio",
        "test_sets_and_styles_never_change_the_engine",
        "test_clear_takes_the_set_away",
        "test_storyboard_version_can_be_worked_out_in_blender",
        "test_toon_set_floors_match_between_engines",
        "test_a_sample_framing_gets_a_dressed_set",
        "test_set_none_takes_a_set_away",
        "test_outdoor_ground_keeps_its_colour_in_the_lit_styles",
        "test_taking_a_set_away_leaves_no_world_behind",
        "test_clear_puts_film_transparent_back",
    ):
        assert f"PASS {name}" in result.stdout
