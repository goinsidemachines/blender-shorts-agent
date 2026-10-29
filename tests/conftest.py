import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Modules under tests/blender import bpy and only run inside Blender (via runner.py).
collect_ignore_glob = ["blender/test_*.py"]


def find_blender():
    """Blender executable via bsa.locate, or None when it is not installed."""
    from bsa.locate import BlenderNotFound
    from bsa.locate import find_blender as _find

    try:
        return _find()
    except BlenderNotFound:
        return None


def run_in_blender(test_module, timeout=300):
    """Run a test module inside Blender via tests/blender/runner.py. Returns the CompletedProcess."""
    blender = find_blender()
    if blender is None:
        pytest.skip("Blender not found")
    runner = ROOT / "tests" / "blender" / "runner.py"
    cmd = [
        str(blender), "-b", "--factory-startup", "--python-exit-code", "1",
        "--python", str(runner), "--", str(test_module),
    ]  # fmt: skip
    return subprocess.run(cmd, check=False, capture_output=True, text=True, timeout=timeout, cwd=str(ROOT))


@pytest.fixture
def blender_run():
    return run_in_blender


@pytest.fixture
def bsa_home(tmp_path, monkeypatch):
    home = tmp_path / "bsa_home"
    home.mkdir()
    monkeypatch.setenv("BSA_HOME", str(home))
    return home


def pytest_collection_modifyitems(config, items):
    if "slow" not in (config.getoption("-m") or ""):
        skip_slow = pytest.mark.skip(reason="slow test: run with -m slow")
        for item in items:
            if "slow" in item.keywords:
                item.add_marker(skip_slow)
    if find_blender() is None:
        skip_blender = pytest.mark.skip(reason="Blender not found")
        for item in items:
            if "blender" in item.keywords:
                item.add_marker(skip_blender)
