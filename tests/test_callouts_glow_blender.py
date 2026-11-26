from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent


@pytest.mark.blender
def test_callouts_glow_in_blender(blender_run):
    result = blender_run(HERE / "blender" / "test_callouts_glow.py", timeout=900)
    assert result.returncode == 0, result.stdout + result.stderr
    for name in (
        "test_glow_keys_only_the_callout_object",
        "test_glow_ramps_at_cue_frames",
        "test_glow_clipped_at_the_shot_end",
        "test_existing_emission_untouched",
        "test_glow_is_idempotent",
        "test_glow_survives_save_and_reopen",
        "test_culprit_pulses",
        "test_part_without_a_shader_gets_a_note",
        "test_changed_cue_frames_change_the_shot_fingerprint",
    ):
        assert f"PASS {name}" in result.stdout
