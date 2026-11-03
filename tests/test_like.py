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


def _rich(root, title="Big Ben", *, record=None):
    """An earlier short with everything a short can hold, narrated by `record` (a narrator.json) when given."""
    from bsa.short import Asset, Fact, Line, Shot

    sdir = workspace.new_short(root, title)
    short = Short(slug=workspace.slugify(title), title=title, length_s=40, topic="clock towers", audience="kids",
                  style={"style": "toon", "set": "workbench", "font": "Some Font", "caption": {"size": 60}},
                  look="fresh", end_card={"text": "Follow for more", "second": "example.org"},
                  closing={"seconds": 2.0}, render={"engine": "cycles"},
                  narrator={"take_mode": "paragraph", "lead_in": 0.3, "voice": "own", "speed": 1.1},
                  facts=[Fact("f1", "It is 96 m tall", "https://example.org/ben")],
                  lines=[Line("l01", "It is 96 m tall.", "s01", ["f1"]), Line("l02", "It ticks.", "s02")],
                  shots=[Shot("s01", "Tower", "a tower", "tower", min_s=3.0, hold=True),
                         Shot("s02", "Gears", "gears", "gears", min_s=4.0),
                         Shot("s03", "Bell", "a bell", "bell", min_s=6.0, hold=[[0, 1]])],
                  assets=[Asset("a1", "Gear", "model", "https://example.org/g", "CC0")],
                  post={"title": "Why Big Ben?", "description": "d", "hashtags": ["#x"]})  # fmt: skip
    save_short(short, sdir / "source" / "short.json")
    (sdir / "out").mkdir(exist_ok=True)
    (sdir / "out" / "thumbnail.png").write_bytes(b"png")
    if record is not None:
        out = sdir / "audio" / "vo"
        out.mkdir(parents=True, exist_ok=True)
        (out / "narrator.json").write_text(json.dumps(record), encoding="utf-8")
    return sdir


_SAME = {"narrator": {"provider": "kokoro"}}
_KOKORO = {"provider": "kokoro", "voice": "af_test", "speed": 1.2, "take_mode": "paragraph", "lead_in": 0.4}


def _cli_ws(tmp_path, monkeypatch, settings_=None):
    ws = tmp_path / "ws"
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("BSA_HOME", str(home))
    (home / "settings.json").write_text(json.dumps({"workspace": str(ws), **(settings_ or {})}), encoding="utf-8")
    return ws


def test_pace_of_counts_shots_median_and_holds(tmp_path):
    from bsa.short import load_short

    sdir = _rich(tmp_path)
    assert like.pace_of(load_short(sdir / "source" / "short.json")) == {"shots": 3, "min_s": 4.0, "holds": 2}


def test_like_copies_look_pace_closing_and_voice_only(tmp_path, monkeypatch, capsys):
    ws = _cli_ws(tmp_path, monkeypatch, _SAME)
    other = _rich(ws, record=_KOKORO)
    assert bsa_cli.main(["new-short", "Eiffel Tower", "--like", other.name]) == 0
    out = json.loads(capsys.readouterr().out)
    data = json.loads((Path(out["short"]) / "source" / "short.json").read_text("utf-8"))
    assert data["facts"] == [] and data["lines"] == [] and data["shots"] == [] and data["sfx"] == []
    assert data["assets"] == [] and data["post"] == {"title": "", "description": "", "hashtags": []}
    assert data["topic"] == "" and data["audience"] == "" and data["title"] == "Eiffel Tower"
    assert data["style"] == {"style": "toon", "set": "workbench", "font": "Some Font", "caption": {"size": 60}}
    assert data["look"] == "fresh" and data["length_s"] == 40
    assert data["pace"] == {"shots": 3, "min_s": 4.0, "holds": 2}
    assert data["end_card"] == {"text": "Follow for more", "second": "example.org"}
    assert data["closing"] == {"seconds": 2.0}
    assert data["narrator"] == {"take_mode": "paragraph", "lead_in": 0.4, "voice": "af_test", "speed": 1.2}
    assert data["like"]["short"] == other.name and data["like"]["at"].endswith("Z")
    assert data["like"]["copied"] == list(like.COPY_KEYS) == out["copied"]
    assert out["like"] == other.name and out["notes"] == []
    assert out["skipped"] == ["look", "length", "pace", "closing", "voice"]
    assert out["said"].startswith("Started from Big Ben: I copied the look, length, pace, closing and voice")
    assert not list((Path(out["short"]) / "out").glob("*"))  # no thumbnail
    from bsa.short import load_short, validate

    assert validate(load_short(Path(out["short"]) / "source" / "short.json")) == ["the short has no lines"]


