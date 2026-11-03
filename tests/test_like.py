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
    assert data["narrator"] == {"take_mode": "paragraph", "lead_in": 0.4, "voice": "af_test", "speed": 1.2,
                                "voice_for": "kokoro"}
    assert data["like"]["short"] == other.name and data["like"]["at"].endswith("Z")
    assert data["like"]["copied"] == list(like.COPY_KEYS) == out["copied"]
    assert data["like"]["title"] == "Big Ben" and data["like"]["style_keys"] == like.style_keys(data["style"])
    assert out["like"] == other.name and out["notes"] == ["this short keeps Big Ben's look as its own"]
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


def _voice_notes(notes):
    return [n for n in notes if "voice" in n]


def test_like_copies_the_voice_only_for_the_same_provider(tmp_path):
    other = _rich(tmp_path, record=_KOKORO)
    values, copied, notes = like.copy_from(other, {"narrator": {"provider": "elevenlabs", "voice": "x"}})
    assert values["narrator"] == {"take_mode": "paragraph", "lead_in": 0.4} and "narrator" in copied
    assert _voice_notes(notes) == ["the voice wasn't copied: Big Ben used Kokoro, you now use ElevenLabs"]
    values, _, notes = like.copy_from(other, _SAME)
    assert values["narrator"] == {"take_mode": "paragraph", "lead_in": 0.4, "voice": "af_test", "speed": 1.2,
                                  "voice_for": "kokoro"}
    assert _voice_notes(notes) == []
    # narrated with the defaults: narrator.json has no take_mode / lead_in, so line and no lead-in
    # values equal to what the settings give a new short (line, no lead-in, speed 1.0) are not pinned
    plain = _rich(tmp_path / "b", record={"provider": "kokoro", "voice": "af_test", "speed": 1.0})
    assert like.copy_from(plain, _SAME)[0]["narrator"] == {"voice": "af_test", "voice_for": "kokoro"}
    # never narrated: take mode and lead-in from its own choices over the settings; no voice
    silent = _rich(tmp_path / "c")
    values, _, notes = like.copy_from(silent, _SAME)
    assert values["narrator"] == {"take_mode": "paragraph", "lead_in": 0.3}
    assert _voice_notes(notes) == ["the voice wasn't copied: Big Ben hasn't been narrated yet"]


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
    number = int(other.name.split("-")[0])
    assert bsa_cli.main(["new-short", "Other", "--number", str(number), "--like", other.name]) != 0
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


def test_like_never_pins_what_equals_the_current_settings(tmp_path):
    from bsa.short import load_short

    settings_ = {"narrator": {"provider": "kokoro", "voice": "af_test", "speed": 1.2, "take_mode": "paragraph",
                              "lead_in": 0.4},
                 "brand": {"mode": "brand", "style": "toon", "set": "workbench", "palette": {"accent": "#E8B33A"},
                           "caption": {"size": 60}}}  # fmt: skip
    other = _rich(tmp_path, record=_KOKORO)
    p = other / "source" / "short.json"
    short = load_short(p)
    short.look = "brand"
    short.style = {"style": "toon", "set": "graph", "palette": {"accent": "#e8b33a", "text": "#101010"},
                   "caption": {"size": 60, "box": "#000000"}, "material": "felt"}  # fmt: skip
    save_short(short, p)
    values, copied, notes = like.copy_from(other, settings_)
    assert "narrator" not in values and "narrator" not in copied  # voice, speed, take mode, lead-in all equal
    assert "look" not in values  # the brand look is what the settings give anyway
    assert values["style"] == {"set": "graph", "palette": {"text": "#101010"}, "caption": {"box": "#000000"},
                               "material": "felt"}  # fmt: skip
    assert notes == []
    skipped = like.skipped_questions(copied, values.get("narrator") or {})
    assert "voice" not in skipped and "voice" not in like.said("Big Ben", skipped)
    # everything equal: no style at all
    short.style = {"style": "toon", "palette": {"accent": "#E8B33A"}}
    save_short(short, p)
    assert "style" not in like.copy_from(other, settings_)[0]


def test_voice_is_skipped_only_when_the_voice_was_copied(tmp_path):
    other = _rich(tmp_path, record=_KOKORO)
    values, copied, _ = like.copy_from(other, {"narrator": {"provider": "elevenlabs"}})
    assert values["narrator"] == {"take_mode": "paragraph", "lead_in": 0.4} and "narrator" in copied
    skipped = like.skipped_questions(copied, values["narrator"])
    assert "voice" not in skipped and skipped == ["look", "length", "pace", "closing"]


def test_captions_only_source_says_so(tmp_path):
    other = _rich(tmp_path, record={"provider": "none"})
    assert like.copy_from(other, _SAME)[2] == ["this short keeps Big Ben's look as its own",
                                               "the voice wasn't copied: Big Ben was captions only"]  # fmt: skip


def test_like_record_accepts_title_and_style_keys_and_checks_their_type():
    from bsa.short import _like_problems

    ok = {"short": "01-a", "copied": ["style"], "at": "2026-11-04T10:00:00Z"}
    assert _like_problems(ok) == [] and _like_problems({**ok, "title": "A", "style_keys": ["font"]}) == []
    assert _like_problems({**ok, "title": 3}) and _like_problems({**ok, "style_keys": "font"})


