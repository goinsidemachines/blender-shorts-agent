import copy
import json

import pytest

from bsa import settings


def test_defaults_when_missing(bsa_home):
    s = settings.load_settings()
    assert s == settings.DEFAULTS
    assert s is not settings.DEFAULTS
    assert settings.bsa_home() == bsa_home
    assert settings.settings_path() == bsa_home / "settings.json"


def test_bsa_home_default(monkeypatch, tmp_path):
    monkeypatch.delenv("BSA_HOME", raising=False)
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    assert settings.bsa_home() == tmp_path / ".blender-shorts-agent"


def test_set_and_reload(bsa_home):
    settings.set_value("narrator.provider", "kokoro")
    settings.set_value("handle", "goinside")
    s = settings.load_settings()
    assert s["narrator"]["provider"] == "kokoro"
    assert s["narrator"]["speed"] == 1.0  # untouched defaults survive the merge
    assert s["handle"] == "goinside"
    assert not list(bsa_home.glob("*.tmp"))


def test_corrupt_file_kept_aside(bsa_home):
    settings.settings_path().write_text("{not json", encoding="utf-8")
    s = settings.load_settings()
    assert s == settings.DEFAULTS
    kept = list(bsa_home.glob("settings.corrupt-*.json"))
    assert len(kept) == 1
    assert kept[0].read_text(encoding="utf-8") == "{not json"
    assert not settings.settings_path().exists()


def test_non_object_file_is_corrupt(bsa_home):
    settings.settings_path().write_text(json.dumps([1, 2]), encoding="utf-8")
    assert settings.load_settings() == settings.DEFAULTS
    assert len(list(bsa_home.glob("settings.corrupt-*.json"))) == 1


def test_missing_lists_unanswered(bsa_home):
    s = settings.load_settings()
    assert settings.missing(s) == ["workspace", "narrator.provider", "render.engine", "brand.mode", "rules", "handle",
                                    "closing"]
    settings.set_value("rules_asked", True)  # asked: no hard rules
    settings.set_value("workspace", "C:/Videos/Shorts")
    settings.set_value("narrator.provider", "none")
    settings.set_value("render.engine", "ask-always")
    settings.set_value("brand.mode", "fresh")
    settings.set_value("handle", "")  # asked, and the user wants no handle
    settings.set_value("closing_asked", True)  # asked: no closing card
    assert settings.missing(settings.load_settings()) == []
    s = settings.set_value("render.engine", "eevee")
    assert settings.missing(s) == []
    assert "handle" in settings.missing(settings.DEFAULTS)  # None: never asked


def test_summary_empty_handle_is_no_handle():
    s = {**settings.DEFAULTS, "handle": ""}
    assert " · no handle · " in settings.summary_line(s)


def test_summary_line_exact():
    s = copy.deepcopy(settings.DEFAULTS)
    s["workspace"] = r"C:\Videos\Shorts"
    s["narrator"].update(provider="kokoro", voice="af_heart", speed=1.0)
    s["render"]["engine"] = "cycles"
    s["handle"] = "yourhandle"
    assert settings.summary_line(s) == r"Kokoro (af_heart, 1.0×) · Cycles · @yourhandle · saving to C:\Videos\Shorts"


def test_summary_line_variants():
    base = settings.DEFAULTS
    assert (
        settings.summary_line(base)
        == "narrator not set · engine not set · handle not set · saving to workspace not set"
    )
    s = {**base, "narrator": {"provider": "elevenlabs", "voice": "Callum", "speed": 1.2}}
    assert settings.summary_line(s).startswith("ElevenLabs (Callum, 1.2×) · ")
    s = {**base, "narrator": {"provider": "none", "voice": None, "speed": 1.0}, "render": {"engine": "eevee"}}
    assert settings.summary_line(s).startswith("captions only · EEVEE · ")


def test_oserror_on_read_does_not_rename(bsa_home, monkeypatch):
    import pathlib

    import pytest

    settings.set_value("handle", "goinside")
    real = pathlib.Path.read_text

    def locked(self, *a, **k):
        if self.name == "settings.json":
            raise PermissionError("locked")
        return real(self, *a, **k)

    monkeypatch.setattr(pathlib.Path, "read_text", locked)
    with pytest.raises(PermissionError):
        settings.load_settings()
    monkeypatch.setattr(pathlib.Path, "read_text", real)
    assert settings.settings_path().exists()
    assert not list(bsa_home.glob("settings.corrupt-*.json"))
    assert settings.load_settings()["handle"] == "goinside"


@pytest.mark.parametrize(
    ("key", "given", "stored"),
    [("narrator.provider", "Kokoro", "kokoro"), ("render.engine", "EEVEE", "eevee"), ("brand.mode", "Fresh", "fresh"),
     ("narrator.provider", "none", "none"), ("render.engine", "ask-always", "ask-always")],
)
def test_set_value_normalises_enums(bsa_home, key, given, stored):
    s = settings.set_value(key, settings.check_value(key, given))
    section, name = key.split(".")
    assert s[section][name] == stored


@pytest.mark.parametrize(("key", "bad"), [("narrator.provider", "elevenlab"), ("render.engine", "blender"),
                                          ("brand.mode", "mine")])
