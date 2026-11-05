"""bsa.proc: background programs start without a console window on Windows, and nothing else changes."""

import re
import subprocess
import sys
import types
from pathlib import Path

from bsa import proc

ROOT = Path(__file__).resolve().parent.parent
FLAG = 0x08000000  # CREATE_NO_WINDOW


def _fakes(monkeypatch):
    seen = []

    def fake_run(*a, **k):
        seen.append(("run", a, k))
        return subprocess.CompletedProcess(a[0], 0, "", "")

    def fake_popen(*a, **k):
        seen.append(("popen", a, k))
        return object()

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    return seen


def test_windows_adds_the_no_window_flag(monkeypatch):
    seen = _fakes(monkeypatch)
    monkeypatch.setattr(proc, "NO_WINDOW", {"creationflags": FLAG})
    proc.run(["x"], capture_output=True, timeout=5)
    proc.popen(["y"], stdout=subprocess.PIPE)
    assert seen[0] == ("run", (["x"],), {"capture_output": True, "timeout": 5, "creationflags": FLAG})
    assert seen[1] == ("popen", (["y"],), {"stdout": subprocess.PIPE, "creationflags": FLAG})


def test_given_flags_are_kept_and_ored(monkeypatch):
    seen = _fakes(monkeypatch)
    monkeypatch.setattr(proc, "NO_WINDOW", {"creationflags": FLAG})
    detached, group = 0x8, 0x200
    proc.popen(["y"], creationflags=detached | group)
    assert seen[0][2]["creationflags"] == detached | group | FLAG
    proc.run(["y"], creationflags=FLAG)  # already there: unchanged
    assert seen[1][2]["creationflags"] == FLAG


def test_elsewhere_nothing_extra_is_passed(monkeypatch):
    seen = _fakes(monkeypatch)
    monkeypatch.setattr(proc, "NO_WINDOW", {})
    proc.run(["x"], check=False)
    proc.popen(["y"], creationflags=0x8)
    assert seen[0] == ("run", (["x"],), {"check": False})
    assert seen[1] == ("popen", (["y"],), {"creationflags": 0x8})


def test_the_flag_matches_the_platform():
    if sys.platform == "win32":
        assert proc.NO_WINDOW == {"creationflags": subprocess.CREATE_NO_WINDOW}
    else:
        assert proc.NO_WINDOW == {}


def test_real_run_still_captures_output():
    out = proc.run([sys.executable, "-c", "print('hi')"], capture_output=True, text=True, check=False)
    assert out.returncode == 0 and out.stdout.strip() == "hi"


def _fake_bpy(monkeypatch):
    bpy = types.SimpleNamespace(app=types.SimpleNamespace(binary_path="blender"))
    monkeypatch.setitem(sys.modules, "bpy", bpy)


def test_the_side_check_starts_without_a_window_and_keeps_its_pipes(monkeypatch, tmp_path):
    import bsa_blender

    seen = _fakes(monkeypatch)
    monkeypatch.setattr(proc, "NO_WINDOW", {"creationflags": FLAG})
    _fake_bpy(monkeypatch)
    monkeypatch.setattr(bsa_blender, "_SideCheck", lambda p, chain: p)
    bsa_blender._start_side_check(types.SimpleNamespace(every=1.0), tmp_path, None)
    kind, _, kw = seen[0]
    assert kind == "popen" and kw["creationflags"] == FLAG
    assert kw["stdout"] == subprocess.PIPE and kw["stderr"] == subprocess.PIPE


def test_the_script_mode_build_starts_without_a_window(monkeypatch, tmp_path):
    import bsa_blender

    seen = _fakes(monkeypatch)
    monkeypatch.setattr(proc, "NO_WINDOW", {"creationflags": FLAG})
    _fake_bpy(monkeypatch)
    (tmp_path / "source").mkdir()
    (tmp_path / "source" / "build.py").write_text("", encoding="utf-8")
    monkeypatch.setattr(bsa_blender, "_blend", lambda sdir: tmp_path / "none.blend")
    try:
        bsa_blender._build_script_mode(tmp_path)
    except bsa_blender.CliError:
        pass  # the fake build produced no blend: only the launch matters here
    assert seen[0][0] == "run" and seen[0][2]["creationflags"] == FLAG and seen[0][2]["capture_output"] is True


_DIRECT = re.compile(
    r"\bsubprocess\.(run|Popen|check_output|check_call|call|getoutput|getstatusoutput)\s*\(|\bos\.(system|popen|startfile)\b"
    r"|\bfrom\s+subprocess\s+import\b|\bos\.spawn|\bsubprocess\.(run|Popen)\b(?!\s*\()"
)


def test_no_production_file_starts_a_program_without_the_helper():
    allowed = {ROOT / "bsa" / "proc.py", ROOT / "voices" / "runner" / "bsa_tts" / "proc.py"}
    files = [*(ROOT / "bsa").rglob("*.py"), ROOT / "bsa_blender.py", ROOT / "bsa_cli.py",
             *(ROOT / "voices" / "runner" / "bsa_tts").rglob("*.py")]  # fmt: skip
    bad = []
    for f in files:
        if f in allowed:
            continue
        for n, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            if _DIRECT.search(line.split("#")[0]):
                bad.append(f"{f.relative_to(ROOT)}:{n}: {line.strip()}")
    assert not bad, "start programs through bsa.proc (run/popen):\n" + "\n".join(bad)
