"""Optional script and edit targets (bsa.targets): the settings group and its checks, each target firing and staying
silent at its edges, nothing at all when nothing is set, and the command line."""

import json
import os
import shutil
import sys
import wave
from pathlib import Path

import numpy as np
import pytest

from bsa import jobstate, presets, settings, targets
from bsa import proc as _proc
from bsa.short import Line, Short, Shot, load_short, save_short, validate
from bsa.timeline import build_timeline

ROOT = Path(__file__).resolve().parent.parent
CLI = ROOT / "bsa_cli.py"
SAMPLE = ROOT / "tests" / "e2e" / "pendulum" / "source" / "short.json"
SR = 48000


# ------------------------------------------------------------------------------------------------ number counting


@pytest.mark.parametrize(
    "text, want",
    [
        ("No numbers in here.", []),
        ("It weighs 1,000 kg and 3.5 m, up 50%.", [1000, 3.5, 50]),
        ("Twenty five gears", [25]),
        ("twenty-one gears and four wheels", [21, 4]),
        ("two hundred and five", [205]),
        ("one hundred twenty three thousand", [123000]),
        ("a hundred", [100]),
        ("one thousand five", [1005]),
        ("half, double, twice and triple", [0.5, 2, 2, 3]),
        ("one two three", [1, 2, 3]),
        ("a *fifty* percent drop", [50]),
        ("a million, a billion", [1e6, 1e9]),
    ],
)
def test_numbers_in(text, want):
    assert targets.numbers_in(text) == [float(w) for w in want]


def test_words_ignore_highlight_asterisks_and_bare_punctuation():
    assert targets.count_words("A *pendulum* swings — like clockwork.") == 5


# --------------------------------------------------------------------------------------------------- the settings


def test_every_target_defaults_to_off_and_checks_its_range():
    assert set(targets.defaults().values()) == {None}
    assert targets.check("wpm", [170, 180]) == [170.0, 180.0]
    assert targets.check("max_numbers", 3) == 3 and isinstance(targets.check("max_numbers", 3.0), int)
    assert targets.check("hook_callback", True) is True and targets.check("wpm", None) is None
    for key, bad in [
        ("wpm", [59, 100]), ("wpm", [100, 401]), ("wpm", [180, 170]), ("wpm", [100]), ("wpm", 150),
        ("max_pause_s", -0.1), ("max_pause_s", 5.1), ("max_numbers", 51), ("max_numbers", 2.5),
        ("max_numbers", True), ("max_cuts", 101), ("max_cuts", -1), ("length_s", [0.5, 10]),
        ("length_s", [10, 601]), ("payoff_by_s", 601), ("payoff_by_s", -1), ("hook_callback", "yes"),
        ("motion_at_frame_1", 1), ("nope", 1),
    ]:  # fmt: skip
        with pytest.raises(ValueError):
            targets.check(key, bad)
    assert targets.check("max_pause_s", 0) == 0.0 and targets.check("payoff_by_s", 600) == 600.0
    assert targets.check("length_s", [1, 600]) == [1.0, 600.0] and targets.check("wpm", [60, 60]) == [60.0, 60.0]


def test_problems_in_names_every_wrong_key():
    assert targets.problems_in(None) == [] and targets.problems_in({"max_cuts": 4}) == []
    bad = targets.problems_in({"wpm": [200, 100], "colour": 1})
    assert len(bad) == 2 and any("min at most max" in b for b in bad) and any("unknown keys" in b for b in bad)
    assert targets.problems_in([1]) and targets.problems_in("x")


def test_settings_set_validates_targets(monkeypatch, tmp_path):
    monkeypatch.setenv("BSA_HOME", str(tmp_path))
    assert settings.check_value("targets.max_numbers", 3) == 3
    assert settings.check_value("targets", {"wpm": [150, 160]}) == {"wpm": [150.0, 160.0]}
    with pytest.raises(ValueError):
        settings.check_value("targets.max_numbers", 99)
    with pytest.raises(ValueError):
        settings.check_value("targets", {"wpm": [10, 20]})
    assert settings.DEFAULTS["targets"] == targets.defaults()


