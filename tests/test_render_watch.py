from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent


@pytest.mark.blender
def test_render_watch_in_blender(blender_run):
    result = blender_run(HERE / "blender" / "test_render_watch.py", timeout=600)
    assert result.returncode == 0, result.stdout + result.stderr
    for name in (
        "test_status_follows_a_resumed_render",
        "test_settings_thumbnails_and_page",
        "test_a_black_frame_is_caught_live",
        "test_overhead_is_small_on_a_full_size_frame",
        "test_flicker_is_caught_by_the_live_checks",
    ):
        assert f"PASS {name}" in result.stdout
