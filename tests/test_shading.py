from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent


@pytest.mark.blender
def test_shading_in_blender(blender_run):
    result = blender_run(HERE / "blender" / "test_shading.py", timeout=600)
    assert result.returncode == 0, result.stdout + result.stderr
    for name in (
        "test_lights_use_suns_for_big_subjects_and_far_area_lights_for_small_ones",
        "test_flat_faces_are_the_heros_seen_planes",
        "test_close_area_lights_shade_a_tall_wall_and_sun_lamps_do_not",
    ):
        assert f"PASS {name}" in result.stdout
