"""CLI: sound and font assets through the plan-and-yes flow, the mix's report of recorded vs synthesised effects,
sound_policy and font warnings in validate, bsa.audio.clip from source/sound.py."""

import io
import json
import os
import shutil
import subprocess
import sys
import wave
import zipfile
from pathlib import Path

import numpy as np
import pytest
from fontbuild import build_font

import bsa_cli
from bsa.assets import sounds

ROOT = Path(__file__).resolve().parent.parent
SAMPLE = ROOT / "tests" / "e2e" / "pendulum" / "source" / "short.json"


def run(home, *args):
    env = {**os.environ, "BSA_HOME": str(home)}
    env.pop("FREESOUND_API_KEY", None)
    return subprocess.run([sys.executable, str(ROOT / "bsa_cli.py"), *map(str, args)], capture_output=True, text=True,
                          env=env, cwd=str(home), timeout=120, check=False)  # fmt: skip


def ok(proc):
    assert proc.returncode == 0, proc.stdout + proc.stderr
    return json.loads(proc.stdout)


def _wav_bytes(f=250.0, sec=0.3):
    buf = io.BytesIO()
    t = np.arange(int(sec * 48000)) / 48000
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(48000)
        w.writeframes((0.5 * np.sin(2 * np.pi * f * t) * np.exp(-t / 0.1) * 32767).astype("<i2").tobytes())
    return buf.getvalue()


@pytest.fixture
def home(tmp_path):
    h = tmp_path / "home"
    h.mkdir()
    return h


@pytest.fixture
def short(home, tmp_path):
    ok(run(home, "settings", "set", "workspace", tmp_path / "ws"))
    sdir = Path(ok(run(home, "new-short", "Lever"))["short"])
    shutil.copyfile(SAMPLE, sdir / "source" / "short.json")
    return sdir


def _edit_short(sdir, **changes):
    path = sdir / "source" / "short.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data.update(changes)
    path.write_text(json.dumps(data), encoding="utf-8")


def _seed_library(sdir):
    """What `assets download` leaves behind for a pack: clips in the cache and the library index."""
    cache = sdir.parent / ".assets-cache"
    clips = []
    for name, kinds in (("thud_1.wav", ["thud"]), ("click_1.wav", ["click"])):
        p = cache / "sounds" / "pack" / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(_wav_bytes())
        clips.append({"path": f"sounds/pack/{name}", "kinds": kinds, "asset": "sfx-pack", "name": p.stem,
                      "source": "OpenGameArt (someone)", "license": "CC0"})  # fmt: skip
    (cache / "sounds" / "library.json").write_text(json.dumps({"clips": clips}), encoding="utf-8")


def test_mix_reports_recordings_and_synthesis(home, short):
    _seed_library(short)
    _edit_short(short, sfx=[{"kind": "thud", "shot": "s01", "at_s": 0.5, "dur_s": 0.3, "level_db": -18},
                            {"kind": "creak", "shot": "s02", "at_s": 0.2, "dur_s": 0.6, "level_db": -20}])  # fmt: skip
    ok(run(home, "timeline", short))
    data = ok(run(home, "mix", short))
    sfx = data["sfx"]
    assert sfx["recorded"] == 1 and sfx["synthesised"] == 1 and sfx["skipped"] == 0
    assert sfx["clips"] == [{"clip": "sounds/pack/thud_1.wav", "source": "OpenGameArt (someone)", "license": "CC0"}]
    assert any("creak" in n and "synthesis" in n for n in sfx["notes"])
    assert data["sound_policy"] == "any"


def test_natural_only_in_validate_and_mix(home, short):
    _seed_library(short)
    _edit_short(short, sound_policy="natural-only",
                sfx=[{"kind": "blip", "shot": "s01", "at_s": 0.5, "dur_s": 0.1, "level_db": -18},
                     {"kind": "click", "shot": "s01", "at_s": 1.0, "dur_s": 0.1, "level_db": -18}])  # fmt: skip
    v = ok(run(home, "validate", short))
    assert v["ok"] is True and any("blip" in w and "natural-only" in w for w in v["warnings"])
    ok(run(home, "timeline", short))
    sfx = ok(run(home, "mix", short))["sfx"]
    assert (sfx["recorded"], sfx["skipped"]) == (1, 1)
    # the setting applies when the short does not say
    _edit_short(short, sound_policy="")
    ok(run(home, "settings", "set", "sound_policy", "natural-only"))
    assert ok(run(home, "mix", short))["sound_policy"] == "natural-only"


