"""The voice check: word differences, the per-line block, and that it never moves the narration version."""

import json
import os
import time

import pagefix

from bsa.narrate import run
from bsa.narrate.local import WER_LIMIT
from bsa.review import snapshot, voice, worddiff
from bsa.short import load_short


def test_word_diff_marks_changed_missing_and_extra():
    assert worddiff.diff("Length sets the beat.", "length set the the beat") == [
        ["same", "length"], ["changed", "set", "sets"], ["same", "the"], ["extra", "the"], ["same", "beat"]]  # fmt: skip
    assert worddiff.diff("Four times longer", "four longer") == [
        ["same", "four"], ["missing", "times"], ["same", "longer"]]  # fmt: skip


def test_words_normalise_case_punctuation_and_dashes():
    assert worddiff.words("It's a ten-metre\u2014tall \u201cclock\u201d!") == [
        "it's", "a", "ten", "metre", "tall", "clock"]  # fmt: skip
    assert worddiff.words("It takes 5 seconds") == ["it", "takes", "5", "seconds"]


def _block(sdir):
    return voice.block(sdir, load_short(sdir / "source" / "short.json"), {})


def test_voice_block_marks_failed_lines(tmp_path):
    sdir = pagefix.make_short(tmp_path, "narration")
    pagefix.write_heard(sdir, {"l01": "a pendulum swings like clockwork", "l02": "length set the"})
    rows = _block(sdir)["lines"]
    assert [r["id"] for r in rows] == ["l01", "l02", "l03", "l04"]
    assert rows[0]["ok"] is True and rows[0]["wer"] == 0.0
    assert rows[1]["ok"] is False and rows[1]["wer"] == 0.5
    assert rows[2]["ok"] is None and rows[2]["said"] is None  # never heard


def test_no_heard_file_is_not_narrated_yet(tmp_path):
    sdir = pagefix.make_short(tmp_path, "narration")
    assert _block(sdir) == {"lines": [], "checked": False, "why_not": "Not narrated yet."}
    assert "voice" not in snapshot.build(sdir, settings={})["stages"][4]["content"]


def test_heard_entry_for_old_words_is_not_a_diff(tmp_path):
    sdir = pagefix.make_short(tmp_path, "narration")
    pagefix.write_heard(sdir, {"l02": "length sets the tempo"})
    path = sdir / "audio" / "vo" / run.HEARD_FILE
    data = json.loads(path.read_text(encoding="utf-8"))
    data["lines"]["l02"]["text"] = "Length sets the tempo."  # the voice was asked for other words
    path.write_text(json.dumps(data), encoding="utf-8")
    row = _block(sdir)["lines"][1]
    assert row["stale"] is True and row["diff"] is None and row["ok"] is None and row["said"] is None


def test_a_line_whose_take_was_deleted_shows_unchecked_not_the_old_heard_text(tmp_path):
    sdir = pagefix.make_short(tmp_path, "narration")
    pagefix.write_heard(sdir, {"l02": "length set the beat"})
    (sdir / "audio" / "vo" / "l02.wav").unlink()  # what a re-take does
    row = _block(sdir)["lines"][1]
    assert row["waiting"] is True
    assert row["said"] is None and row["diff"] is None and row["ok"] is None and row["wer"] is None


def test_heard_with_a_reason_it_was_not_checked(tmp_path):
    sdir = pagefix.make_short(tmp_path, "narration")
    pagefix.write_heard(sdir, {}, checked=False, why_not=run.WHISPER_MISSING)
    b = _block(sdir)
    assert b["checked"] is False and b["why_not"] == run.WHISPER_MISSING and b["lines"][0]["ok"] is None


def test_voice_never_moves_the_narration_version(tmp_path):
    sdir = pagefix.make_short(tmp_path, "narration")
    pagefix.write_heard(sdir, {"l02": "length sets the beat"})
    ctx = snapshot.load_ctx(sdir, {})
    before = snapshot.stage_version(sdir, "narration", ctx)
    pagefix.write_heard(sdir, {"l02": "length set the beat"})
    assert snapshot.stage_version(sdir, "narration", snapshot.load_ctx(sdir, {})) == before


def test_a_take_newer_than_heard_json_is_waiting_not_the_old_diff(tmp_path):
    """A re-take writes the new wav before heard.json is rewritten: until then the old words must not show."""
    sdir = pagefix.make_short(tmp_path, "narration")
    pagefix.write_heard(sdir, {"l02": "length set the beat"})
    vo = sdir / "audio" / "vo"
    old = time.time() - 100
    os.utime(vo / run.HEARD_FILE, (old, old))
    (vo / "l02.wav").write_bytes(b"RIFF")  # touched after heard.json
    row = _block(sdir)["lines"][1]
    assert row["waiting"] is True
    assert row["said"] is None and row["diff"] is None and row["ok"] is None and row["wer"] is None


def test_word_diff_slides_over_a_run_of_equal_words():
    assert worddiff.diff("go on now", "go on on now") == [
        ["same", "go"], ["same", "on"], ["extra", "on"], ["same", "now"]]  # fmt: skip
    assert worddiff.diff("the the the cat", "the the cat") == [
        ["same", "the"], ["same", "the"], ["missing", "the"], ["same", "cat"]]  # fmt: skip
    assert worddiff.diff("a b b b c", "a x b b b c")[:3] == [["same", "a"], ["extra", "x"], ["same", "b"]]


def test_a_corrupt_heard_file_says_so(tmp_path):
    sdir = pagefix.make_short(tmp_path, "narration")
    vo = sdir / "audio" / "vo"
    vo.mkdir(parents=True, exist_ok=True)
    (vo / run.HEARD_FILE).write_text("{not json", encoding="utf-8")
    b = _block(sdir)
    assert b == {"lines": [], "checked": False, "why_not": "The voice check file can't be read."}
    (vo / run.HEARD_FILE).write_text("[1]", encoding="utf-8")
    assert _block(sdir)["why_not"] == "The voice check file can't be read."


def test_a_word_error_rate_at_the_limit_passes(tmp_path):
    sdir = pagefix.make_short(tmp_path, "narration")
    pagefix.write_heard(sdir, {"l01": "a pendulum swings like clockwork"})
    path = sdir / "audio" / "vo" / run.HEARD_FILE
    data = json.loads(path.read_text(encoding="utf-8"))
    data["lines"]["l01"]["wer"] = WER_LIMIT
    path.write_text(json.dumps(data), encoding="utf-8")
    assert _block(sdir)["lines"][0]["ok"] is True
    data["lines"]["l01"]["wer"] = WER_LIMIT + 0.01
    path.write_text(json.dumps(data), encoding="utf-8")
    assert _block(sdir)["lines"][0]["ok"] is False