def test_resolution_order_and_presets():
    s = {"targets": {"max_numbers": 5, "max_cuts": 9}, "presets": {"tight": {"targets": {"max_numbers": 3}}},
         "style_preset": "tight"}  # fmt: skip
    values, where = presets.targets_resolved(s, {"targets": {"max_cuts": 2}})
    assert values["max_numbers"] == 3 and where["max_numbers"] == "preset:tight"
    assert values["max_cuts"] == 2 and where["max_cuts"] == "short" and where["wpm"] == "default"
    assert targets.resolved(s, {"targets": {"max_cuts": 2}}) == {"max_numbers": 3, "max_cuts": 2}
    assert targets.resolved({}, None) == {}
    assert targets.resolved({"targets": {"hook_callback": False}}, None) == {}  # false is off


def test_presets_and_shorts_report_bad_targets():
    bad_preset = {"presets": {"x": {"targets": {"wpm": [500, 600]}}}}
    assert any("preset 'x'" in p and "wpm" in p for p in presets.problems(bad_preset))
    assert any("short.json" in p and "max_cuts" in p for p in presets.problems({}, {"targets": {"max_cuts": 500}}))
    assert any("targets" in p for p in presets.problems({"targets": {"wpm": "fast"}}))
    saved = presets.from_short({"targets": {"max_numbers": 3}}, {})
    assert saved["targets"] == {"max_numbers": 3}
    assert "targets" in presets.GROUPS


def _short(lines, shots=None, **kw):
    shots = shots or [Shot("s1", "One", "p", "sc"), Shot("s2", "Two", "p", "sc"), Shot("s3", "Three", "p", "sc")]
    return Short(slug="t", title=kw.pop("title", "Gears"), length_s=20, lines=lines, shots=shots, **kw)


def test_short_validates_targets_and_one_payoff():
    lines = [Line("l1", "Hi there.", "s1")]
    assert validate(_short(lines, targets={"wpm": [170, 180]})) == []
    assert any("wpm" in e for e in validate(_short(lines, targets={"wpm": [10, 20]})))
    shots = [Shot("s1", "One", "p", "sc", payoff=True), Shot("s2", "Two", "p", "sc", payoff=True)]
    assert any("only one shot can be the payoff" in e for e in validate(_short(lines, shots)))
    shots = [Shot("s1", "One", "p", "sc", payoff="yes")]
    assert any("payoff must be true or false" in e for e in validate(_short(lines, shots)))


def test_payoff_and_targets_are_written_only_when_set(tmp_path):
    plain = _short([Line("l1", "Hi there.", "s1")])
    path = tmp_path / "short.json"
    save_short(plain, path)
    data = json.loads(path.read_text(encoding="utf-8"))
    assert "targets" not in data and all("payoff" not in s for s in data["shots"])
    plain.shots[1].payoff = True
    plain.targets = {"max_cuts": 2}
    save_short(plain, path)
    back = load_short(path)
    assert back.shots[1].payoff is True and back.shots[0].payoff is False and back.targets == {"max_cuts": 2}


# ------------------------------------------------------------------------------------------------- script targets


def test_max_numbers_edges():
    short = _short([Line("l1", "One gear turns two gears.", "s1"), Line("l2", "That is 3 in all.", "s2")])
    assert targets.script_rows(short, {"max_numbers": 3})[0]["ok"] is True  # "One", "two", "3"
    miss = targets.script_rows(short, {"max_numbers": 2})[0]
    assert miss["ok"] is False and miss["measured"] == 3
    assert targets.warnings_of([miss]) == ["3 numbers spoken, target at most 2"]
    assert targets.script_rows(_short([Line("l1", "Say it.", "s1")]), {"max_numbers": 0})[0]["ok"] is True


def test_numbers_use_the_spoken_words_when_a_line_has_say():
    ln = Line("l1", "About 50 percent.", "s1", say="About fifty percent.")
    assert targets.script_rows(_short([ln]), {"max_numbers": 1})[0]["ok"] is True


def test_hook_callback_fires_and_stays_silent():
    echo = _short([Line("l1", "A *pendulum* swings like clockwork.", "s1"), Line("l2", "Pendulums keep time.", "s2")])
    row = targets.script_rows(echo, {"hook_callback": True})[0]
    assert row["ok"] is True and row["measured"] == ["pendulum"]  # plural stripped, asterisks gone
    assert "shares pendulum" in row["text"] and "no word" not in row["text"]  # a pass names the shared words
    no_echo = _short([Line("l1", "A pendulum swings.", "s1"), Line("l2", "Follow for more.", "s2")], title="Clocks")
    miss = targets.script_rows(no_echo, {"hook_callback": True})[0]
    assert miss["ok"] is False and "simple word-overlap" in miss["text"] and "shares no word" in miss["text"]
    via_title = _short([Line("l1", "A pendulum swings.", "s1"), Line("l2", "Clocks use this.", "s2")], title="Clocks")
    assert targets.script_rows(via_title, {"hook_callback": True})[0]["ok"] is True
    stop_only = _short([Line("l1", "It is the one.", "s1"), Line("l2", "That is the end.", "s2")], title="x")
    assert targets.script_rows(stop_only, {"hook_callback": True})[0]["ok"] is False  # stopwords never count
    single = _short([Line("l1", "Only line.", "s1")])
    assert targets.script_rows(single, {"hook_callback": True})[0]["ok"] is None