def test_sound_py_can_load_a_clip(home, short):
    _seed_library(short)
    (short / "source" / "sound.py").write_text(
        "import numpy as np\nfrom bsa.audio import clip\n"
        "def build_bed(short, timeline, short_dir):\n"
        "    n = round(timeline.n_frames / timeline.fps * 48000)\n"
        "    bed = np.zeros(n)\n"
        "    x = clip('thud', short_dir=short_dir)\n"
        "    bed[48000:48000 + len(x)] += x\n"
        "    x = clip('click_1')\n"  # found through the workspace setting
        "    bed[96000:96000 + len(x)] += x\n"
        "    return bed\n", encoding="utf-8")  # fmt: skip
    ok(run(home, "timeline", short))
    assert ok(run(home, "mix", short))["sound_py"] is True


def test_validate_warns_about_missing_glyphs(home, short, tmp_path):
    font = tmp_path / "Brand.ttf"
    data = json.loads((short / "source" / "short.json").read_text(encoding="utf-8"))
    text = "".join(ln["text"] for ln in data["lines"])
    font.write_bytes(build_font(text.replace("s", "")))  # every character of the lines except "s"
    _edit_short(short, style={"font": str(font)})
    v = ok(run(home, "validate", short))
    assert any("no glyph for 's' (U+0073)" in w for w in v["warnings"])
    out = ok(run(home, "fonts", "--short", short))
    assert any("'s'" in w for w in out["glyph_warnings"])


def test_settings_hide_the_freesound_key(home):
    out = ok(run(home, "settings", "set", "freesound.api_key", "abc123secret"))
    assert "abc123secret" not in json.dumps(out)
    assert "abc123secret" not in run(home, "settings", "show").stdout
    stored = json.loads((home / "settings.json").read_text(encoding="utf-8"))
    assert stored["freesound"]["api_key"] == "abc123secret"


def test_assets_plan_and_download_sounds_in_process(home, short, monkeypatch, capsys):
    monkeypatch.setenv("BSA_HOME", str(home))
    monkeypatch.delenv("FREESOUND_API_KEY", raising=False)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("sfx/doorCreak_1.wav", _wav_bytes())
        z.writestr("sfx/metalClick.wav", _wav_bytes(900))
    archive = buf.getvalue()
    pack = sounds.PACKS["kenney-rpg"]
    requests = []

    def fake_open(url, headers=None):
        requests.append(url)
        assert url == pack["url"], url
        return io.BytesIO(archive)

    monkeypatch.setattr(sounds, "_urlopen", fake_open)
    monkeypatch.setattr(sounds, "content_length", lambda url: len(archive))
    assert bsa_cli.main(["assets", "plan", str(short), "pack:kenney-rpg"]) == 0
    plan = json.loads(capsys.readouterr().out)
    assert "CC0" in plan["table"] and plan["total_bytes"] == len(archive)
    assert requests == []  # planning downloads nothing
    assert bsa_cli.main(["assets", "download", str(short)]) == 0
    capsys.readouterr()
    assert requests == [pack["url"]]
    lib = json.loads((short.parent / ".assets-cache" / "sounds" / "library.json").read_text(encoding="utf-8"))
    kinds = {Path(c["path"]).name: c["kinds"] for c in lib["clips"]}
    assert kinds == {"doorCreak_1.wav": ["creak"], "metalClick.wav": ["click"]}
    credits = json.loads((short / "source" / "short.json").read_text(encoding="utf-8"))["assets"]
    assert credits[-1]["id"] == "sfx-kenney-rpg" and credits[-1]["license"] == "CC0"


def test_assets_search_sounds_needs_no_key(home, monkeypatch, capsys):
    monkeypatch.setenv("BSA_HOME", str(home))
    monkeypatch.delenv("FREESOUND_API_KEY", raising=False)
    monkeypatch.setattr(sounds, "_urlopen", lambda url, headers=None: io.BytesIO(b'{"query": {"pages": {}}}'))
    assert bsa_cli.main(["assets", "search", "sounds", "click"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert any(r["id"] == "pack:kenney-ui-sfx" for r in out["results"])


def test_mix_plays_build_events(home, short):
    _seed_library(short)
    _edit_short(short, sfx=[])
    ok(run(home, "timeline", short))
    (short / "source" / "events.json").write_text(json.dumps(
        [{"t": 0.5, "kind": "click"}, {"frame": 40, "kind": "thud", "gain": 0.7}]), encoding="utf-8")
    data = ok(run(home, "mix", short))
    assert data["events"] == 2
    assert data["sfx"]["recorded"] == 2
    assert {e["kind"] for e in data["sfx"]["events"]} == {"click", "thud"}
