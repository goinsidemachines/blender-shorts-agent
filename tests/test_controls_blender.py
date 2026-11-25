from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent


@pytest.mark.blender
def test_controls_in_blender(blender_run):
    result = blender_run(HERE / "blender" / "test_controls.py", timeout=900)
    assert result.returncode == 0, result.stdout + result.stderr
    for name in (
        "test_four_channels_use_two_empties_and_signs_survive",
        "test_values_between_keys_are_linear_and_only_location_is_keyed",
        "test_saved_blend_reopened_and_rendered_by_render_range_animates_every_frame",
    ):
        assert f"PASS {name}" in result.stdout
