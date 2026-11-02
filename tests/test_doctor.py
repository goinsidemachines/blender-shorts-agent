"""CLI doctor: a health check of Blender, the GPU lock, the voice, the disk, the caption font and the page server."""

import json
import os
import shutil
import time
import types
from pathlib import Path

from bsa import doctor, fonts, gpulock, locate
from bsa.narrate import install
from tests import test_cli
from tests.test_cli import ok, run

home, workspace, pendulum = test_cli.home, test_cli.workspace, test_cli.pendulum  # the CLI tests' fixtures


def _by_id(checks):
    return {c["id"]: c for c in checks}


def test_all_ok(monkeypatch, tmp_path):
    monkeypatch.setattr(locate, "find_blender", lambda s=None: Path("/b/blender"))
    monkeypatch.setattr(locate, "blender_version", lambda exe: (4, 5))
    monkeypatch.setattr(shutil, "disk_usage", lambda p: (0, 0, 50 * 10**9))
    checks = doctor.run({"narrator": {"provider": "none"}}, tmp_path)
    assert [c["id"] for c in checks] == ["blender", "gpu", "voice", "disk", "font", "server"]
    levels = {c["id"]: c["level"] for c in checks}
    assert levels["blender"] == "ok"
    assert "4.5" in checks[0]["text"]
    assert levels["gpu"] == levels["voice"] == levels["disk"] == levels["font"] == "ok"
    assert levels["server"] == "warn"
    assert "not started yet" in checks[-1]["text"]


def test_server_details_make_the_server_ok(monkeypatch, tmp_path):
    server = next(c for c in doctor.run({}, tmp_path, server={"port": 4321}) if c["id"] == "server")
    assert server["level"] == "ok" and "4321" in server["text"]


def test_low_disk_warns_under_10_gb(monkeypatch, tmp_path):
    monkeypatch.setattr(shutil, "disk_usage", lambda p: (0, 0, 9 * 10**9))
    disk = next(c for c in doctor.run({}, tmp_path) if c["id"] == "disk")
    assert disk["level"] == "warn" and "9.0 GB" in disk["text"] and disk["fix"]


def test_missing_blender_is_an_error_with_a_fix(monkeypatch, tmp_path):
    def nope(s=None):
        raise locate.BlenderNotFound("no Blender")

    monkeypatch.setattr(locate, "find_blender", nope)
    b = doctor.run({}, tmp_path)[0]
    assert b["level"] == "error" and "blender_exe" in b["fix"]


def test_old_blender_is_an_error(monkeypatch, tmp_path):
    monkeypatch.setattr(locate, "find_blender", lambda s=None: Path("/b/blender"))
    monkeypatch.setattr(locate, "blender_version", lambda exe: (4, 2))
    b = doctor.run({}, tmp_path)[0]
    assert b["level"] == "error" and "4.2" in b["text"] and b["fix"]


def test_gpu_lock_holder_is_named(tmp_path):
    gpulock.lock_path(tmp_path).write_text(json.dumps({"pid": os.getpid(), "short": "03-x", "command": "render",
                                                       "at": time.time()}))  # fmt: skip
    gpu = next(c for c in doctor.run({}, tmp_path) if c["id"] == "gpu")
    assert gpu["level"] == "warn" and "03-x" in gpu["text"]


def test_dead_gpu_lock_is_not_busy(tmp_path):
    gpulock.lock_path(tmp_path).write_text(json.dumps({"pid": 2**22 + 12345, "short": "03-x", "command": "render"}))
    gpu = next(c for c in doctor.run({}, tmp_path) if c["id"] == "gpu")
    assert gpu["level"] == "ok"


def test_voice_levels(monkeypatch, tmp_path):
    monkeypatch.setattr(install, "installed", lambda: {"kokoro": True, "chatterbox": False})
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)

    def voice(narrator):
        return next(c for c in doctor.run({"narrator": narrator}, tmp_path) if c["id"] == "voice")

    assert voice({"provider": "kokoro"})["level"] == "ok"
    assert voice({"provider": "chatterbox"})["level"] == "warn"
    assert voice({"provider": "custom"})["level"] == "warn"
    assert voice({"provider": "custom", "command": "tts {text}"})["level"] == "ok"
    assert voice({"provider": "elevenlabs"})["level"] == "warn"
    monkeypatch.setenv("ELEVENLABS_API_KEY", "x")
    assert voice({"provider": "elevenlabs"})["level"] == "ok"
    unset = voice({"provider": None})
    assert unset["level"] == "warn"
    assert "00-first-run" in unset["fix"]


