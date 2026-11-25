"""bsa.presets: named style presets, their resolution order, the CLI and the page line."""

import json
from pathlib import Path

import pytest

import bsa_cli
from bsa import presets, settings, timeline, timing, workspace
from bsa.short import Line, Short, Shot, load_short, save_short, validate


def _short(**kw):
    return Short(slug="t", title="T", length_s=10, lines=[Line("l01", "One.", "s01")],
                 shots=[Shot("s01", "Hook", "pic", "Scene", min_s=1.0)], **kw)  # fmt: skip


def test_shipped_presets_are_neutral_and_only_use_allowed_groups():
    assert set(presets.SHIPPED) == {"explainer", "fast-loop"}
    assert presets.SHIPPED["explainer"] == {}
    banned = {"handle", "website", "name", "voice", "narrator", "engine", "render", "brand", "end_card", "closing"}
    for name, preset in presets.SHIPPED.items():
        assert presets.preset_problems(name, preset, presets.SHIPPED) == []
        assert set(preset) <= set(presets.GROUPS) | {presets.EXTEND}
        keys = set(preset) | {k for group in preset.values() if isinstance(group, dict) for k in group}
        assert not keys & banned
    assert presets.SHIPPED["fast-loop"]["timing"]["gap_s"] == 0.2


def test_default_is_unchanged_and_fast_loop_applies():
    assert timing.resolve({}, _short()) == timing.Timing()
    assert timing.resolve({"style_preset": "explainer"}, _short()) == timing.Timing()
    t = timing.resolve({"style_preset": "fast-loop"}, _short())
    assert (t.gap_s, t.end_card_s, t.max_words, t.max_chars, t.orphan_max_chars) == (0.2, 0.0, 3, 18, 6)
    assert timeline.default_timing() == timing.Timing()


def test_resolution_order_defaults_settings_preset_short():
    s = {"timing": {"gap_s": 0.5, "tail_s": 0.9}, "style_preset": "fast-loop"}
    t = timing.resolve(s, _short(timing={"gap_s": 0.25}))
    assert t.gap_s == 0.25  # the short beats the preset
    assert t.tail_s == 0.1  # the preset beats the settings' own top-level value
    assert timing.resolve({"timing": {"tail_s": 0.9}}, _short()).tail_s == 0.9  # settings beat the defaults
    mine = {"presets": {"slow": {"timing": {"gap_s": 1.0}}}, "style_preset": "fast-loop"}
    assert timing.resolve(mine, _short(preset="slow")).gap_s == 1.0  # the short's preset beats the settings' choice
    assert timing.resolve(mine, _short(preset="slow")).tail_s == 0.6  # slow doesn't touch it: the default shows


def test_extend_chain_base_first_and_user_overrides_shipped():
    s = {"presets": {"mine": {"extend": "fast-loop", "timing": {"gap_s": 0.3}, "captions": {"max_words": 4}}},
         "style_preset": "mine"}
    t = timing.resolve(s, _short())
    assert (t.gap_s, t.tail_s, t.max_words, t.max_chars) == (0.3, 0.1, 4, 18)
    over = {"presets": {"fast-loop": {"timing": {"gap_s": 0.9}}}, "style_preset": "fast-loop"}
    t = timing.resolve(over, _short())
    assert t.gap_s == 0.9 and t.tail_s == 0.6 and t.max_words == 6  # replaced, not merged with the shipped one
    assert presets.source_of("fast-loop", over) == "user" and presets.source_of("explainer", over) == "shipped"


