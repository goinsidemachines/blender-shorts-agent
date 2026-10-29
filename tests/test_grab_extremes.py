from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent


@pytest.mark.blender
def test_grab_in_blender(blender_run):
    result = blender_run(HERE / "blender" / "test_grab.py", timeout=600)
    assert result.returncode == 0, result.stdout + result.stderr
    for name in ("test_frames_for_seconds", "test_grab_keeps_the_videos_colours_at_full_size",
                 "test_contact_sheet_tiles_the_frames"):  # fmt: skip
        assert f"PASS {name}" in result.stdout


@pytest.mark.blender
def test_extremes_in_blender(blender_run):
    result = blender_run(HERE / "blender" / "test_extremes.py", timeout=600)
    assert result.returncode == 0, result.stdout + result.stderr
    for name in ("test_extreme_frames_of_heroes_and_their_moving_parts",
                 "test_render_extremes_writes_one_still_per_frame",
                 "test_extreme_frames_follow_a_hero_moved_by_an_untagged_parent_or_constraint"):  # fmt: skip
        assert f"PASS {name}" in result.stdout