def test_script_rows_need_lines_and_a_target():
    assert targets.script_rows(_short([]), {"max_numbers": 1, "hook_callback": True}) == []
    assert targets.script_rows(_short([Line("l1", "One.", "s1")]), {}) == []


# ------------------------------------------------------------------------------------------- storyboard targets


def test_numbers_on_screen():
    shots = [Shot("s1", "One", "p", "sc", labels=["Length 4", {"text": "Two bobs", "near": "A"}]),
             Shot("s2", "T", "p", "sc")]  # fmt: skip
    lines = [Line("l1", "Four times longer, twice slower.", "s1"), Line("l2", "Quiet here.", "s2")]
    assert targets.storyboard_rows(_short(lines, shots), {"numbers_on_screen": True})[0]["ok"] is True  # "Two bobs"
    shots[0].labels = ["Length 4"]
    miss = targets.storyboard_rows(_short(lines, shots), {"numbers_on_screen": True})[0]
    assert miss["ok"] is False and miss["measured"] == ["s1: 2"]
    shots[0].labels.append("x2")
    assert targets.storyboard_rows(_short(lines, shots), {"numbers_on_screen": True})[0]["ok"] is True
    shots[0].labels = []
    shots[0].badge = "4 times"
    shots[0].bars = {"items": [{"label": "Beat", "value": 2, "text": "2x"}]}
    assert targets.storyboard_rows(_short(lines, shots), {"numbers_on_screen": True})[0]["ok"] is True


def test_numbers_on_screen_is_per_shot_and_silent_without_numbers():
    shots = [Shot("s1", "One", "p", "sc", labels=["3 gears"]), Shot("s2", "T", "p", "sc")]
    lines = [Line("l1", "Plain words.", "s1"), Line("l2", "Three gears.", "s2")]
    assert targets.storyboard_rows(_short(lines, shots), {"numbers_on_screen": True})[0]["ok"] is False
    assert targets.storyboard_rows(_short([lines[0]], shots), {"numbers_on_screen": True})[0]["ok"] is True


def test_max_cuts_cuts_and_one_take():
    lines = [Line("l1", "Hi there.", "s1")]
    short = _short(lines)
    assert targets.storyboard_rows(short, {"max_cuts": 2})[0]["ok"] is True  # 3 shots = 2 cuts
    miss = targets.storyboard_rows(short, {"max_cuts": 1})[0]
    assert miss["ok"] is False and miss["text"] == "2 cuts, target at most 1"
    one = _short(lines, camera={"style": "one-take", "seconds": 10})
    assert targets.cut_count(one) == 0 and targets.storyboard_rows(one, {"max_cuts": 0})[0]["ok"] is True
    assert targets.cut_count(_short(lines, [Shot("s1", "One", "p", "sc")])) == 0


# --------------------------------------------------------------------------------------------- timeline targets


def _tl(short, durations):
    return build_timeline(short, durations)


def test_length_s_edges():
    short = _short([Line("l1", "Hi there.", "s1")], [Shot("s1", "One", "p", "sc", min_s=5.0)])
    tl = _tl(short, {"l1": 1.0})
    seconds = tl.n_frames / tl.fps
    for rng, ok_ in [([seconds, seconds + 1], True), ([seconds - 1, seconds], True), ([seconds + 0.5, 600], False),
                     ([1, seconds - 0.5], False)]:  # fmt: skip
        assert targets.timeline_rows(short, tl, {"length_s": rng})[0]["ok"] is ok_, rng


