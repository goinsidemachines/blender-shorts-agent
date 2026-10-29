from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent


@pytest.mark.blender
def test_runner_executes_smoke(blender_run):
    result = blender_run(HERE / "blender" / "test_smoke.py")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "PASS test_blender_version" in result.stdout


@pytest.mark.blender
def test_runner_fails_on_failing_test(blender_run):
    result = blender_run(HERE / "blender" / "fixtures" / "failing_module.py")
    assert result.returncode != 0, result.stdout + result.stderr
    assert "FAIL test_broken" in result.stdout


@pytest.mark.blender
def test_runner_fails_when_module_has_no_tests(blender_run):
    result = blender_run(HERE / "blender" / "fixtures" / "empty_module.py")
    assert result.returncode != 0, result.stdout + result.stderr
    assert "no test_* functions" in result.stdout
