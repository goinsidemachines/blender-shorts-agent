"""CLI shorts: the workspace's shorts with their look, length and voice."""

import json
import os
import sys
from pathlib import Path

from bsa import engine_runs, jobstate, like, workspace
from bsa.short import Short, save_short

sys.path.insert(0, str(Path(__file__).parent))
import kitfix

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import bsa_cli


def _make(root, title, *, style=None, length=45.0):
    sdir = workspace.new_short(root, title)
    save_short(Short(slug=workspace.slugify(title), title=title, length_s=length, style=style or {}),
               sdir / "source" / "short.json")
    return sdir


def _voice(sdir, provider="kokoro", voice="af_heart", speed=1.0):
    out = sdir / "audio" / "vo"
    out.mkdir(parents=True, exist_ok=True)
    (out / "narrator.json").write_text(json.dumps({"provider": provider, "voice": voice, "speed": speed}),
                                       encoding="utf-8")


def test_summary_reads_style_set_length_and_voice(tmp_path):
    sdir = _make(tmp_path, "Big Ben", style={"style": "toon", "set": "workbench"})
    _voice(sdir)
    engine_runs.record_mp4(sdir, "final", engine_runs.EngineRun(None), sdir / "out" / "a.mp4",
                           edit={"timeline_hash": "x", "fps": 30, "frames": 1290, "closing": None})
    row = like.summary(sdir, {"brand": kitfix.KIT})
    assert row["short"] == sdir.name and row["number"] == 1 and row["title"] == "Big Ben"
    assert row["style"] == "toon" and row["set"] == "workbench"
    assert row["length_s"] == 43.0
    assert row["narrator"] == "Kokoro (af_heart, 1.0×)"
    assert row["finished"] is False and row["problem"] is None
    jobstate.mark_done(sdir, "final")
    assert like.summary(sdir, {})["finished"] is True


def test_summary_without_edit_or_voice(tmp_path):
    sdir = _make(tmp_path, "Plain", length=40)
    row = like.summary(sdir, {})
    assert row["length_s"] == 40 and row["narrator"] == "not narrated" and row["style"] is None


def test_list_is_newest_first_and_survives_a_broken_short(tmp_path):
    a = _make(tmp_path, "One")
    _make(tmp_path, "Two")
    (a / "source" / "short.json").write_text("{nope", encoding="utf-8")
    rows = like.list_shorts(tmp_path, {})
    assert [r["number"] for r in rows] == [2, 1]
    assert rows[0]["problem"] is None
    assert rows[1]["problem"] and rows[1]["title"] is None and rows[1]["style"] is None


def test_cli_shorts(tmp_path, monkeypatch, capsys):
    ws = tmp_path / "ws"
    for i in range(5):
        sdir = _make(ws, f"Short {i}")
        jobstate.mark_done(sdir, "final")
    _make(ws, "Unfinished")
    monkeypatch.setenv("BSA_HOME", str(tmp_path / "home"))
    os.makedirs(tmp_path / "home")
    (tmp_path / "home" / "settings.json").write_text(json.dumps({"workspace": str(ws)}), encoding="utf-8")
    assert bsa_cli.main(["shorts"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert len(out["shorts"]) == 6 and out["shorts"][0]["title"] == "Unfinished"
    assert out["last_finished"] == ["Short 4", "Short 3", "Short 2"]