def test_resolution_is_tolerant_but_validation_reports():
    loop = {"presets": {"a": {"extend": "b"}, "b": {"extend": "a"}}, "style_preset": "a"}
    assert timing.resolve(loop, _short()) == timing.Timing()  # a loop is ignored at build time
    assert any("loop" in p for p in presets.problems(loop))
    unknown = {"style_preset": "nope"}
    assert timing.resolve(unknown, _short()) == timing.Timing()
    assert any("unknown preset 'nope'" in p for p in presets.problems(unknown))
    assert any("unknown preset 'zzz'" in p for p in presets.problems({}, _short(preset="zzz")))
    assert any("unknown preset" in p for p in presets.problems({"presets": {"a": {"extend": "ghost"}}}))
    bad = {"presets": {"a": {"timing": {"gap_s": 99}}}, "style_preset": "a"}
    assert timing.resolve(bad, _short()).gap_s == 0.35 and presets.problems(bad)


def test_render_engine_and_unknown_groups_are_refused():
    msg = "render engine is always the user's choice per short; presets can't set it"
    assert any(msg in p for p in presets.preset_problems("p", {"render": {"engine": "cycles"}}))
    assert any(msg in p for p in presets.preset_problems("p", {"engine": "cycles"}))
    assert any("unknown groups" in p for p in presets.preset_problems("p", {"bogus": True}))
    assert any("unknown groups" in p for p in presets.preset_problems("p", {"colours": {}}))
    with pytest.raises(ValueError, match="render engine"):
        settings.check_value("presets.x", {"render": {"engine": "eevee"}})
    with pytest.raises(ValueError, match="letters, digits"):
        settings.check_value("presets", {"bad name": {}})


def test_short_preset_roundtrip_and_validate(tmp_path):
    path = tmp_path / "s" / "short.json"
    save_short(_short(), path)
    assert "preset" not in json.loads(path.read_text("utf-8"))  # an unset preset leaves the file as before
    save_short(_short(preset="fast-loop"), path)
    assert load_short(path).preset == "fast-loop"
    assert validate(_short(preset="fast-loop")) == []
    assert any("preset must be" in p for p in validate(_short(preset=3)))


