from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent


@pytest.mark.blender
def test_relink_in_blender(blender_run):
    result = blender_run(HERE / "blender" / "test_relink.py", timeout=600)
    assert result.returncode == 0, result.stdout + result.stderr
    for name in ("test_relink_repoints_an_image_under_the_old_root",
                 "test_relink_saves_nothing_when_nothing_points_at_the_old_root",
                 "test_relink_lists_a_repointed_file_that_is_not_there", "test_relink_needs_the_short_blend",
                 "test_relink_repoints_strips_texts_outputs_and_caches"):  # fmt: skip
        assert f"PASS {name}" in result.stdout