def test_variable_caption_font_warns(monkeypatch, tmp_path):
    monkeypatch.setattr(fonts, "resolve", lambda name, *a, **k: (Path("f.ttf"), "Inter", None))
    monkeypatch.setattr(fonts, "font_info_or_none", lambda path, index=0: {
        "variable": True, "default_weight": 400, "default_instance": "Regular", "family": "Inter"})
    font = next(c for c in doctor.run({"brand": {"font": "Inter"}}, tmp_path) if c["id"] == "font")
    assert font["level"] == "warn"
    assert "variable font" in font["text"]
    assert font["fix"]


def test_font_not_installed_warns(monkeypatch, tmp_path):
    monkeypatch.setattr(fonts, "resolve", lambda name, *a, **k: (None, None, "font 'Zzz' is not installed"))
    font = next(c for c in doctor.run({"brand": {"font": "Zzz"}}, tmp_path) if c["id"] == "font")
    assert font["level"] == "warn" and "not installed" in font["text"]


def test_short_style_font_wins_over_the_brand_font(monkeypatch, tmp_path):
    seen = []
    monkeypatch.setattr(fonts, "resolve", lambda name, *a, **k: seen.append(name) or (None, None, None))

    short = types.SimpleNamespace(style={"font": "Mine"})
    doctor.run({"brand": {"font": "Brand"}}, tmp_path, short=short)
    assert seen == ["Mine"]


def test_save_writes_review_doctor_json(tmp_path):
    path = doctor.save(tmp_path, [{"id": "disk", "level": "ok", "text": "t", "fix": None}])
    data = json.loads(path.read_text(encoding="utf-8"))
    assert path == tmp_path / "review" / "doctor.json" and data["checks"][0]["id"] == "disk" and data["at"]


def test_cli_doctor_saves_for_the_short(home, pendulum):
    out = ok(run(home, "doctor", "pendulum"))
    assert out["checks"] and isinstance(out["ok"], bool) and (pendulum / "review" / "doctor.json").is_file()


def test_cli_doctor_without_a_short(home):
    out = ok(run(home, "doctor"))
    assert out["checks"][0]["id"] == "blender"


def test_weighted_font_in_the_short_workspace_cache_is_ok(tmp_path):
    cache = tmp_path / "ws" / ".assets-cache"
    (cache / "fonts").mkdir(parents=True)
    (cache / "fonts" / fonts.cached_font_name("Zzfamily", 600)).write_bytes(b"not a real font")
    settings = {"brand": {"font": "Zzfamily@600"}}
    font = next(c for c in doctor.run(settings, tmp_path, cache_dirs=[cache]) if c["id"] == "font")
    assert font["level"] == "ok"
    bare = next(c for c in doctor.run(settings, tmp_path, cache_dirs=[tmp_path / "none"]) if c["id"] == "font")
    assert bare["level"] == "warn"


def test_variable_font_advice_names_a_cached_weight(monkeypatch, tmp_path):
    monkeypatch.setattr(fonts, "resolve", lambda name, *a, **k: (Path("f.ttf"), "Inter", None))
    monkeypatch.setattr(fonts, "font_info_or_none", lambda path, index=0: {
        "variable": True, "default_weight": 400, "default_instance": "Regular", "family": "Inter"})
    monkeypatch.setattr(fonts, "cached_weights", lambda family, cache_dirs=None: [600])
    font = next(c for c in doctor.run({"brand": {"font": "Inter"}}, tmp_path) if c["id"] == "font")
    assert "Inter@600" in font["fix"]


def test_gpu_without_a_workspace_says_so():
    gpu = next(c for c in doctor.run({}, None) if c["id"] == "gpu")
    assert gpu["level"] == "ok"
    assert "No workspace" in gpu["text"]
