from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent


@pytest.mark.blender
def test_scene_render_in_blender(blender_run):
    result = blender_run(HERE / "blender" / "test_scene_render.py", timeout=600)
    assert result.returncode == 0, result.stdout + result.stderr
    for name in (
        "test_setup_scene_9x16",
        "test_a_shorts_own_scenes_replace_the_factory_scene",
        "test_render_still_jpeg",
        "test_resume_rerenders_empty_placeholders",
        "test_keyframe_series",
        "test_tags_merge",
        "test_estimate_restores_scene_state",
        "test_render_range_raises_on_failed_render",
        "test_explicit_engine_overrides_scene",
        "test_estimate_positive",
        "test_eevee_presets_turn_ray_tracing_on",
        "test_presets_and_look_helpers",
        "test_eevee_estimate_times_a_warm_frame",
        "test_cycles_estimate_times_one_frame",
    ):
        assert f"PASS {name}" in result.stdout