def test_unknown_enum_value_is_refused(bsa_home, key, bad):
    with pytest.raises(ValueError, match="one of") as err:
        settings.check_value(key, bad)
    assert bad in str(err.value)
    assert not settings.settings_path().exists()


def test_narrator_enum_covers_every_local_engine():
    from bsa.narrate.run import LOCAL_ENGINES

    assert {"none", "elevenlabs", "custom", *LOCAL_ENGINES} == set(settings.ENUMS["narrator.provider"])


def test_custom_enum_accepted_and_command_stays_a_string(bsa_home):
    assert settings.check_value("narrator.provider", "Custom") == "custom"
    settings.set_value("narrator.provider", "custom")
    settings.set_value("narrator.command", "tts --lines {lines} --out {out}")
    assert settings.load_settings()["narrator"]["command"] == "tts --lines {lines} --out {out}"


def test_custom_without_command_is_unanswered(bsa_home):
    s = settings.load_settings()
    s["workspace"] = "w"
    s["narrator"]["provider"] = "custom"
    assert "narrator.provider" in settings.missing(s)
    s["narrator"]["command"] = "   "
    assert "narrator.provider" in settings.missing(s)
    s["narrator"]["command"] = "mytts --out {out}"
    assert "narrator.provider" not in settings.missing(s)


def test_summary_line_for_custom_shows_tool_basename():
    base = {"workspace": "w", "render": {"engine": "cycles"}, "handle": None}
    s = {**base, "narrator": {"provider": "custom", "command": r'"C:\Tools\my tts\speak.exe" --out {out}'}}
    assert settings.summary_line(s).startswith("your voice tool (speak) · ")
    s["narrator"]["command"] = "/usr/bin/mytts --out {out}"
    assert settings.summary_line(s).startswith("your voice tool (mytts) · ")


def test_summary_unanswered_engine_and_handle_vs_answered():
    s = copy.deepcopy(settings.DEFAULTS)
    line = settings.summary_line(s)
    assert "engine not set" in line and "handle not set" in line
    s["render"]["engine"], s["handle"] = "ask-always", ""
    line = settings.summary_line(s)
    assert "engine: ask each time" in line and " · no handle · " in line


@pytest.mark.parametrize(
    ("command", "voice", "label", "shown"),
    [
        (r'"C:\Py\python.exe" -m chatterbox_cli --model chatterbox --voice {voice} --out {out}', "emma", None,
         "chatterbox · emma"),
        ("python tts.py --model kokoro-v1 --out {out}", None, None, "kokoro-v1"),
        ("python3 tts.py --voice=anna --out {out}", None, None, "anna"),
        ("python -m tts speak --lines {lines} --out {out}", None, None, "tts"),
        (r'"C:\Program Files\Python\python.exe" "D:\my tools\adapter.py" {lines} {out}', None, None, "adapter"),
        ("uv run --project C:/x speak.py {lines}", None, None, "speak"),
        ("python -m tts --out {out}", "emma", "chatterbox · emma", "chatterbox · emma"),
    ],
)
def test_custom_voice_summary_names_the_tool_not_the_interpreter(command, voice, label, shown):
    s = {"workspace": "w", "render": {"engine": "cycles"}, "handle": "",
         "narrator": {"provider": "custom", "command": command, "voice": voice, "label": label}}
    assert settings.summary_line(s).startswith(f"your voice tool ({shown}) · ")


def test_end_card_line_default():
    assert settings.DEFAULTS["end_card"] == "Follow for more"
    assert settings.DEFAULTS["narrator"]["label"] is None


def test_has_end_card_only_with_a_handle():
    assert not settings.has_end_card({"handle": None}) and not settings.has_end_card({"handle": ""})
    assert not settings.has_end_card({"handle": "@"}) and settings.has_end_card({"handle": "me"})


def test_a_short_end_card_overrides_the_setting():
    from bsa.short import Short

    s = {"handle": "me", "end_card": "Follow for more"}
    plain = Short(slug="t", title="T", length_s=10)
    assert settings.end_card(s, plain) == {"handle": "me", "text": "Follow for more", "second": None}
    assert settings.end_card({"handle": "me"}, None)["text"] == "Follow for more"  # the default line
    own = Short(slug="t", title="T", length_s=10, end_card={"text": "Play with it", "second": "example.com"})
    assert settings.end_card(s, own) == {"handle": "me", "text": "Play with it", "second": "example.com"}
    assert settings.end_card(s, Short(slug="t", title="T", length_s=10, end_card="Mine"))["text"] == "Mine"
    assert settings.end_card(s, Short(slug="t", title="T", length_s=10, end_card={"text": ""}))["text"] is None
    nobody = {"handle": ""}
    assert not settings.has_end_card(nobody, plain)
    assert settings.end_card(nobody, plain) == {"handle": None, "text": None, "second": None}  # no handle: no card
    assert settings.has_end_card(nobody, own)  # the short's own card shows without a handle
    assert settings.end_card(nobody, own) == {"handle": None, "text": "Play with it", "second": "example.com"}
