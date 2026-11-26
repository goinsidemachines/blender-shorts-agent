from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent


@pytest.mark.blender
def test_callout_edit_in_blender(blender_run):
    result = blender_run(HERE / "blender" / "test_callout_edit.py", timeout=900)
    assert result.returncode == 0, result.stdout + result.stderr
    for name in (
        "test_label_and_arrow_strips_run_from_the_cue_frame",
        "test_callout_hidden_before_f_on_and_drawn_after",
        "test_arrow_dropped_where_the_part_is_off_screen",
        "test_culprit_uses_highlight_colour_and_a_thicker_arrow",
        "test_outline_style_draws_no_plate_and_an_edged_arrow",
        "test_no_callouts_the_edit_is_unchanged",
        "test_callout_edit_encodes",
    ):
        assert f"PASS {name}" in result.stdout
