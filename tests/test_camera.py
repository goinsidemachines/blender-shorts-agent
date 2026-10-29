from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent


@pytest.mark.blender
def test_camera_and_motion_in_blender(blender_run):
    result = blender_run(HERE / "blender" / "test_camera_motion.py", timeout=600)
    assert result.returncode == 0, result.stdout + result.stderr
    for name in (
        "test_frame_objects_fills_the_safe_area_above_the_captions",
        "test_frame_objects_over_the_frames_of_a_motion_and_aim",
        "test_frame_objects_with_an_orthographic_camera_and_no_band",
        "test_moving_frames_follow_keyed_heroes_and_the_camera",
        "test_frame_objects_on_an_animated_camera_keeps_its_pose_and_keys",
        "test_frame_objects_refuses_a_box_with_no_size",
        "test_frame_objects_warns_when_the_answer_is_absurd",
        "test_frame_box_frames_a_region_of_one_long_mesh",
        "test_frame_objects_measures_parented_objects_in_a_scene_that_is_not_the_windows",
    ):
        assert f"PASS {name}" in result.stdout
