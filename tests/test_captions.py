"""Caption files: SRT, VTT and the transcript, cue for cue with the captions the final video shows."""

import itertools
import json
import math
import wave

import numpy as np
import pagefix
import pytest
from test_cli import home, ok, pendulum, run, workspace  # noqa: F401 - fixtures

from bsa import captions
from bsa.short import load_short
from bsa.timeline import Card

TEXT_120 = ("The period of a pendulum depends on its length and on gravity, and never on how heavy "
            "the swinging weight is at the end.")


def _short(sdir):
    return load_short(sdir / "source" / "short.json")


def _take(path, seconds):
    t = np.arange(round(seconds * 48000)) / 48000
    pcm = (0.3 * np.sin(2 * math.pi * 220 * t) * 32767).astype("<i2")
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(48000)
        w.writeframes(pcm.tobytes())


def test_cues_are_the_captions_cue_for_cue():
    cards = [Card("Length sets the beat.", "l01", 1, 45), Card("Not the weight.", "l02", 56, 90)]
    assert captions.to_srt(captions.cues(cards, 30), 30) == (
        "1\n00:00:00,000 --> 00:00:01,500\nLength sets the beat.\n\n"
        "2\n00:00:01,833 --> 00:00:03,000\nNot the weight.\n")  # fmt: skip
    assert captions.to_vtt(captions.cues(cards, 30), 30).startswith("WEBVTT\n\n00:00:00.000 --> 00:00:01.500\n")


def test_stamp_rounds_to_the_millisecond():
    assert captions.stamp(1 / 30, ",") == "00:00:00,033" and captions.stamp(2 / 30, ".") == "00:00:00.067"
    assert captions.stamp(3661.0005, ",") == "01:01:01,001"


def test_a_long_card_wraps_at_42_and_splits_past_two_lines():
    assert len(captions.wrap(TEXT_120)) > 2
    cs = captions.cues([Card(TEXT_120, "l01", 1, 90)], 30)
    assert len(cs) == 2 and all(len(c.lines) <= 2 and all(len(x) <= 42 for x in c.lines) for c in cs)
    assert cs[0].f0 == 1 and cs[0].f1 + 1 == cs[1].f0 and cs[1].f1 == 90


def test_an_overlong_word_gets_its_own_line():
    assert captions.wrap("see https://example.com/a-very-long-path-that-goes-on-and-on now") == [
        "see", "https://example.com/a-very-long-path-that-goes-on-and-on", "now"]  # fmt: skip


def test_a_cue_under_half_a_second_joins_the_one_before():
    cs = captions.cues([Card("One two", "l01", 1, 30), Card("three.", "l01", 31, 40)], 30)
    assert [(c.f0, c.f1, c.lines) for c in cs] == [(1, 40, ["One two three."])]


def test_a_short_cue_that_cannot_join_borrows_frames():
    full = "a" * 20 + " " + "b" * 21  # 42 characters on one line
    full2 = "c" * 20 + " " + "d" * 21
    before = Card(f"{full} {full2}", "l01", 1, 60)  # two full lines
    short_card = Card("Then more.", "l02", 61, 70)  # 10 frames, under the 15 wanted
    cs = captions.cues([before, short_card], 30)
    assert len(cs) == 2
    assert (cs[0].f0, cs[0].f1) == (1, 55) and (cs[1].f0, cs[1].f1) == (56, 70)
    assert cs[1].f1 - cs[1].f0 + 1 == 15 and cs[0].lines == [full, full2]


def test_a_short_first_cue_joins_the_next():
    cs = captions.cues([Card("Hi.", "l01", 1, 10), Card("Length sets the beat.", "l01", 11, 60)], 30)
    assert [(c.f0, c.f1, c.lines) for c in cs] == [(1, 60, ["Hi. Length sets the beat."])]


def test_a_cue_that_cannot_borrow_stays():
    full = " ".join(["a" * 20 + " " + "b" * 21, "c" * 20 + " " + "d" * 21])
    cs = captions.cues([Card(full, "l01", 1, 19), Card("Short one.", "l02", 20, 29)], 30)
    assert [(c.f0, c.f1) for c in cs] == [(1, 19), (20, 29)]  # 19 - 5 would leave 14, under the 15 wanted


