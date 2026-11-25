"""bsa.timing: the timing / captions settings, the short's own values over them, and that every consumer follows them."""

import json

import numpy as np
import pytest

from bsa import doctor, settings, timeline, timing
from bsa.narrate import takes
from bsa.short import Line, Short, Shot, load_short, save_short, validate
from bsa.timeline import build_timeline, chunk_caption, pacing

TEXT = "One two three four five six seven eight nine ten eleven twelve."


def _short(lines=None, **kw):
    shots = [Shot("s01", "Hook", "pic", "Scene", min_s=1.0), Shot("s02", "Two", "pic", "Scene", min_s=1.0)]
    lines = lines or [Line("l01", TEXT, "s01"), Line("l02", "Second line of words.", "s01"), Line("l03", "Last.", "s02")]
    return Short(slug="t", title="T", length_s=10, lines=lines, shots=shots, **kw)


def test_unset_gives_the_same_timeline_as_the_constants():
    short = _short()
    for end_card in (False, True):
        for durations in (None, {"l01": 2.0, "l02": 1.3, "l03": 0.9}):
            resolved = timing.resolve({}, short)
            assert build_timeline(short, durations, end_card, timing=resolved) == build_timeline(
                short, durations, end_card
            )
    assert timing.resolve(settings.DEFAULTS, _short()) == timing.Timing()
    assert timing.Timing() == timeline.default_timing()


def test_defaults_in_settings_are_the_constants():
    assert settings.DEFAULTS["timing"] == {
        "lead_in_s": 0.2, "gap_s": 0.35, "tail_s": 0.6, "take_tail_s": 0.35, "end_card_s": 1.5,
    }  # fmt: skip
    assert settings.DEFAULTS["captions"] == {"max_words": 6, "max_chars": 32, "orphan_max_chars": 8}
    assert (timeline.LEAD_IN_S, timeline.GAP_S, timeline.TAIL_S, timeline.END_CARD_S) == (0.2, 0.35, 0.6, 1.5)
    assert takes.TAIL_S == 0.35


@pytest.mark.parametrize(
    ("key", "const"),
    [("lead_in_s", "LEAD_IN_S"), ("gap_s", "GAP_S"), ("tail_s", "TAIL_S"), ("end_card_s", "END_CARD_S")],
)
def test_each_timing_key_acts_as_its_old_constant(monkeypatch, key, const):
    short = _short()
    durations = {"l01": 2.0, "l02": 1.3, "l03": 0.9}
    new = build_timeline(short, durations, True, timing=timing.resolve({"timing": {key: 1.0}}, short))
    monkeypatch.setattr(timeline, const, 1.0)
    old = build_timeline(short, durations, True)
    assert new == old
    assert new != build_timeline(short, durations, True, timing=timing.Timing())


@pytest.mark.parametrize(
    ("key", "const", "value"),
    [("max_words", "CARD_MAX_WORDS", 3), ("max_chars", "CARD_MAX_CHARS", 14), ("orphan_max_chars", "ORPHAN_MAX_CHARS", 0)],
)
def test_each_captions_key_acts_as_its_old_constant(monkeypatch, key, const, value):
    text = "One two three four five six. Go. Seven eight nine ten, then stop now please."
    new = chunk_caption(text, timing.resolve({"captions": {key: value}}))
    monkeypatch.setattr(timeline, const, value)
    assert new == chunk_caption(text)
    assert new != chunk_caption(text, timing.Timing())


def test_one_take_timeline_follows_timing():
    short = _short(camera={"style": "one-take", "seconds": 20})
    short.shots[0].at = 0
    short.shots[1].at = 10
    durations = {"l01": 2.0, "l02": 1.3, "l03": 0.9}
    base = build_timeline(short, durations, False)
    moved = build_timeline(short, durations, False, timing=timing.Timing(lead_in_s=0.5))
    assert moved.lines[0].f0 == base.lines[0].f0 + round(0.3 * 30)


def test_the_short_wins_over_the_settings():
    s = {"timing": {"gap_s": 0.5, "tail_s": 0.9}, "captions": {"max_words": 4}}
    short = _short(timing={"gap_s": 0.1}, captions={"max_chars": 20})
    t = timing.resolve(s, short)
    assert (t.gap_s, t.tail_s, t.max_words, t.max_chars) == (0.1, 0.9, 4, 20)
    assert t.lead_in_s == 0.2 and t.end_card_s == 1.5  # not named anywhere: the default


def test_bad_values_are_ignored_by_resolve():
    assert timing.resolve({"timing": {"gap_s": "x", "bogus": 1}, "captions": {"max_words": 99}}) == timing.Timing()


@pytest.mark.parametrize(
    ("path", "value"),
    [("timing.gap_s", 3), ("timing.gap_s", -0.1), ("timing.gap_s", "x"), ("timing.gap_s", True),
     ("timing.end_card_s", 5.5), ("captions.max_words", 0), ("captions.max_words", 13), ("captions.max_words", 2.5),
     ("captions.max_chars", 5), ("captions.max_chars", 61), ("captions.orphan_max_chars", 21),
     ("timing.nope", 1), ("captions.nope", 1)],
)  # fmt: skip
def test_settings_reject_bad_values(path, value):
    with pytest.raises(ValueError, match=path.split(".")[0]):
        settings.check_value(path, value)