def test_payoff_by_s():
    shots = [Shot("s1", "One", "p", "sc", min_s=4.0), Shot("s2", "Two", "p", "sc", min_s=4.0, payoff=True)]
    short = _short([Line("l1", "Hi there.", "s1")], shots)
    tl = _tl(short, {"l1": 1.0})
    start = (tl.shots[1].f0 - 1) / tl.fps  # 4.0 s
    assert targets.timeline_rows(short, tl, {"payoff_by_s": start})[0]["ok"] is True
    late = targets.timeline_rows(short, tl, {"payoff_by_s": start - 0.5})[0]
    assert late["ok"] is False and f"{start:g} s" in late["text"] and "s2" in late["text"]
    shots[1].payoff = False
    none = targets.timeline_rows(short, tl, {"payoff_by_s": 10})[0]
    assert none["ok"] is False and "mark the payoff shot" in none["text"]


# ----------------------------------------------------------------------------------------------------- audio


def _voice_wav(path: Path, parts: list[tuple[float, float]]) -> None:
    """A take: (seconds, amplitude) pieces of a tone; amplitude 0 = silence."""
    pcm = []
    for secs, amp in parts:
        t = np.arange(round(secs * SR)) / SR
        pcm.append(amp * np.sin(2 * np.pi * 220 * t))
    data = (np.concatenate(pcm) * 32767).astype("<i2")
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(data.tobytes())


def test_voiced_spans_and_longest_pause():
    t = np.arange(SR * 3) / SR
    x = 0.3 * np.sin(2 * np.pi * 220 * t)
    x[int(0.9 * SR) : int(1.5 * SR)] = 0  # a 0.6 s silence
    x[: int(0.2 * SR)] = 0  # leading silence is not a pause
    x[int(2.8 * SR) :] = 0
    spans = targets.voiced_spans(x, SR)
    assert len(spans) == 2
    seconds, at = targets.longest_pause(spans)
    assert seconds == pytest.approx(0.6, abs=0.05) and at == pytest.approx(0.9, abs=0.05)
    assert targets.longest_pause(spans[:1]) is None and targets.voiced_spans(np.zeros(SR), SR) == []


def test_wpm_per_line_and_whole_short(tmp_path):
    # l1: 5 words in 1.0 s of sound = 300 wpm; l2: 4 words in 2.0 s = 120 wpm; whole: 9 words / 3 s = 180 wpm
    short = _short([Line("l1", "one two three four five", "s1"), Line("l2", "six seven eight nine", "s2")])
    _voice_wav(tmp_path / "l1.wav", [(0.3, 0), (1.0, 0.3), (0.5, 0)])
    _voice_wav(tmp_path / "l2.wav", [(2.0, 0.3), (0.4, 0)])
    rows = targets.wpm_rows(short, tmp_path, {"wpm": [170, 190]})
    assert rows[0]["ok"] is True and rows[0]["measured"] == 180
    assert rows[0]["text"] == "pace 180 wpm, target 170–190"
    assert [(r["line"], r["measured"]) for r in rows if r.get("line")] == [("l1", 300), ("l2", 120)]
    low = targets.wpm_rows(short, tmp_path, {"wpm": [181, 190]})
    assert low[0]["ok"] is False and targets.warnings_of(low[:1]) == ["pace 180 wpm, target 181–190"]
    assert targets.wpm_rows(short, tmp_path, {}) == []
    assert targets.wpm_rows(short, tmp_path / "none", {"wpm": [100, 200]})[0]["ok"] is None  # no takes: info


def test_short_lines_are_not_judged_alone(tmp_path):
    short = _short([Line("l1", "Go on", "s1")])
    _voice_wav(tmp_path / "l1.wav", [(0.2, 0.3)])  # 2 words in 0.2 s: 600 wpm, but only two words
    rows = targets.wpm_rows(short, tmp_path, {"wpm": [100, 200]})
    assert len(rows) == 1 and rows[0]["measured"] == 600 and rows[0]["ok"] is False  # the whole-short row only


def test_max_pause_across_lines_and_shots(tmp_path):
    short = _short([Line("l1", "Hello there", "s1"), Line("l2", "Goodbye now", "s2")])
    _voice_wav(tmp_path / "l1.wav", [(1.0, 0.3)])
    _voice_wav(tmp_path / "l2.wav", [(1.0, 0.3)])
    tl = _tl(short, {"l1": 1.0, "l2": 1.0})
    gap = (tl.lines[1].f0 - 1) / tl.fps - ((tl.lines[0].f0 - 1) / tl.fps + 1.0)
    assert gap > 1.0  # a shot's tail, the next shot's lead-in and its minimum length sit between the lines
    miss = targets.pause_rows(tl, tmp_path, {"max_pause_s": gap - 0.1})[0]
    assert miss["ok"] is False and miss["measured"] == pytest.approx(gap, abs=0.06)
    assert "longest pause" in miss["text"] and "target at most" in miss["text"]
    assert targets.pause_rows(tl, tmp_path, {"max_pause_s": gap + 0.1})[0]["ok"] is True
    assert targets.pause_rows(tl, tmp_path, {}) == []