def test_a_copied_voice_is_not_used_once_the_provider_changes(tmp_path, monkeypatch, capsys):
    from bsa.narrate import run as nrun
    from bsa.short import Line, Shot, load_short

    ws = _cli_ws(tmp_path, monkeypatch, _SAME)
    other = _rich(ws, record=_KOKORO)
    assert bsa_cli.main(["new-short", "Eiffel Tower", "--like", other.name]) == 0
    sdir = Path(json.loads(capsys.readouterr().out)["short"])
    p = sdir / "source" / "short.json"
    short = load_short(p)
    assert short.narrator["voice_for"] == "kokoro" and short.narrator["voice"] == "af_test"
    assert nrun.narrator_for({"narrator": {"provider": "kokoro", "voice": "x"}}, short)["voice"] == "af_test"
    short.lines = [Line("l01", "It is tall.", "s01")]
    short.shots = [Shot("s01", "Tower", "a tower", "tower")]
    save_short(short, p)
    jobstate.mark_done(sdir, "storyboard")
    home = Path(os.environ["BSA_HOME"])
    (home / "settings.json").write_text(json.dumps({"workspace": str(ws), "narrator": {
        "provider": "elevenlabs", "voice": "el_settings", "speed": 1.0}}), encoding="utf-8")  # fmt: skip
    used = {}

    def fake_narrate(short_dir, short_, settings_):
        used.update(nrun.narrator_for(settings_, short_))
        return nrun.NarrationResult("captions-only", None, "no voice in this test")

    monkeypatch.setattr(nrun, "narrate", fake_narrate)
    monkeypatch.setattr(bsa_cli, "cmd_timeline", lambda args: {})
    assert bsa_cli.main(["narrate", sdir.name]) == 0
    out = json.loads(capsys.readouterr().out)
    assert used["voice"] == "el_settings" and used["speed"] == 1.0 and "voice_for" not in used
    assert used["take_mode"] == "paragraph" and used["lead_in"] == 0.4  # take mode and lead-in still the short's
    assert out["notes"] == [("this short's own voice was chosen for Kokoro, but you now use ElevenLabs, so the voice "
                             "from your settings was used")]  # fmt: skip


def test_an_older_short_without_voice_for_keeps_its_voice():
    from bsa.narrate import run as nrun

    short = Short(slug="a", title="A", length_s=40, narrator={"voice": "own", "speed": 1.1})
    got = nrun.narrator_for({"narrator": {"provider": "elevenlabs", "voice": "el"}}, short)
    assert got["voice"] == "own" and got["speed"] == 1.1
    assert nrun.own_voice_dropped({"narrator": {"provider": "elevenlabs"}}, short) is None
    short.narrator = {"voice": "own", "voice_for": "kokoro"}
    assert nrun.own_voice_dropped({"narrator": {"provider": "elevenlabs"}}, short) == "kokoro"
    assert nrun.own_voice_dropped({"narrator": {"provider": "kokoro"}}, short) is None


def test_voice_for_is_checked_by_validate():
    from bsa.short import _narrator_problems

    assert _narrator_problems({"voice": "v", "voice_for": "kokoro"}) == []
    assert _narrator_problems({"voice_for": ""}) and _narrator_problems({"voice_for": 3})
    assert _narrator_problems({"lead_in": 2}) == ["narrator.lead_in must be seconds from 0 to 1, got 2"]


def test_a_style_value_the_kit_refuses_now_is_not_copied(tmp_path):
    from bsa.short import load_short

    other = _rich(tmp_path, record=_KOKORO)
    p = other / "source" / "short.json"
    short = load_short(p)
    short.style = {"style": "toon", "set": "no-such-set", "palette": {"accent": "#E8B33A", "glow": "#FFFFFF"}}
    save_short(short, p)
    values, _, notes = like.copy_from(other, _SAME)
    assert "set" not in values["style"] and "glow" not in values["style"].get("palette", {})
    assert values["style"]["palette"] == {"accent": "#E8B33A"}
    assert "Big Ben's set isn't valid in your kit now, so it wasn't copied" in notes
    assert "Big Ben's palette.glow isn't valid in your kit now, so it wasn't copied" in notes


def test_summary_says_a_damaged_state_and_a_voice_without_a_name(tmp_path):
    sdir = _make(tmp_path, "Big Ben")
    (sdir / "state.json").write_text("{nope", encoding="utf-8")
    row = like.summary(sdir, {})
    assert row["problem"] == "state.json damaged" and row["finished"] is False and row["title"] == "Big Ben"
    _voice(sdir, voice=None)
    assert like.summary(sdir, {})["narrator"] == "Kokoro"
    _voice(sdir, voice="", speed="fast")
    assert like.summary(sdir, {})["narrator"] == "Kokoro"


def test_summary_with_a_malformed_edit_record_uses_the_short_length(tmp_path):
    sdir = _make(tmp_path, "Big Ben", length=40)
    for edit in ({"fps": 30}, {"frames": "x", "fps": 30}, {"frames": 10, "fps": 0}, "nope"):
        (sdir / "engine_runs.json").unlink(missing_ok=True)
        engine_runs.record_mp4(sdir, "final", engine_runs.EngineRun(None), sdir / "out" / "a.mp4",
                               edit={"timeline_hash": "x", "fps": 30, "frames": 1290, "closing": None})
        path = next(p for p in sdir.rglob("*.json") if "1290" in p.read_text("utf-8"))
        data = json.loads(path.read_text("utf-8"))
        data["final"]["edit"] = edit
        path.write_text(json.dumps(data), "utf-8")
        row = like.summary(sdir, {})
        assert row["length_s"] == 40 and row["problem"] is None, edit
