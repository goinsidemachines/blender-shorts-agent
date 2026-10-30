from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent


@pytest.mark.blender
def test_rigid_body_sim_in_blender(blender_run):
    result = blender_run(HERE / "blender" / "test_rigid.py", timeout=600)
    assert result.returncode == 0, result.stdout + result.stderr
    for name in (
        "test_hinge_pendulum_period_at_the_shorts_fps",
        "test_a_second_run_in_the_same_session_repeats_and_never_reuses_a_stale_cache",
        "test_keyframe_matrices_bakes_the_motion_onto_a_render_object",
    ):
        assert f"PASS {name}" in result.stdout
