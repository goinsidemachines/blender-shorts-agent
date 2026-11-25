from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent


@pytest.mark.blender
def test_loop_check_in_blender(blender_run):
    result = blender_run(HERE / "blender" / "test_loopcheck.py", timeout=600)
    assert result.returncode == 0, result.stdout + result.stderr
    for name in (
        "test_a_cube_turning_exactly_once_loops",
        "test_a_cube_sliding_without_returning_is_found",
        "test_a_camera_move_that_does_not_return_is_found",
    ):
        assert f"PASS {name}" in result.stdout
