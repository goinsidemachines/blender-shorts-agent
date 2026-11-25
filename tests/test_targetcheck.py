from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent


@pytest.mark.blender
def test_motion_at_frame_1_in_blender(blender_run):
    result = blender_run(HERE / "blender" / "test_targetcheck.py", timeout=600)
    print(result.stdout)
    assert result.returncode == 0, result.stdout + result.stderr
    for name in (
        "test_a_static_scene_is_not_moving",
        "test_a_moving_cube_is_found_by_name",
        "test_a_moving_camera_is_found",
        "test_a_cube_that_starts_moving_later_is_still_at_frame_1",
        "test_a_parent_that_moves_carries_its_mesh",
        "test_a_hidden_moving_cube_is_not_something_visible",
        "test_an_animated_shape_key_counts",
    ):
        assert f"PASS {name}" in result.stdout
