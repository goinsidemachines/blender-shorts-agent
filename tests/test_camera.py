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
        "test_framing_warns_when_the_camera_ends_up_under_the_ground_if_asked_not_to_lift",
        "test_framing_lifts_the_camera_out_of_the_ground",
        "test_framing_refuses_a_subject_under_the_ground",
        "test_frame_objects_puts_the_subject_between_explicit_rows",
        "test_framing_warnings_are_kept_on_the_scene_as_findings",
        "test_frame_objects_fits_an_explicit_box_with_the_lens_shift",
        "test_frame_objects_measures_parented_objects_in_a_scene_that_is_not_the_windows",
        "test_move_orbits_with_the_subject_in_the_band_the_whole_way",
        "test_move_pushes_in_to_a_part",
    ):
        assert f"PASS {name}" in result.stdout