def test_unreadable_voice_is_a_note_not_a_warning(tmp_path):
    short = _short([Line("l1", "Hello there", "s1")])
    tl = _tl(short, {"l1": 1.0})
    (tmp_path / "l1.wav").write_bytes(b"not a wav")
    rows = targets.pause_rows(tl, tmp_path, {"max_pause_s": 1})
    assert rows[0]["ok"] is None and targets.warnings_of(rows) == [] and targets.notes_of(rows)


def test_measure_is_empty_without_targets_and_notes_what_waits_for_narration():
    short = _short([Line("l1", "Hi there.", "s1")])
    assert targets.measure(short, {}) == []
    rows = targets.measure(short, {"wpm": [100, 200], "length_s": [5, 10], "motion_at_frame_1": True})
    assert [r["ok"] for r in rows] == [None, None, None] and targets.warnings_of(rows) == []
    assert any("after narration" in n for n in targets.notes_of(rows))


def test_write_rows_replaces_by_target_and_keeps_a_measured_motion_row(tmp_path):
    a = targets.row("max_cuts", "storyboard", 3, "at most 2", False, "3 cuts, target at most 2")
    m = targets.row("motion_at_frame_1", "final", ["Cube"], "x", True)
    targets.write_rows(tmp_path, [a, m])
    unmeasured = targets.row("motion_at_frame_1", "final", None, "x", None, "later")
    merged = targets.write_rows(tmp_path, [targets.row("max_cuts", "storyboard", 1, "at most 2", True), unmeasured])
    by = {r["target"]: r for r in merged}
    assert by["max_cuts"]["ok"] is True and by["motion_at_frame_1"]["ok"] is True
    targets.clear(tmp_path)
    assert targets.read_rows(tmp_path) == []


# --------------------------------------------------------------------------------------------------- the CLI


def run(home: Path, *args, timeout: int = 120):
    env = {**os.environ, "BSA_HOME": str(home), "PYTHONIOENCODING": "utf-8"}
    return _proc.run([sys.executable, str(CLI), *map(str, args)], check=False, capture_output=True, text=True,
                     encoding="utf-8", env=env, cwd=str(home), timeout=timeout)  # fmt: skip


def ok(proc) -> dict:
    assert proc.returncode == 0, proc.stdout + proc.stderr
    return json.loads(proc.stdout)


@pytest.fixture
def home(tmp_path):
    h = tmp_path / "home"
    h.mkdir()
    return h


@pytest.fixture
def pendulum(home, tmp_path) -> Path:
    ok(run(home, "settings", "set", "workspace", tmp_path / "ws"))
    short = Path(ok(run(home, "new-short", "Pendulum"))["short"])
    shutil.copyfile(SAMPLE, short / "source" / "short.json")
    return short


def _narrated(short: Path, seconds: float = 1.5) -> None:
    for ln in json.loads((short / "source" / "short.json").read_text(encoding="utf-8"))["lines"]:
        _voice_wav(short / "audio" / "vo" / f"{ln['id']}.wav", [(0.1, 0), (seconds, 0.3), (0.2, 0)])
    jobstate.mark_done(short, "narration", status="ok", provider="kokoro")


def test_check_targets_with_nothing_set_prints_nothing_and_writes_nothing(home, pendulum):
    out = ok(run(home, "check-targets", "pendulum"))
    assert out["rows"] == [] and out["warnings"] == [] and "no targets" in out["note"]
    assert not (pendulum / "review" / "targets.json").exists()
    validated = ok(run(home, "validate", "pendulum"))
    assert not any("target" in w.lower() for w in validated["warnings"])
    assert not (pendulum / "review" / "targets.json").exists()


