from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent


@pytest.mark.blender
def test_fingerprints_in_blender(blender_run):
    result = blender_run(HERE / "blender" / "test_fingerprint.py", timeout=600)
    assert result.returncode == 0, result.stdout + result.stderr
    for name in (
        "test_save_and_reload_without_a_change_keeps_every_fingerprint",
        "test_a_camera_change_in_shot_2_changes_only_shot_2",
        "test_parts_say_which_kind_of_data_changed",
        "test_a_tag_changes_the_tags_part_but_not_the_fingerprint",
        "test_a_material_only_shot_1_uses_changes_only_shot_1",
        "test_a_shared_material_changes_both_shots",
        "test_geometry_modifiers_lights_world_render_settings_and_images_count",
        "test_a_key_inside_one_shot_of_a_shared_scene_changes_only_that_shot",
        "test_animated_material_values_are_sampled_per_frame",
        "test_fingerprints_are_quick",
        "test_a_mesh_stored_in_another_order_is_the_same_mesh",
    ):
        assert f"PASS {name}" in result.stdout
