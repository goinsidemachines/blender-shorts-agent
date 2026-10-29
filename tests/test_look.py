from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent


@pytest.mark.blender
def test_look_in_blender(blender_run):
    result = blender_run(HERE / "blender" / "test_look.py", timeout=600)
    assert result.returncode == 0, result.stdout + result.stderr
    for name in (
        "test_a_plain_world_renders_grey_under_agx",
        "test_paper_world_renders_the_brand_colour",
        "test_paper_world_with_a_look_and_exposure",
        "test_brand_view_and_bad_look",
        "test_paper_world_reaches_a_dark_colour_under_punchy",
        "test_paper_world_says_when_the_view_cant_reach_the_colour",
        "test_a_fresh_short_keeps_the_view_even_when_build_py_omits_the_short",
    ):
        assert f"PASS {name}" in result.stdout