def test_check_targets_rows_before_and_after_narration(home, pendulum):
    for key, value in [("max_numbers", "0"), ("max_cuts", "1"), ("wpm", "[100, 120]"), ("hook_callback", "true")]:
        ok(run(home, "settings", "set", f"targets.{key}", value))
    before = ok(run(home, "check-targets", "pendulum"))  # exit 0 on a miss
    by = {r["target"]: r for r in before["rows"]}
    assert set(by) == {"max_numbers", "max_cuts", "wpm", "hook_callback"}
    assert by["max_numbers"]["ok"] is False and by["max_numbers"]["measured"] == 2  # "Four", "twice"
    assert by["max_cuts"]["ok"] is False and by["max_cuts"]["measured"] == 2
    assert by["wpm"]["ok"] is None and by["hook_callback"]["ok"] is True  # "time", via the title
    assert len(before["warnings"]) == 2 and before["notes"]
    written = json.loads((pendulum / "review" / "targets.json").read_text(encoding="utf-8"))
    assert {r["target"] for r in written["rows"]} == set(by)
    _narrated(pendulum)
    after = ok(run(home, "check-targets", "pendulum"))
    wpm = next(r for r in after["rows"] if r["target"] == "wpm" and not r.get("line"))
    assert wpm["ok"] is not None and wpm["wanted"] == "100–120" and f"pace {wpm['measured']} wpm" in wpm["text"]
    assert all(set(r) >= {"target", "measured", "wanted", "ok"} for r in after["rows"])


def test_validate_prints_the_target_warnings(home, pendulum):
    ok(run(home, "settings", "set", "targets.max_cuts", "0"))
    ok(run(home, "settings", "set", "targets.length_s", "[1, 5]"))
    warnings = ok(run(home, "validate", "pendulum"))["warnings"]
    assert "2 cuts, target at most 0" in warnings
    assert not any("length" in w and "target" in w for w in warnings)  # length waits for the narration


def test_a_short_can_set_its_own_targets_and_a_bad_one_is_refused(home, pendulum):
    path = pendulum / "source" / "short.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["targets"] = {"max_cuts": 1}
    path.write_text(json.dumps(data), encoding="utf-8")
    assert "2 cuts, target at most 1" in ok(run(home, "check-targets", "pendulum"))["warnings"]
    data["targets"] = {"max_cuts": 500}
    path.write_text(json.dumps(data), encoding="utf-8")
    bad = run(home, "validate", "pendulum")
    assert bad.returncode != 0 and "max_cuts" in bad.stdout + bad.stderr


def test_the_pause_target_reads_the_takes_through_the_cli(home, pendulum):
    ok(run(home, "settings", "set", "targets.max_pause_s", "0.1"))
    _narrated(pendulum)
    out = ok(run(home, "check-targets", "pendulum"))
    pause = next(r for r in out["rows"] if r["target"] == "max_pause_s")
    assert pause["ok"] is False and pause["measured"] > 0.1 and "longest pause" in out["warnings"][0]


# ------------------------------------------------------------------------------------------------ the review page


def _stage(snap, name):
    return next(s for s in snap["stages"] if s["id"] == name)


def test_the_page_shows_targets_beside_their_stage_and_keeps_versions(tmp_path):
    import pagefix

    from bsa.review import snapshot

    sdir = pagefix.make_short(tmp_path, "done")
    plain = snapshot.build(sdir, settings={})
    assert all("targets" not in (s["content"] or {}) for s in plain["stages"])  # nothing set: nothing on the page
    s = {"targets": {"max_numbers": 0, "max_cuts": 1, "hook_callback": True, "wpm": [100, 120]}}
    snap = snapshot.build(sdir, settings=s)
    story = _stage(snap, "story")["content"]["targets"]
    assert {t["target"] for t in story} == {"max_numbers", "hook_callback"}
    miss = next(t for t in story if t["target"] == "max_numbers")
    assert miss["ok"] is False and miss["measured"] == "2" and miss["text"] == "2 numbers spoken, target at most 0"
    board = _stage(snap, "storyboard")["content"]["targets"]
    assert [t["target"] for t in board] == ["max_cuts"] and board[0]["ok"] is False
    assert "targets" not in _stage(snap, "narration")["content"]  # nothing measured there yet: no row, no block
    targets.write_rows(sdir, [targets.row("wpm", "narration", 150, "100–120", False, "pace 150 wpm, target 100–120")])
    snap = snapshot.build(sdir, settings=s)
    assert _stage(snap, "narration")["content"]["targets"][0]["text"] == "pace 150 wpm, target 100–120"
    for name in ("story", "storyboard", "narration"):  # a target never moves a stage's version
        assert _stage(snap, name)["version"] == _stage(plain, name)["version"], name