def test_text_never_say(tmp_path):
    sdir = pagefix.make_short(tmp_path, "done")
    path = sdir / "source" / "short.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["lines"][0].update(text="Taipei", say="Tie-pay")
    path.write_text(json.dumps(data), encoding="utf-8")
    pagefix.write_timeline(sdir)
    pagefix.record_edit(sdir, "final")
    out = captions.write_files(sdir, _short(sdir), {})
    srt = (sdir / "out" / "pendulum.srt").read_text(encoding="utf-8")
    text = (sdir / "out" / "pendulum-transcript.txt").read_text(encoding="utf-8")
    assert "Taipei" in srt and "Taipei" in text and "Tie-pay" not in srt + text and out["cues"] > 0


def test_transcript_is_one_paragraph_per_shot_then_the_closing_line(tmp_path):
    short = _short(pagefix.make_short(tmp_path, "done"))
    paras = captions.transcript(short, "Visit us.").rstrip("\n").split("\n\n")
    want = [" ".join(ln.text for ln in short.lines if ln.shot == s.id) for s in short.shots]
    assert paras == [w for w in want if w] + ["Visit us."]
    assert captions.transcript(short, None).endswith("\n") and "Visit us." not in captions.transcript(short, None)


def test_closing_line_cue_when_narrated(tmp_path):
    sdir = pagefix.make_short(tmp_path, "done")
    pagefix.record_edit(sdir, "final", closing=75)  # closing card 31-105 on the 30-frame story
    _take(sdir / "audio" / "vo" / "closing.wav", 1.0)
    s = {"website": "example.com", "closing": {"voice_line": "Find out more."}}
    out = captions.write_files(sdir, _short(sdir), s)
    last = (sdir / "out" / "pendulum.srt").read_text(encoding="utf-8").rstrip("\n").split("\n\n")[-1]
    assert last == f"{out['cues']}\n{captions.stamp(36 / 30, ',')} --> {captions.stamp(66 / 30, ',')}\nFind out more."
    assert (sdir / "out" / "pendulum-transcript.txt").read_text(encoding="utf-8").endswith("\n\nFind out more.\n")


def test_stale_timeline_writes_nothing(tmp_path):
    sdir = pagefix.make_short(tmp_path, "done")
    pagefix.record_edit(sdir, "final", stale=True)
    with pytest.raises(captions.Stale, match="older timing"):
        captions.write_files(sdir, _short(sdir), {})
    assert not list((sdir / "out").glob("*.srt")) and not list((sdir / "out").glob("*.vtt"))
    assert captions.state(sdir, _short(sdir)) == {"stale": True, "why": captions.STALE}


def test_no_final_edit_writes_nothing(tmp_path):
    sdir = pagefix.make_short(tmp_path, "story")
    with pytest.raises(captions.Stale) as exc:
        captions.write_files(sdir, _short(sdir), {})
    assert str(exc.value) == captions.NO_FINAL
    assert not list((sdir / "out").glob("*.srt")) if (sdir / "out").exists() else True


def test_files_are_utf8_without_bom_and_lf(tmp_path):
    sdir = pagefix.make_short(tmp_path, "done")
    captions.write_files(sdir, _short(sdir), {})
    for name in ("pendulum.srt", "pendulum.vtt", "pendulum-transcript.txt"):
        raw = (sdir / "out" / name).read_bytes()
        assert not raw.startswith(b"\xef\xbb\xbf") and b"\r" not in raw and raw.endswith(b"\n")
    assert not list((sdir / "out").glob("*.tmp"))


def test_cli_captions_writes_three_files(home, pendulum):  # noqa: F811
    ok(run(home, "timeline", "pendulum"))
    pagefix.record_edit(pendulum, "final")
    out = ok(run(home, "captions", "pendulum"))
    assert out["cues"] > 0
    for key in ("srt", "vtt", "transcript"):
        assert (pendulum / "out").joinpath(out[key].replace("\\", "/").split("/")[-1]).is_file()
    assert (pendulum / "out" / "pendulum.vtt").read_text(encoding="utf-8").startswith("WEBVTT\n\n")


def test_cli_captions_refuses_a_stale_final(home, pendulum):  # noqa: F811
    ok(run(home, "timeline", "pendulum"))
    pagefix.record_edit(pendulum, "final", stale=True)
    proc = run(home, "captions", "pendulum")
    assert proc.returncode != 0 and "older timing" in proc.stderr + proc.stdout
    assert not list((pendulum / "out").glob("*.vtt"))


