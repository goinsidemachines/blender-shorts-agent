import os
import socket
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Phone access binds every interface; tests never do (no firewall prompt, nothing on the developer's network). The
# variable reaches the page servers the CLI spawns too, and the guard fails any in-process bind to a wildcard address.
os.environ["BSA_PAGE_BIND"] = "127.0.0.1"
_WILDCARD = ("0.0.0.0", "", "::")
_real_bind = socket.socket.bind


def _guarded_bind(self, address):
    if isinstance(address, tuple) and address and address[0] in _WILDCARD:
        raise AssertionError(f"a test tried to listen on every interface: {address!r}")
    return _real_bind(self, address)


socket.socket.bind = _guarded_bind

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
    browser_items = [item for item in items if "browser" in item.keywords]
    if browser_items:
        missing = chromium_missing()
        if missing and os.environ.get("BSA_REQUIRE_BROWSER"):  # CI's browser job must never pass by skipping
            raise pytest.UsageError(missing)
        if missing:
            skip_browser = pytest.mark.skip(reason=missing)
            for item in browser_items:
                item.add_marker(skip_browser)


def chromium_missing() -> str | None:
    """Why the browser tests can't run (Playwright or its Chromium is not installed), or None when they can."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return "Playwright is not installed"
    try:
        with sync_playwright() as pw:
            pw.chromium.launch().close()
    except Exception as e:  # noqa: BLE001 - any launch failure means no usable Chromium
        return f"Chromium can't start: run `uv run playwright install chromium` ({type(e).__name__})"
    return None
