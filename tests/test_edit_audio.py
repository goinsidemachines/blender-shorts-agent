from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent


@pytest.mark.blender
def test_edit_audio_in_blender(blender_run):
    result = blender_run(HERE / "blender" / "test_edit_audio.py", timeout=600)
    assert result.returncode == 0, result.stdout + result.stderr
    for name in ("test_the_mix_strip_starts_on_frame_one_with_no_offset", "test_the_encode_keeps_the_first_50_ms"):
        assert f"PASS {name}" in result.stdout