def _stubbed_edit(monkeypatch, sdir, tl_hash):
    """bsa_blender._cmd_edit with Blender's parts stubbed: it reaches the caption hook after recording the MP4."""
    import types

    import bsa_blender
    from bsa import engine_runs, freshness

    mp4 = sdir / "out" / "pendulum.mp4"
    mp4.parent.mkdir(exist_ok=True)
    for name, value in (("_engine_run", engine_runs.EngineRun(None)), ("_fingerprints", {}), ("_rendered", []),
                        ("_check_keys", {}), ("_parts", {}), ("_label_heroes", None), ("_drop_outgoing", None),
                        ("_closing_plan", (None, []))):  # fmt: skip
        monkeypatch.setattr(bsa_blender, name, (lambda *a, _v=value, **k: _v))
    monkeypatch.setattr(bsa_blender, "_timeline", lambda sdir, short: (types.SimpleNamespace(n_frames=30), tl_hash))
    monkeypatch.setattr(freshness, "require_checks", lambda *a, **k: None)
    monkeypatch.setattr(freshness, "load_anchors", lambda *a, **k: {})
    info = {"width": 1080, "height": 1920, "frames": 30, "fps": 30.0, "has_audio": False}

    def encode(scene, progress=None):
        mp4.write_bytes(b"mp4")
        return mp4

    fake = types.SimpleNamespace(build_edit=lambda *a, **k: object(), encode=encode, probe=lambda p: info,
                                 end_card_note=lambda s: None, glyph_fallbacks=lambda s: [],
                                 font_used=lambda s: "f", font_note=lambda s: None)  # fmt: skip
    run = types.SimpleNamespace(progress=lambda *a, **k: None)
    args = types.SimpleNamespace(preset="final")
    short = bsa_blender._load(sdir)
    return lambda: bsa_blender._cmd_edit(args, sdir, short, run, fake)


def test_the_final_edit_writes_the_caption_files(tmp_path, monkeypatch):
    sdir = pagefix.make_short(tmp_path, "story")
    data = pagefix.write_timeline(sdir)
    go = _stubbed_edit(monkeypatch, sdir, captions.timeline.timeline_hash(data))
    out = go()
    assert out["captions"]["cues"] > 0 and (sdir / "out" / "pendulum.vtt").is_file() and "warnings" not in out


def test_a_final_edit_whose_timing_moved_warns_instead_of_writing_captions(tmp_path, monkeypatch):
    sdir = pagefix.make_short(tmp_path, "story")
    pagefix.write_timeline(sdir)
    go = _stubbed_edit(monkeypatch, sdir, "0" * 16)  # the edit was cut from another timing
    out = go()
    assert "captions" not in out and out["warnings"][0].startswith("caption files not written: ")
    assert "older timing" in out["warnings"][0] and not list((sdir / "out").glob("*.vtt"))


def test_a_card_too_short_for_its_groups_keeps_all_its_text_without_overlap():
    text = "a" * 40 + " " + "b" * 40 + " " + "c" * 40  # wraps to 3 lines
    cs = captions.cues([Card("Before.", "l01", 1, 30), Card(text, "l02", 31, 31), Card("After.", "l03", 32, 62)], 30)
    mid = [c for c in cs if c.lines == text.split(" ")]  # one cue holding all the text
    assert len(mid) == 1 and len(cs) == 3
    assert cs[0].f0 == 1 and cs[-1].f1 == 62 and all(a.f1 + 1 == b.f0 for a, b in itertools.pairwise(cs))


def test_a_malformed_timeline_warns_instead_of_losing_the_edit_result(tmp_path, monkeypatch):
    sdir = pagefix.make_short(tmp_path, "story")
    data = pagefix.write_timeline(sdir)
    go = _stubbed_edit(monkeypatch, sdir, captions.timeline.timeline_hash(data))
    monkeypatch.setattr(captions, "write_files", lambda *a, **k: (_ for _ in ()).throw(KeyError("cards")))
    out = go()
    assert out["mp4"] and "captions" not in out and out["warnings"] == ["caption files not written: 'cards'"]