def test_settings_accept_good_values_and_whole_objects():
    assert settings.check_value("timing.end_card_s", 5) == 5.0
    assert settings.check_value("timing.gap_s", 0) == 0.0
    assert settings.check_value("captions.max_words", 12) == 12
    assert settings.check_value("captions", {"max_chars": 40}) == {"max_chars": 40}
    with pytest.raises(ValueError, match="unknown keys"):
        settings.check_value("timing", {"gap": 1})
    with pytest.raises(ValueError, match="object"):
        settings.check_value("captions", 3)


def test_short_validation_names_the_problems():
    assert not [p for p in validate(_short(timing={"gap_s": 0.1}, captions={"max_words": 4})) if "timing" in p]
    errors = validate(_short(timing={"gap_s": 9, "x": 1}, captions={"max_words": 0}))
    assert any("timing has unknown keys ['x']" in e for e in errors)
    assert any("timing.gap_s" in e for e in errors)
    assert any("captions.max_words" in e for e in errors)
    assert any("timing must be an object" in e for e in validate(_short(timing=3)))


def test_short_json_round_trip_keeps_only_set_values(tmp_path):
    path = tmp_path / "short.json"
    save_short(_short(), path)
    assert "timing" not in json.loads(path.read_text("utf-8"))
    save_short(_short(timing={"gap_s": 0.2}), path)
    assert load_short(path).timing == {"gap_s": 0.2}


def test_workers_get_the_values_from_the_settings_and_short_files(bsa_home):
    """A worker Blender loads the settings file and short.json itself: no patched import needed."""
    import bsa_cli

    short = _short()
    plain = bsa_cli._reading_timeline(short)
    settings.set_value("timing.gap_s", 0.0)
    settings.set_value("captions.max_words", 3)
    from_settings = bsa_cli._reading_timeline(short)
    assert from_settings != plain
    assert from_settings == build_timeline(short, None, False, timing=timing.Timing(gap_s=0.0, max_words=3))
    short.timing = {"gap_s": 0.5}
    assert bsa_cli._reading_timeline(short) == build_timeline(
        short, None, False, timing=timing.Timing(gap_s=0.5, max_words=3)
    )


def test_pacing_follows_timing():
    short = _short()
    t = timing.Timing(gap_s=1.0)
    tl = build_timeline(short, None, False, timing=t)
    assert pacing(short, tl, False, t)[0]["suggest_s"] > pacing(short, tl, False)[0]["suggest_s"]


def test_take_grouping_and_cut_follow_timing():
    lines = [Line("l01", "Short one.", "s01"), Line("l02", "Short two.", "s02")]
    held = [Shot("s01", "a", "p", "s", min_s=4.0), Shot("s02", "b", "p", "s", min_s=1.0)]
    assert len(takes.group(lines, held)) == 2  # held 4 s: much longer than the lines need
    assert len(takes.group(lines, held, timing.Timing(tail_s=2.0))) == 1  # a longer tail: the lines need it
    sr = takes.SR
    x = np.concatenate([np.full(sr, 0.5), np.zeros(sr), np.full(sr, 0.5)])
    default = takes.cut(x, ["one", "two"], ["a", "b"])
    shorter = takes.cut(x, ["one", "two"], ["a", "b"], tail_s=0.05)
    assert default[0].end != shorter[0].end


def test_takes_record_notes_a_non_default_take_tail():
    from bsa.narrate import run

    narrator = {"provider": "kokoro", "take_mode": "paragraph", "_timing": timing.Timing(take_tail_s=0.1)}
    assert run._takes_record(_short(), narrator)["take_tail_s"] == 0.1
    assert "take_tail_s" not in run._takes_record(_short(), {**narrator, "_timing": timing.Timing()})


def test_changed_timing_makes_the_timeline_stale():
    from bsa.timeline import timeline_hash, to_json

    short = _short()

    def fingerprint(t):
        return timeline_hash(json.loads(json.dumps(to_json(build_timeline(short, None, False, timing=t)))))

    assert fingerprint(timing.Timing()) != fingerprint(timing.Timing(gap_s=0.0))
    assert fingerprint(timing.Timing()) != fingerprint(timing.Timing(max_words=3))


def test_doctor_warns_about_patched_constants(tmp_path):
    src = tmp_path / "source"
    src.mkdir()
    (src / "tight.py").write_text(
        "from bsa import timeline\ntimeline.GAP_S = 0.1\ntimeline.CARD_MAX_WORDS=4\n", encoding="utf-8"
    )
    (src / "ok.py").write_text("from bsa import timeline\nx = timeline.GAP_S == 0.1\n", encoding="utf-8")
    checks = doctor.timing_patches(tmp_path)
    assert len(checks) == 1 and checks[0]["level"] == "warn"
    assert "tight.py" in checks[0]["text"] and "ok.py" not in checks[0]["text"]
    assert "timing.gap_s" in checks[0]["fix"] and "captions.max_words" in checks[0]["fix"]
    assert doctor.timing_patches(None) == [] and doctor.timing_patches(tmp_path / "none") == []
    assert any(c["id"] == "timing_patch" for c in doctor.run({}, tmp_path, sdir=tmp_path))