def test_like_never_copies_the_render_engine(tmp_path, monkeypatch, capsys):
    ws = _cli_ws(tmp_path, monkeypatch, {**_SAME, "render": {"engine": "eevee"}})
    other = _rich(ws, record=_KOKORO)
    values, copied, _ = like.copy_from(other, {**_SAME, "render": {"engine": "eevee"}})
    assert "render" not in values and "render" not in copied
    assert bsa_cli.main(["new-short", "Eiffel Tower", "--like", "1"]) == 0
    sdir = Path(json.loads(capsys.readouterr().out)["short"])
    data = json.loads((sdir / "source" / "short.json").read_text("utf-8"))
    assert data["render"] == {}
    assert "engine" not in json.dumps({k: v for k, v in data.items() if k != "render"})
    assert "engine" not in jobstate.load_state(sdir)["stages"]
    source = (ROOT / "bsa" / "like.py").read_text("utf-8")
    assert '"render"' not in source and "'render'" not in source


def test_like_copies_the_voice_only_for_the_same_provider(tmp_path):
    other = _rich(tmp_path, record=_KOKORO)
    values, copied, notes = like.copy_from(other, {"narrator": {"provider": "elevenlabs", "voice": "x"}})
    assert values["narrator"] == {"take_mode": "paragraph", "lead_in": 0.4} and "narrator" in copied
    assert notes == ["the voice wasn't copied: Big Ben used Kokoro, you now use ElevenLabs"]
    values, _, notes = like.copy_from(other, _SAME)
    assert values["narrator"] == {"take_mode": "paragraph", "lead_in": 0.4, "voice": "af_test", "speed": 1.2}
    assert notes == []
    # narrated with the defaults: narrator.json has no take_mode / lead_in, so line and no lead-in
    plain = _rich(tmp_path / "b", record={"provider": "kokoro", "voice": "af_test", "speed": 1.0})
    assert like.copy_from(plain, _SAME)[0]["narrator"] == {"take_mode": "line", "lead_in": 0.0, "voice": "af_test",
                                                            "speed": 1.0}  # fmt: skip
    # never narrated: take mode and lead-in from its own choices over the settings; no voice
    silent = _rich(tmp_path / "c")
    values, _, notes = like.copy_from(silent, _SAME)
    assert values["narrator"] == {"take_mode": "paragraph", "lead_in": 0.3}
    assert notes == ["the voice wasn't copied: Big Ben hasn't been narrated yet"]


def test_like_refuses_a_broken_or_self_source(tmp_path, monkeypatch, capsys):
    import pytest

    ws = _cli_ws(tmp_path, monkeypatch, _SAME)
    broken = _make(ws, "Broken")
    (broken / "source" / "short.json").write_text("{nope", encoding="utf-8")
    with pytest.raises(ValueError, match=f"{broken.name}'s short.json can't be read"):
        like.copy_from(broken, _SAME)
    assert bsa_cli.main(["new-short", "New", "--like", broken.name]) != 0
    err = capsys.readouterr()
    assert f"{broken.name}'s short.json can't be read" in err.out + err.err
    assert [p.name for p in ws.iterdir() if p.is_dir() and p.name[:1].isdigit()] == [broken.name]  # nothing made
    other = _rich(ws, "Other", record=_KOKORO)
    monkeypatch.setattr(bsa_cli.workspace, "new_short", lambda root, title, number=None: other)
    assert bsa_cli.main(["new-short", "Other", "--like", other.name]) != 0
    err = capsys.readouterr()
    assert "itself" in err.out + err.err
    assert json.loads((other / "source" / "short.json").read_text("utf-8"))["like"] is None
    assert bsa_cli.main(["new-short", "New", "--like", "no-such-short"]) != 0


def test_explicit_length_wins_over_the_copied_one(tmp_path, monkeypatch, capsys):
    ws = _cli_ws(tmp_path, monkeypatch, _SAME)
    other = _rich(ws, record=_KOKORO)
    assert bsa_cli.main(["new-short", "Eiffel Tower", "--like", other.name, "--length", "60"]) == 0
    out = json.loads(capsys.readouterr().out)
    data = json.loads((Path(out["short"]) / "source" / "short.json").read_text("utf-8"))
    assert data["length_s"] == 60 and "length_s" not in out["copied"] and "length_s" not in data["like"]["copied"]
    assert "length" not in out["skipped"]
    assert out["said"] == ("Started from Big Ben: I copied the look, pace, closing and voice, so I won't ask about "
                           "those.")  # fmt: skip


def test_skipped_questions_and_the_one_line_said():
    assert like.skipped_questions(["style", "look", "pace", "end_card", "closing", "narrator"]) == [
        "look", "pace", "closing", "voice"]  # fmt: skip
    assert like.skipped_questions([]) == []
    assert like.said("Big Ben", ["look", "pace", "closing", "voice"]) == (
        "Started from Big Ben: I copied the look, pace, closing and voice, so I won't ask about those.")
    assert like.said("Big Ben", ["voice"]) == "Started from Big Ben: I copied the voice, so I won't ask about it."
    assert like.provenance(Path("x/03-big-ben"), ["pace"], now=0) == {"short": "03-big-ben", "copied": ["pace"],
                                                                      "at": "1970-01-01T00:00:00Z"}  # fmt: skip
    assert set(like.SKIP) == set(like.COPY_KEYS) == set(like.LABELS)
