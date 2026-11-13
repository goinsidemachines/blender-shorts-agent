from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent


@pytest.mark.blender
def test_chunks_in_blender(blender_run):
    result = blender_run(HERE / "blender" / "test_chunks.py", timeout=600)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "PASS test_two_chunks_render_one_folder_once_each" in result.stdout