def test_settings_set_validates_style_preset(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("BSA_HOME", str(tmp_path))
    assert bsa_cli.main(["settings", "set", "style_preset", "fast-loop"]) == 0
    assert settings.load_settings()["style_preset"] == "fast-loop"
    capsys.readouterr()
    assert bsa_cli.main(["settings", "set", "style_preset", "nope"]) != 0
    assert "unknown preset 'nope'" in capsys.readouterr().err
    assert settings.load_settings()["style_preset"] == "fast-loop"
    assert bsa_cli.main(["settings", "set", "style_preset", "null"]) == 0
    assert settings.load_settings()["style_preset"] is None
    capsys.readouterr()
    assert bsa_cli.main(["settings", "set", "presets.mine", '{"timing": {"gap_s": 0.3}}']) == 0
    assert settings.load_settings()["presets"]["mine"] == {"timing": {"gap_s": 0.3}}
    assert bsa_cli.main(["settings", "set", "presets.mine", "null"]) == 0
    assert "mine" not in settings.load_settings()["presets"]


def _ws(tmp_path, monkeypatch, extra=None):
    ws = tmp_path / "ws"
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("BSA_HOME", str(home))
    (home / "settings.json").write_text(json.dumps({"workspace": str(ws), **(extra or {})}), encoding="utf-8")
    return ws


def _make(ws, title, **kw):
    sdir = workspace.new_short(ws, title)
    save_short(Short(slug=workspace.slugify(title), title=title, length_s=40, **kw), sdir / "source" / "short.json")
    return sdir


def _out(capsys):
    return json.loads(capsys.readouterr().out)


def test_cli_preset_list_and_show(tmp_path, monkeypatch, capsys):
    ws = _ws(tmp_path, monkeypatch, {"style_preset": "fast-loop", "timing": {"tail_s": 0.8},
                                     "presets": {"mine": {"extend": "fast-loop", "timing": {"gap_s": 0.3}}}})
    _make(ws, "Alpha", timing={"gap_s": 0.25}, captions={"max_words": 2})
    assert bsa_cli.main(["preset", "list"]) == 0
    out = _out(capsys)
    rows = {r["name"]: r for r in out["presets"]}
    assert rows["explainer"]["source"] == "shipped" and rows["mine"]["source"] == "user"
    assert rows["mine"]["extends"] == "fast-loop" and rows["fast-loop"]["default"] and out["active"] == "fast-loop"
    assert bsa_cli.main(["preset", "show", "mine", "--short", "alpha"]) == 0
    out = _out(capsys)
    assert out["chain"] == ["fast-loop", "mine"]
    assert out["values"]["gap_s"] == 0.25 and out["from"]["gap_s"] == "short"
    assert out["from"]["tail_s"] == "preset:fast-loop" and out["values"]["tail_s"] == 0.1
    assert out["from"]["max_words"] == "short" and out["from"]["lead_in_s"] == "preset:fast-loop"
    assert bsa_cli.main(["preset", "show", "explainer"]) == 0
    out = _out(capsys)
    assert out["from"]["tail_s"] == "settings" and out["from"]["gap_s"] == "default"
    assert bsa_cli.main(["preset", "show", "ghost"]) != 0


def test_cli_preset_save(tmp_path, monkeypatch, capsys):
    ws = _ws(tmp_path, monkeypatch)
    _make(ws, "Alpha", timing={"gap_s": 0.25, "tail_s": 0.6}, captions={"max_words": 3})
    assert bsa_cli.main(["preset", "save", "tight", "--from", "alpha"]) == 0
    capsys.readouterr()
    assert settings.load_settings()["presets"]["tight"] == {"timing": {"gap_s": 0.25}, "captions": {"max_words": 3}}
    assert bsa_cli.main(["preset", "save", "tight", "--from", "alpha"]) != 0  # no overwrite
    assert bsa_cli.main(["preset", "save", "fast-loop", "--from", "alpha"]) != 0  # a shipped name
    capsys.readouterr()
    assert bsa_cli.main(["preset", "save", "tight", "--from", "alpha", "--force"]) == 0
    assert bsa_cli.main(["preset", "save", "fast-loop", "--from", "alpha", "--force"]) == 0
    assert settings.load_settings()["presets"]["fast-loop"]["timing"] == {"gap_s": 0.25}


def test_cli_validate_reports_an_unknown_short_preset(tmp_path, monkeypatch, capsys):
    ws = _ws(tmp_path, monkeypatch)
    _make(ws, "Alpha", preset="nope")
    bsa_cli.main(["validate", "alpha"])
    assert "unknown preset 'nope'" in capsys.readouterr().out


def test_like_copies_the_preset_unless_it_is_already_the_default(tmp_path, monkeypatch, capsys):
    ws = _ws(tmp_path, monkeypatch)
    other = _make(ws, "Big Ben", preset="fast-loop")
    assert bsa_cli.main(["new-short", "Eiffel", "--like", other.name]) == 0
    out = _out(capsys)
    data = json.loads((Path(out["short"]) / "source" / "short.json").read_text("utf-8"))
    assert data["preset"] == "fast-loop" and "preset" in out["copied"] and "preset" in data["like"]["copied"]
    home = tmp_path / "home" / "settings.json"
    home.write_text(json.dumps({**json.loads(home.read_text("utf-8")), "style_preset": "fast-loop"}), "utf-8")
    assert bsa_cli.main(["new-short", "Louvre", "--like", other.name]) == 0
    assert "preset" not in _out(capsys)["copied"]


def test_page_line_shows_the_preset_and_the_short_overrides():
    assert presets.line({}, _short()) == "Style preset: explainer"
    s = {"style_preset": "fast-loop"}
    assert presets.line(s, _short(timing={"gap_s": 0.25})) == "Style preset: fast-loop (short overrides: gap_s 0.25)"
    from bsa.review import snapshot

    assert "style_preset" in snapshot._UNVERSIONED["storyboard"]
