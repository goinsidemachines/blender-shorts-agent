import os
import subprocess
from pathlib import Path

import pytest

from bsa import locate
from bsa.locate import MIN_BLENDER, BlenderNotFound, blender_version, check_blender, find_blender


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv("BLENDER_EXE", raising=False)
    # No PATH hit, no real install folders unless a test opts in.
    monkeypatch.setattr(locate.shutil, "which", lambda name: None)
    monkeypatch.setattr(locate, "PROGRAM_FILES", "Z:/nonexistent")
    monkeypatch.setattr(locate, "_OTHER_CANDIDATES", [])


def _fake_exe(directory: Path, name="blender.exe") -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    exe = directory / name
    exe.write_text("fake")
    return exe


def test_settings_path_wins(tmp_path, monkeypatch):
    exe = _fake_exe(tmp_path / "mine")
    monkeypatch.setenv("BLENDER_EXE", str(_fake_exe(tmp_path / "env")))
    assert find_blender({"blender_exe": str(exe)}) == exe


def test_env_var_used(tmp_path, monkeypatch):
    exe = _fake_exe(tmp_path / "env")
    monkeypatch.setenv("BLENDER_EXE", str(exe))
    assert find_blender({}) == exe
    assert find_blender() == exe


def test_path_lookup(tmp_path, monkeypatch):
    exe = _fake_exe(tmp_path / "onpath")
    monkeypatch.setattr(locate.shutil, "which", lambda name: str(exe))
    assert find_blender() == exe


def test_newest_windows_install_picked(tmp_path, monkeypatch):
    _fake_exe(tmp_path / "Blender Foundation" / "Blender 4.5")
    newest = _fake_exe(tmp_path / "Blender Foundation" / "Blender 5.2")
    _fake_exe(tmp_path / "Blender Foundation" / "Blender 5.10").unlink()  # folder without exe is ignored
    monkeypatch.setattr(locate, "PROGRAM_FILES", str(tmp_path))
    assert find_blender() == newest


def test_numeric_not_lexical_version_order(tmp_path, monkeypatch):
    _fake_exe(tmp_path / "Blender Foundation" / "Blender 5.2")
    newest = _fake_exe(tmp_path / "Blender Foundation" / "Blender 5.10")
    monkeypatch.setattr(locate, "PROGRAM_FILES", str(tmp_path))
    assert find_blender() == newest


def test_not_found_message_says_how_to_set(tmp_path):
    with pytest.raises(BlenderNotFound) as err:
        find_blender({"blender_exe": str(tmp_path / "missing.exe")})
    assert "BLENDER_EXE" in str(err.value)
    assert "blender_exe" in str(err.value)


def test_version_parse():
    assert blender_version_from("Blender 5.2.0\n\tbuild date: x\n") == (5, 2)
    assert blender_version_from("Blender 4.5.3 LTS\n") == (4, 5)


def blender_version_from(text):
    return locate.parse_version(text)


def test_version_parse_garbage():
    with pytest.raises(ValueError):
        locate.parse_version("not blender")


def test_blender_version_runs_exe_with_arg_list(monkeypatch, tmp_path):
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="Blender 5.2.0\n", stderr="")

    monkeypatch.setattr(locate.subprocess, "run", fake_run)
    exe = tmp_path / "blender.exe"
    assert blender_version(exe) == (5, 2)
    assert calls == [[str(exe), "--version"]]


def test_too_old_reported(monkeypatch, tmp_path):
    monkeypatch.setattr(locate, "blender_version", lambda exe: (4, 2))
    monkeypatch.setattr(locate, "blender_python", lambda exe: tmp_path / "python.exe")
    problems = check_blender(tmp_path / "blender.exe")
    assert len(problems) == 1
    assert "4.5" in problems[0]
    assert MIN_BLENDER == (4, 5)


def test_missing_python_reported(monkeypatch, tmp_path):
    monkeypatch.setattr(locate, "blender_version", lambda exe: (5, 2))

    def boom(exe):
        raise FileNotFoundError("no python")

    monkeypatch.setattr(locate, "blender_python", boom)
    problems = check_blender(tmp_path / "blender.exe")
    assert len(problems) == 1
    assert "Python" in problems[0]


def test_blender_python_found_in_bundle(tmp_path):
    exe = _fake_exe(tmp_path)
    py_name = "python.exe" if os.name == "nt" else "python3.11"
    py_dir = tmp_path / "5.2" / "python" / "bin"
    py_dir.mkdir(parents=True)
    (py_dir / py_name).write_text("fake")
    assert locate_python(exe, (5, 2)) == py_dir / py_name


def locate_python(exe, version):
    import bsa.locate as m

    orig = m.blender_version
    m.blender_version = lambda e: version
    try:
        return m.blender_python(exe)
    finally:
        m.blender_version = orig


def test_cmd_shim_on_path_skipped_for_real_exe(tmp_path, monkeypatch):
    shim = _fake_exe(tmp_path / "bin", "blender.CMD")
    real = _fake_exe(tmp_path / "pf" / "Blender Foundation" / "Blender 5.2")
    monkeypatch.setattr(locate.shutil, "which", lambda name: str(shim))
    monkeypatch.setattr(locate, "PROGRAM_FILES", str(tmp_path / "pf"))
    assert find_blender() == real


def test_shim_used_as_last_resort(tmp_path, monkeypatch):
    shim = _fake_exe(tmp_path / "bin", "blender.bat")
    monkeypatch.setattr(locate.shutil, "which", lambda name: str(shim))
    assert find_blender() == shim


def test_env_var_shim_still_wins(tmp_path, monkeypatch):
    shim = _fake_exe(tmp_path / "bin", "blender.cmd")
    monkeypatch.setenv("BLENDER_EXE", str(shim))
    _fake_exe(tmp_path / "pf" / "Blender Foundation" / "Blender 5.2")
    monkeypatch.setattr(locate, "PROGRAM_FILES", str(tmp_path / "pf"))
    assert find_blender() == shim
