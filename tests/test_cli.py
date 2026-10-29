"""The two command lines, run as Claude runs them: a subprocess, JSON on stdout, one plain line on stderr."""

import json
import math
import os
import shutil
import subprocess
import sys
import wave
from pathlib import Path

import numpy as np
import pytest

from bsa import jobstate

ROOT = Path(__file__).resolve().parent.parent
CLI = ROOT / "bsa_cli.py"
SAMPLE = ROOT / "tests" / "e2e" / "pendulum" / "source" / "short.json"


def run(home: Path, *args, cwd: Path | None = None, timeout: int = 120) -> subprocess.CompletedProcess:
    env = {**os.environ, "BSA_HOME": str(home)}
    env.pop("ELEVENLABS_API_KEY", None)
    return subprocess.run(
        [sys.executable, str(CLI), *map(str, args)],
        check=False, capture_output=True, text=True, env=env, cwd=str(cwd or home), timeout=timeout,
    )  # fmt: skip


def ok(proc: subprocess.CompletedProcess) -> dict:
    assert proc.returncode == 0, proc.stdout + proc.stderr
    return json.loads(proc.stdout)


@pytest.fixture
def home(tmp_path):
    h = tmp_path / "home"
    h.mkdir()
    return h


@pytest.fixture
def workspace(tmp_path, home):
    ws = tmp_path / "My Shorts é"
    ok(run(home, "settings", "set", "workspace", ws))
    return ws


@pytest.fixture
def pendulum(home, workspace) -> Path:
    short = Path(ok(run(home, "new-short", "Pendulum"))["short"])
    shutil.copyfile(SAMPLE, short / "source" / "short.json")
    return short


def _write_take(path: Path, seconds: float) -> None:
    t = np.arange(round(seconds * 48000)) / 48000
    pcm = (0.3 * np.sin(2 * math.pi * 220 * t) * 32767).astype("<i2")
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(48000)
        w.writeframes(pcm.tobytes())


def test_where_prints_paths(home):
    data = ok(run(home, "where"))
    assert Path(data["plugin_root"]) == ROOT
    assert Path(data["settings_path"]) == home / "settings.json"
    assert {"blender", "blender_python", "python"} <= data.keys()
    if data["blender"]:
        assert Path(data["blender"]).is_file()
        assert Path(data["blender_python"]).is_file()


def test_new_short_and_state(home, workspace):
    data = ok(run(home, "new-short", "Pendulum"))
    short = Path(data["short"])
    assert short.parent == workspace and short.name == "01-pendulum"
    assert (short / "source" / "short.json").is_file()
    assert (workspace / ".gitignore").is_file()
    state = ok(run(home, "state", "pendulum"))
    assert state["next"] == "interview"
    assert ok(run(home, "state", short))["next"] == "interview"  # a path works too


def test_new_short_with_a_number(home, workspace):
    ok(run(home, "new-short", "Pendulum"))
    data = ok(run(home, "new-short", "The Lever", "--number", "5"))
    assert Path(data["short"]).name == "05-the-lever"
    proc = run(home, "new-short", "Again", "--number", "1")
    assert proc.returncode != 0 and "01-pendulum" in proc.stderr
    assert len(proc.stderr.strip().splitlines()) == 1


def test_brand_exposure_and_look_settings(home):
    s = ok(run(home, "settings", "set", "brand.exposure", "0.3"))["settings"]
    assert s["brand"]["exposure"] == 0.3
    assert ok(run(home, "settings", "set", "brand.look", "Punchy"))["settings"]["brand"]["look"] == "Punchy"
    proc = run(home, "settings", "set", "brand.exposure", "bright")
    assert proc.returncode != 0 and "exposure" in proc.stderr


def test_new_short_needs_workspace(home):
    proc = run(home, "new-short", "Pendulum")
    assert proc.returncode != 0
    assert "workspace" in proc.stderr
    assert len(proc.stderr.strip().splitlines()) == 1


def test_state_done_and_reset(home, pendulum):
    ok(run(home, "state", "pendulum", "done", "interview"))
    ok(run(home, "state", "pendulum", "done", "story", "--info", '{"note": "approved"}'))
    state = ok(run(home, "state", "pendulum"))
    assert state["next"] == "storyboard"
    assert state["stages"]["story"]["note"] == "approved"
    state = ok(run(home, "state", "pendulum", "reset", "story"))
    assert state["next"] == "story"
    proc = run(home, "state", "pendulum", "done", "nonsense")
    assert proc.returncode != 0 and "nonsense" in proc.stderr


def test_settings_set_and_summary(home, workspace):
    ok(run(home, "settings", "set", "narrator.provider", "kokoro"))
    ok(run(home, "settings", "set", "narrator.voice", "am_michael"))
    ok(run(home, "settings", "set", "narrator.speed", "1.2"))
    ok(run(home, "settings", "set", "render.engine", "cycles"))
    ok(run(home, "settings", "set", "handle", "@myhandle"))
    shown = ok(run(home, "settings", "show"))
    assert shown["settings"]["narrator"]["speed"] == 1.2  # numbers are stored as numbers
    assert shown["settings"]["handle"] == "myhandle"  # stored without the @
    line = ok(run(home, "settings", "summary"))["summary"]
    assert "Kokoro" in line and "Cycles" in line and "@myhandle" in line and str(workspace) in line
    missing = ok(run(home, "settings", "missing"))
    assert missing["missing"] == ["brand.mode", "rules", "closing"]  # handle answered above
    assert missing["default_workspace"]


def test_settings_strings_stay_strings(home):
    ok(run(home, "settings", "set", "handle", "123"))
    ok(run(home, "settings", "set", "narrator.voice", "007"))
    ok(run(home, "settings", "set", "brand.font", "true"))
    shown = ok(run(home, "settings", "show"))["settings"]
    assert shown["handle"] == "123" and shown["narrator"]["voice"] == "007" and shown["brand"]["font"] == "true"
    ok(run(home, "settings", "set", "handle", ""))  # asked: no handle
    shown = ok(run(home, "settings", "show"))["settings"]
    assert shown["handle"] == ""
    assert "handle" not in ok(run(home, "settings", "missing"))["missing"]
    assert "no handle" in ok(run(home, "settings", "summary"))["summary"]


def test_settings_workspace_is_created_absolute(home, tmp_path):
    rel_parent = tmp_path / "cwd here"
    rel_parent.mkdir()
    ok(run(home, "settings", "set", "workspace", "shorts", cwd=rel_parent))
    ws = Path(ok(run(home, "settings", "show"))["settings"]["workspace"])
    assert ws == rel_parent / "shorts" and (ws / ".gitignore").is_file()


def test_validate_reports_errors_exit_code(home, pendulum):
    path = pendulum / "source" / "short.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["lines"][0]["text"] = "It swings 60 times a minute."
    path.write_text(json.dumps(data), encoding="utf-8")
    proc = run(home, "validate", "pendulum")
    assert proc.returncode == 1
    assert "l01" in proc.stderr
    assert len(proc.stderr.strip().splitlines()) == 1
    assert json.loads(proc.stdout)["ok"] is False


def test_validate_ok(home, pendulum):
    out = ok(run(home, "validate", "pendulum"))
    assert out["ok"] is True and out["errors"] == []
    # the only warning: its whoosh has no cached recording, so the mix would synthesise it
    assert len(out["warnings"]) == 1 and "whoosh" in out["warnings"][0] and "synthesis" in out["warnings"][0]
    assert [r["shot"] for r in out["pacing"]] == ["s01", "s02", "s03"]
    assert out["pacing_from"] == "reading pace"


def test_validate_and_timeline_report_silent_shots(home, pendulum):
    path = pendulum / "source" / "short.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["shots"][1]["min_s"] = 30  # a long hold under a short line
    data["length_s"] = 50
    path.write_text(json.dumps(data), encoding="utf-8")
    out = ok(run(home, "validate", "pendulum"))
    row = next(r for r in out["pacing"] if r["shot"] == "s02")
    assert row["too_silent"] and row["silent_s"] > 20
    assert any("s02" in w and "silent" in w and "min_s" in w for w in out["warnings"]), out["warnings"]
    tl = ok(run(home, "timeline", "pendulum"))
    assert next(r for r in tl["pacing"] if r["shot"] == "s02")["too_silent"]
    assert any("s02" in w for w in tl["warnings"])
    for lid in ("l01", "l02", "l03", "l04"):  # with takes, the pacing is measured from them
        _write_take(pendulum / "audio" / "vo" / f"{lid}.wav", 2.0)
    jobstate.mark_done(pendulum, "narration", status="ok", provider="kokoro")
    assert ok(run(home, "validate", "pendulum"))["pacing_from"] == "takes"


def test_validate_warns_when_a_caption_needs_a_say(home, pendulum):
    path = pendulum / "source" / "short.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["lines"][0]["text"] = "Length → period."
    path.write_text(json.dumps(data), encoding="utf-8")
    assert any("l01" in w and "say" in w for w in ok(run(home, "validate", "pendulum"))["warnings"])


def test_timeline_written(home, pendulum):
    data = ok(run(home, "timeline", "pendulum"))
    written = json.loads((pendulum / "source" / "timeline.json").read_text(encoding="utf-8"))
    assert written["n_frames"] == data["n_frames"] > 0
    assert [s["id"] for s in written["shots"]] == ["s01", "s02", "s03"]
    assert data["takes"] is False


def test_timeline_holds_the_end_card_only_with_a_handle(home, pendulum):
    ok(run(home, "settings", "set", "handle", ""))
    plain = ok(run(home, "timeline", "pendulum"))["n_frames"]
    ok(run(home, "settings", "set", "handle", "@me"))
    assert ok(run(home, "timeline", "pendulum"))["n_frames"] == plain + 45


def test_timeline_holds_a_short_end_card_without_a_handle(home, pendulum):
    ok(run(home, "settings", "set", "handle", ""))
    plain = ok(run(home, "timeline", "pendulum"))["n_frames"]
    path = pendulum / "source" / "short.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["end_card"] = {"text": "Play with it", "second": "example.com"}
    path.write_text(json.dumps(data), encoding="utf-8")
    assert ok(run(home, "timeline", "pendulum"))["n_frames"] == plain + 45


def test_timeline_uses_takes_after_narration(home, pendulum):
    before = ok(run(home, "timeline", "pendulum"))["n_frames"]
    for lid in ("l01", "l02", "l03", "l04"):
        _write_take(pendulum / "audio" / "vo" / f"{lid}.wav", 4.0)
    jobstate.mark_done(pendulum, "narration", status="ok", provider="kokoro")
    data = ok(run(home, "timeline", "pendulum"))
    assert data["takes"] is True and data["n_frames"] > before


def test_runs_from_other_cwd(home, tmp_path):
    elsewhere = tmp_path / "a folder with spaces"
    elsewhere.mkdir()
    assert Path(ok(run(home, "where", cwd=elsewhere))["plugin_root"]) == ROOT
    ok(run(home, "settings", "set", "workspace", tmp_path / "ws", cwd=elsewhere))
    short = Path(ok(run(home, "new-short", "Spaces test", cwd=elsewhere))["short"])
    assert short.is_dir()


def test_interview_needs_topic_audience_and_length(home, workspace):
    short = Path(ok(run(home, "new-short", "Pendulum"))["short"])
    proc = run(home, "state", "pendulum", "done", "interview")
    assert proc.returncode == 1 and "topic" in proc.stderr and "audience" in proc.stderr
    assert "Traceback" not in proc.stderr and len(proc.stderr.strip().splitlines()) == 1
    spec = json.loads(SAMPLE.read_text(encoding="utf-8"))
    for key, bad in (("length_s", 10), ("length_s", 120), ("title", "")):
        (short / "source" / "short.json").write_text(json.dumps({**spec, key: bad}), encoding="utf-8")
        proc = run(home, "state", "pendulum", "done", "interview")
        assert proc.returncode == 1 and (key if key == "title" else "length") in proc.stderr, (key, proc.stderr)
    assert "interview" not in ok(run(home, "state", "pendulum"))["stages"]
    (short / "source" / "short.json").write_text(json.dumps(spec), encoding="utf-8")
    ok(run(home, "state", "pendulum", "done", "interview"))


def test_story_needs_a_valid_spec(home, pendulum):
    ok(run(home, "state", "pendulum", "done", "interview"))
    spec = json.loads(SAMPLE.read_text(encoding="utf-8"))
    spec["lines"][0]["shot"] = "nope"
    (pendulum / "source" / "short.json").write_text(json.dumps(spec), encoding="utf-8")
    proc = run(home, "state", "pendulum", "done", "story")
    assert proc.returncode == 1 and "unknown shot" in proc.stderr and "validate" in proc.stderr
    assert "story" not in ok(run(home, "state", "pendulum"))["stages"]


def _approve_storyboard(home, short="pendulum"):
    for stage in ("interview", "story", "storyboard"):
        ok(run(home, "state", short, "done", stage))


def test_narrate_refuses_before_the_storyboard_is_approved(home, pendulum):
    ok(run(home, "settings", "set", "narrator.provider", "elevenlabs"))
    ok(run(home, "state", "pendulum", "done", "story"))
    proc = run(home, "narrate", "pendulum")
    assert proc.returncode == 1 and "storyboard" in proc.stderr and "Traceback" not in proc.stderr
    assert "narration" not in ok(run(home, "state", "pendulum"))["stages"]
    assert not (pendulum / "source" / "timeline.json").exists()


def test_narrate_captions_only_never_blocks(home, pendulum):
    _approve_storyboard(home)
    ok(run(home, "settings", "set", "narrator.provider", "none"))
    data = ok(run(home, "narrate", "pendulum"))
    assert data["status"] == "captions-only"
    assert ok(run(home, "state", "pendulum"))["stages"]["narration"]["status"] == "captions-only"


def test_narrate_failure_falls_back(home, pendulum):
    _approve_storyboard(home)
    ok(run(home, "settings", "set", "narrator.provider", "elevenlabs"))  # no voice, no key
    data = ok(run(home, "narrate", "pendulum"))
    assert data["status"] == "captions-only" and "captions only" in data["message"]


def test_narrate_clears_takes_of_another_voice(home, pendulum):
    vo = pendulum / "audio" / "vo"
    _write_take(vo / "l01.wav", 1.0)
    (vo / "narrator.json").write_text(json.dumps({"provider": "kokoro", "voice": "af_heart", "speed": 1.0,
                                                  "lines": {"l01": "old text"}}), encoding="utf-8")  # fmt: skip
    _approve_storyboard(home)
    ok(run(home, "settings", "set", "narrator.provider", "none"))
    ok(run(home, "narrate", "pendulum"))
    assert not (vo / "l01.wav").exists()


def test_mix_writes_wav_and_runs_sound_py(home, pendulum):
    (pendulum / "source" / "sound.py").write_text(
        "import numpy as np\n"
        "def build_bed(short, timeline, short_dir):\n"
        "    n = round(timeline.n_frames / timeline.fps * 48000)\n"
        "    return 0.05 * np.sin(np.arange(n) / 48000 * 2 * np.pi * 60)\n",
        encoding="utf-8",
    )
    ok(run(home, "timeline", "pendulum"))
    data = ok(run(home, "mix", "pendulum"))
    assert data["sound_py"] is True
    with wave.open(str(pendulum / "audio" / "mix.wav"), "rb") as w:
        assert (w.getnchannels(), w.getframerate()) == (2, 48000)
        assert w.getnframes() == round(data["n_frames"] / 30 * 48000)


def test_review_story_and_storyboard(home, pendulum):
    (pendulum / "source" / "styles.json").write_text(
        json.dumps([{"name": "Dusk", "description": "warm"}, {"name": "Lab", "description": "cool"}]), encoding="utf-8"
    )
    story = Path(ok(run(home, "review", "story", "pendulum"))["page"])
    html = story.read_text(encoding="utf-8")
    assert "stills/style-2.jpg" in html
    board = Path(ok(run(home, "review", "storyboard", "pendulum"))["page"])
    assert board == pendulum / "review" / "storyboard.html" and board.is_file()


def test_review_story_shows_pending_downloads(home, pendulum):
    plan = [{"id": "brushed_steel", "name": "Brushed Steel", "kind": "textures", "source": "Poly Haven",
             "license": "CC0", "files": [], "bytes": 5_200_000}]  # fmt: skip
    (pendulum / "source" / "assets-plan.json").write_text(json.dumps(plan), encoding="utf-8")
    html = Path(ok(run(home, "review", "story", "pendulum"))["page"]).read_text(encoding="utf-8")
    assert "Brushed Steel" in html and "5.2 MB" in html and "after your yes" in html


def test_settings_rules_add_list_remove(home):
    assert ok(run(home, "settings", "rules"))["rules"] == []
    assert "rules" in ok(run(home, "settings", "missing"))["missing"]
    r1 = ok(run(home, "settings", "rule", "add", "No people, no hands", "--kind", "no_humans"))["rule"]
    assert r1 == {"id": "r1", "text": "No people, no hands", "kind": "no_humans"}
    broad = ok(run(home, "settings", "rule", "add", "Keep it family friendly"))["rule"]
    part = ok(run(home, "settings", "rule", "add", "No women shown uncovered", "--part-of", broad["id"]))["rule"]
    assert part["part_of"] == "r2"
    listed = ok(run(home, "settings", "rules"))
    assert [r["id"] for r in listed["rules"]] == ["r1", "r2", "r3"] and "no_humans" in listed["kinds"]
    assert "rules" not in ok(run(home, "settings", "missing"))["missing"]
    shown = ok(run(home, "settings", "show"))
    assert [r["text"] for r in shown["rules"]] == ["No people, no hands", "Keep it family friendly",
                                                   "No women shown uncovered"]  # fmt: skip
    assert ok(run(home, "settings", "summary"))["summary"].endswith("3 hard rules")
    ok(run(home, "settings", "rule", "remove", "r1"))
    assert [r["id"] for r in ok(run(home, "settings", "rules"))["rules"]] == ["r2", "r3"]
    for bad in (("rule", "add", "x", "--kind", "no_robots"), ("rule", "remove", "r9"), ("rule", "add", " "),
                ("set", "rules", "[]")):  # fmt: skip
        proc = run(home, "settings", *bad)
        assert proc.returncode == 1 and len(proc.stderr.strip().splitlines()) == 1 and "Traceback" not in proc.stderr


def test_settings_rule_none_records_the_answer(home):
    ok(run(home, "settings", "rule", "none"))
    assert "rules" not in ok(run(home, "settings", "missing"))["missing"]


def _with_rules_check(short: Path, check: dict) -> None:
    path = short / "source" / "short.json"
    spec = json.loads(path.read_text(encoding="utf-8"))
    spec["rules_check"] = check
    path.write_text(json.dumps(spec), encoding="utf-8")


def test_validate_and_story_need_every_rule_answered(home, pendulum):
    ok(run(home, "settings", "rule", "add", "No people", "--kind", "no_humans"))
    ok(run(home, "state", "pendulum", "done", "interview"))
    proc = run(home, "validate", "pendulum")
    assert proc.returncode == 1 and "r1" in proc.stderr and "rules_check" in proc.stderr
    proc = run(home, "state", "pendulum", "done", "story")
    assert proc.returncode == 1 and "rules_check" in proc.stderr
    _with_rules_check(pendulum, {"r1": "Only the pendulum and the clock"})
    out = ok(run(home, "validate", "pendulum"))
    assert not any("hard rule" in w for w in out["warnings"])
    ok(run(home, "state", "pendulum", "done", "story"))


def test_review_pages_show_the_rules_and_how_they_were_kept(home, pendulum):
    ok(run(home, "settings", "rule", "add", "No people", "--kind", "no_humans"))
    ok(run(home, "settings", "rule", "add", "Keep it <modest>"))
    _with_rules_check(pendulum, {"r1": {"kept": "Clock parts only", "cleared": {"hand": "the clock's hand"}}})
    for page in ("story", "storyboard", "final"):
        html = Path(ok(run(home, "review", page, "pendulum"))["page"]).read_text(encoding="utf-8")
        assert "Your hard rules" in html, page
        assert "No people" in html and "Keep it &lt;modest&gt;" in html and "Clock parts only" in html, page
        assert "Not answered yet" in html, page  # r2 has no note
        assert "the clock&#x27;s hand" in html, page


def test_mix_follows_a_natural_sounds_rule(home, pendulum):
    ok(run(home, "settings", "rule", "add", "Only real recorded sounds", "--kind", "natural_sounds_only"))
    ok(run(home, "timeline", "pendulum"))
    data = ok(run(home, "mix", "pendulum"))
    assert data["sound_policy"] == "natural-only"


def test_review_final_reads_findings(home, pendulum):
    (pendulum / "review" / "checks.json").write_text(json.dumps({"findings": [
        {"check": "faceted", "severity": "warning", "obj": "Bob", "frame": 3, "message": "looks faceted"}]}),
        encoding="utf-8")  # fmt: skip
    page = Path(ok(run(home, "review", "final", "pendulum"))["page"])
    assert "looks faceted" in page.read_text(encoding="utf-8")


def test_voice_list_and_plan(home):
    models = ok(run(home, "voice", "list"))["models"]
    assert {"kokoro", "chatterbox", "chatterbox-turbo"} <= {m["model"] for m in models}
    voices = ok(run(home, "voice", "list", "kokoro"))["voices"]
    assert any(v["voice"] == "am_michael" for v in voices)
    plan = ok(run(home, "voice", "plan", "kokoro"))
    assert plan["items"][0]["model"] == "kokoro" and plan["total_bytes"] > 0 and "Apache" in plan["table"]


def test_unknown_short_is_one_plain_line(home, workspace):
    proc = run(home, "state", "nope")
    assert proc.returncode == 1
    assert "nope" in proc.stderr and "Traceback" not in proc.stderr


def test_narrate_retime_failure_never_blocks(home, pendulum, monkeypatch, capsys):
    """Narration says ok but the takes can't be read: fall back to reading-time timing, exit 0, say it once."""
    import bsa_cli
    from bsa.narrate import run as nrun

    def fake_narrate(short_dir, short, settings):
        jobstate.mark_done(short_dir, "narration", status="ok", provider="kokoro")
        (Path(short_dir) / "audio" / "vo" / "durations.json").write_text("{not json", encoding="utf-8")
        return nrun.NarrationResult("ok", {})

    monkeypatch.setattr(nrun, "narrate", fake_narrate)
    monkeypatch.setenv("BSA_HOME", str(home))
    _approve_storyboard(home)
    assert bsa_cli.main(["narrate", "pendulum"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["status"] == "captions-only" and "captions only" in out["message"]
    assert out["timeline"]["takes"] is False
    assert jobstate.load_state(pendulum)["stages"]["narration"]["status"] == "captions-only"


def test_settings_set_rejects_unknown_choices(home):
    proc = run(home, "settings", "set", "render.engine", "blender")
    assert proc.returncode == 1 and "cycles" in proc.stderr and "eevee" in proc.stderr, proc.stderr
    assert ok(run(home, "settings", "set", "narrator.provider", "Kokoro"))["settings"]["narrator"]["provider"] == "kokoro"


def test_review_final_reads_what_check_and_frame_check_write_now(home, pendulum, monkeypatch):
    """Issue #13: review final reads review/checks.json and the frame-check JSON as the current check and
    frame-check write them (shot, scene, shots, redo, redo_frames, a flicker's box...) and keys added later, and
    shows the shot and the redo command."""
    import types

    import bsa_blender
    from bsa.image_checks import Finding, FlickerFinding

    tl = _rendered(home, pendulum, "final")
    monkeypatch.setenv("BSA_HOME", str(home))
    short = bsa_blender._load(pendulum)
    timeline, _ = bsa_blender._timeline(pendulum, short)
    s1, s2 = timeline.shots[0], timeline.shots[1]
    rows = [bsa_blender._check_row(Finding("faceted", "warning", "Weight", s1.f0, "looks faceted"), s1.id, "Lever"),
            bsa_blender._check_row(Finding("names", "warning", "Cube.001", None, "renamed"), s1.id, "Lever"),
            bsa_blender._check_row(Finding("names", "warning", "Cube.001", None, "renamed"), s2.id, "Lever")]
    rows[0]["added_later"] = {"x": 1}  # a key a newer check writes: ignored
    bsa_blender._write_findings(pendulum / "review" / "checks.json", bsa_blender._merge_findings(rows), {"x": 1})

    def frame_checks(frames, load=None, progress=None, **_kw):  # moving=, held=
        n = int(frames[0].stem)
        return [FlickerFinding("flicker", "warning", None, n + 1, "the frame flickers", box=(0.1, 0.2, 0.3, 0.4)),
                Finding("stuck_frame", "warning", None, n + 2, "frame identical to the previous one")]

    class Run:
        def progress(self, *a, **kw):
            pass

    args = types.SimpleNamespace(preset="final")
    bsa_blender._cmd_frame_check(args, pendulum, short, Run(), frame_checks, None)
    data = json.loads((pendulum / "review" / "frame-checks-final.json").read_text(encoding="utf-8"))
    assert {"shot", "box", "redo", "redo_frames"} <= set(data["findings"][0])
    assert tl["n_frames"]
    html = Path(ok(run(home, "review", "final", "pendulum"))["page"]).read_text(encoding="utf-8")
    assert "looks faceted" in html and "the frame flickers" in html
    assert f"shot {s1.id}" in html and f"shots {s1.id}, {s2.id}" in html and "scene Lever" in html
    assert "BLENDER render" in html and "--redo-flagged" in html  # each redo command, and the one for all


def test_review_final_without_checks_says_not_run(home, pendulum):
    html = Path(ok(run(home, "review", "final", "pendulum"))["page"]).read_text(encoding="utf-8")
    assert "All checks passed" not in html and "checks not run" in html.lower()


@pytest.mark.parametrize(("argv", "preset", "kind"), [
    (["render", "--preset", "final"], "final", "render"), (["frame-check", "--preset", "final"], "final", "frame-check"),
    (["edit", "--preset", "preview"], "preview", "edit"), (["check"], "preview", "check"),
    (["preview"], "preview", "preview")])  # fmt: skip
def test_blender_commands_say_they_are_starting_before_anything_else(home, pendulum, monkeypatch, argv, preset, kind):
    """Right after a render or chain is started, render-status must not show the last run as current: each
    BLENDER command that records a status writes "starting" (this process, its kind) before its own work."""
    import bsa_blender
    from bsa import render_status as rs

    monkeypatch.setenv("BSA_HOME", str(home))
    path = rs.status_path(pendulum, preset)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"version": 2, "state": "done", "kind": "edit", "preset": preset, "started": 1.0,
                                "finished": 2.0, "run": {"kind": "edit", "current": None, "steps": []}}),
                    encoding="utf-8")  # fmt: skip
    seen = {}

    def cmd(args):
        seen.update(rs.read_status(path))
        raise bsa_blender.CliError("stopped early for the test")

    name = {"frame-check": "cmd_frame_check"}.get(kind, f"cmd_{kind}")
    monkeypatch.setattr(bsa_blender, name, cmd)
    assert bsa_blender.main([argv[0], str(pendulum), *argv[1:]]) == 1
    assert seen["phase"] == "starting" and seen["kind"] == kind and seen["pid"] == os.getpid()
    assert seen["previous"]["kind"] == "edit"
    after = rs.read_status(path)
    assert after["state"] == "failed" and "stopped early" in after["error"]
    assert "failed" in rs.describe(after)["summary"]


def test_frame_check_keeps_the_findings_of_shots_whose_frames_did_not_change(home, pendulum, monkeypatch):
    """A second frame-check reads again only the shots with a frame rewritten since (size or mtime): the others'
    findings come from review/frame-check-cache-<preset>.json."""
    import os
    import types

    import bsa_blender
    from bsa.image_checks import Finding

    _rendered(home, pendulum, "final")
    monkeypatch.setenv("BSA_HOME", str(home))
    short = bsa_blender._load(pendulum)
    checked = []

    def frame_checks(frames, load=None, progress=None, workers=1, **_kw):
        checked.append(frames[0].parent.name)
        if progress:
            for i in range(len(frames)):
                progress(i + 1, len(frames))
        return [Finding("fireflies", "warning", None, int(frames[1].stem), "30 firefly pixels")]

    class Run:
        def __init__(self):
            self.calls = []

        def progress(self, *a, **kw):
            self.calls.append((a, kw))

    args = types.SimpleNamespace(preset="final")
    first = bsa_blender._cmd_frame_check(args, pendulum, short, Run(), frame_checks, None)
    ids = [s.id for s in short.shots]
    assert checked == ids
    checked.clear()
    run2 = Run()
    again = bsa_blender._cmd_frame_check(args, pendulum, short, run2, frame_checks, None)
    assert checked == [] and again["warnings"] == first["warnings"] == len(ids)
    assert run2.calls[-1][0][0] == run2.calls[-1][0][1]  # all frames counted done
    one = sorted((pendulum / "frames" / "final" / ids[1]).glob("*.png"))[3]
    one.write_bytes(b"png, rendered again")
    again = bsa_blender._cmd_frame_check(args, pendulum, short, Run(), frame_checks, None)
    assert checked == [ids[1]] and again["warnings"] == len(ids)
    checked.clear()
    st = one.stat()
    os.utime(one, ns=(st.st_atime_ns, st.st_mtime_ns + 10**9))  # same size, newer file
    bsa_blender._cmd_frame_check(args, pendulum, short, Run(), frame_checks, None)
    assert checked == [ids[1]]


def test_assets_download_goes_where_build_py_looks(home, pendulum, tmp_path, monkeypatch, capsys):
    """The cache is the short's own workspace's, even if the workspace setting moved since."""
    import bsa_cli
    from bsa.assets import polyhaven

    ok(run(home, "settings", "set", "workspace", tmp_path / "elsewhere"))
    plan = [{"id": "a1", "name": "A", "kind": "textures", "source": "Poly Haven", "license": "CC0", "files": [],
             "bytes": 0}]  # fmt: skip
    (pendulum / "source" / "assets-plan.json").write_text(json.dumps(plan), encoding="utf-8")
    seen = {}
    monkeypatch.setattr(polyhaven, "download", lambda plans, cache_dir: seen.setdefault("cache", cache_dir) and {})
    monkeypatch.setenv("BSA_HOME", str(home))
    assert bsa_cli.main(["assets", "download", str(pendulum)]) == 0, capsys.readouterr().err
    assert Path(seen["cache"]) == pendulum.parent / ".assets-cache" == polyhaven.cache_dir(pendulum)


def test_assets_download_uses_the_workspace_cache(home, pendulum, monkeypatch, capsys):
    import bsa_cli
    from bsa.assets import polyhaven

    plan = [{"id": "a1", "name": "A", "kind": "textures", "source": "Poly Haven", "license": "CC0", "files": [],
             "bytes": 0}]  # fmt: skip
    (pendulum / "source" / "assets-plan.json").write_text(json.dumps(plan), encoding="utf-8")
    seen = {}
    monkeypatch.setattr(polyhaven, "download", lambda plans, cache_dir: seen.setdefault("cache", cache_dir) and {})
    monkeypatch.setenv("BSA_HOME", str(home))
    assert bsa_cli.main(["assets", "download", str(pendulum)]) == 0, capsys.readouterr().err
    ws = json.loads((home / "settings.json").read_text(encoding="utf-8"))["workspace"]
    assert Path(seen["cache"]) == Path(ws) / ".assets-cache"
    assert [a.id for a in __import__("bsa.short", fromlist=["load_short"]).load_short(
        pendulum / "source" / "short.json").assets] == ["a1"]  # fmt: skip


def test_mix_refuses_when_timing_changed(home, pendulum):
    ok(run(home, "timeline", "pendulum"))
    spec = json.loads((pendulum / "source" / "short.json").read_text(encoding="utf-8"))
    spec["lines"][0]["text"] += " and then a good many more words that make this line run much longer"
    (pendulum / "source" / "short.json").write_text(json.dumps(spec), encoding="utf-8")
    proc = run(home, "mix", "pendulum")
    assert proc.returncode == 1 and "timing changed: run CLI timeline" in proc.stderr, proc.stderr
    assert not (pendulum / "audio" / "mix.wav").exists()


def test_state_done_build_resets_later_stages(home, pendulum):
    for stage in ("narration", "build", "checks", "preview", "final"):
        ok(run(home, "state", "pendulum", "done", stage))
    stages = ok(run(home, "state", "pendulum", "done", "build"))["stages"]
    assert "build" in stages and "narration" in stages
    assert not {"checks", "preview", "final"} & stages.keys()


# --------------------------------------------------------------------------- bsa_blender.py


@pytest.mark.parametrize("shift", [0, 3])
def test_edit_refuses_frames_from_an_old_timing(home, pendulum, monkeypatch, capsys, shift):
    import bsa_blender

    _rendered(home, pendulum, "preview", shift=shift)
    seen = {}
    _fake_edit(monkeypatch, seen, probe=lambda mp4: {"width": 1080, "height": 1920, "fps": 30.0, "frames": 0})
    monkeypatch.setenv("BSA_HOME", str(home))
    if shift == 0:  # the frames are current, but one is missing: never padded with the last frame
        max((pendulum / "frames" / "preview" / "s02").glob("*.png")).unlink()
    assert bsa_blender.main(["edit", "pendulum", "--preset", "preview"]) == 1
    err = capsys.readouterr().err
    assert ("older timing" if shift else "missing preview frames") in err, err
    assert "built" not in seen


@pytest.mark.parametrize(("fps", "audio", "mix", "bad"), [(25.0, True, True, "fps"), (30.0, False, True, "has_audio"),
                                                       (30.0, False, False, None), (30.0, True, True, None)])
def test_edit_checks_fps_and_audio(home, pendulum, monkeypatch, capsys, fps, audio, mix, bad):
    import bsa_blender

    tl = _rendered(home, pendulum, "preview")
    if mix:
        (pendulum / "audio" / "mix.wav").write_bytes(b"wav")
    probe = {"width": 1080, "height": 1920, "fps": fps, "frames": tl["n_frames"], "has_audio": audio}
    _fake_edit(monkeypatch, {}, probe=lambda mp4: probe)
    monkeypatch.setenv("BSA_HOME", str(home))
    code = bsa_blender.main(["edit", "pendulum", "--preset", "preview"])
    err = capsys.readouterr().err
    assert code == (1 if bad else 0), err
    if bad:
        assert bad in err


def test_a_standalone_edit_records_itself_as_a_one_step_run(home, pendulum, monkeypatch):
    """BLENDER edit on its own shows on the preview status page: a one-step run with the encode's progress, which
    supersedes the run shown there before (kept as the previous run)."""
    import bsa_blender
    from bsa import render_status as rs

    tl = _rendered(home, pendulum, "preview")
    status = rs.status_path(pendulum, "preview")
    status.parent.mkdir(parents=True, exist_ok=True)
    status.write_text(json.dumps({"state": "done", "preset": "preview", "pid": 1, "started": 1.0, "finished": 2.0,
                                  "updated": 2.0, "frames": {"total": 9, "done": 9}}), encoding="utf-8")  # fmt: skip
    probe = {"width": 1080, "height": 1920, "fps": 30.0, "frames": tl["n_frames"], "has_audio": False}
    _fake_edit(monkeypatch, {}, probe=lambda mp4: probe)
    monkeypatch.setenv("BSA_HOME", str(home))
    assert bsa_blender.main(["edit", "pendulum", "--preset", "preview"]) == 0
    s = json.loads(status.read_text(encoding="utf-8"))
    assert s["kind"] == "edit" and s["state"] == "done" and s["previous"]["kind"] == "render"
    (step,) = s["run"]["steps"]
    assert step["name"] == "edit" and step["state"] == "done" and step["summary"]["mp4"]
    assert step["progress"]["done"] == step["progress"]["total"] == 3
    assert "Done" in rs.page_path(pendulum, "preview").read_text(encoding="utf-8")


def test_a_failed_check_is_recorded_on_the_status_page(home, pendulum, monkeypatch):
    """A standalone step that fails ends its run failed, with the error, and the CLI says how it ended."""
    import bsa_blender
    from bsa import render_status as rs

    monkeypatch.setenv("BSA_HOME", str(home))
    assert bsa_blender.main(["check", "pendulum", "--preset", "final"]) == 1  # no timeline yet: stale
    s = json.loads(rs.status_path(pendulum, "final").read_text(encoding="utf-8"))
    assert s["kind"] == "check" and s["state"] == "failed" and s["error"]
    assert s["run"]["steps"][0]["state"] == "failed"
    out = ok(run(home, "render-status", "pendulum", "--preset", "final"))
    assert out["idle"] is True and out["last_run"]["state"] == "failed" and "check failed" in out["summary"]


def test_edit_reports_a_font_fallback(home, pendulum, monkeypatch, capsys):
    import bsa_blender

    tl = _rendered(home, pendulum, "preview")
    seen = {"font_note": "font 'Brand Sans' was not found, so the text uses Blender's default font"}
    probe = {"width": 1080, "height": 1920, "fps": 30.0, "frames": tl["n_frames"], "has_audio": False}
    _fake_edit(monkeypatch, seen, probe=lambda mp4: probe)
    monkeypatch.setenv("BSA_HOME", str(home))
    assert bsa_blender.main(["edit", "pendulum", "--preset", "preview"]) == 0, capsys.readouterr().err
    out = json.loads(capsys.readouterr().out.split("BSA_RESULT ", 1)[1])
    assert out["font_fallback"] == seen["font_note"] and out["font"] == "Segoe UI"


def test_edit_refuses_when_the_timeline_is_stale(home, pendulum, monkeypatch, capsys):
    import bsa_blender

    _rendered(home, pendulum, "preview")
    spec = json.loads((pendulum / "source" / "short.json").read_text(encoding="utf-8"))
    spec["lines"][0]["text"] += " and then a good many more words that make this line run much longer"
    (pendulum / "source" / "short.json").write_text(json.dumps(spec), encoding="utf-8")
    seen = {}
    _fake_edit(monkeypatch, seen)
    monkeypatch.setenv("BSA_HOME", str(home))
    assert bsa_blender.main(["edit", "pendulum", "--preset", "preview"]) == 1
    assert "timing changed" in capsys.readouterr().err
    assert "built" not in seen


def test_edit_final_needs_current_checks(home, pendulum, monkeypatch, capsys):
    import bsa_blender
    from bsa import freshness

    tl = _rendered(home, pendulum, "final")
    seen = {}
    _fake_edit(monkeypatch, seen)
    monkeypatch.setenv("BSA_HOME", str(home))
    assert bsa_blender.main(["edit", "pendulum", "--preset", "final"]) == 1
    assert "checks have not run" in capsys.readouterr().err and "built" not in seen
    short = bsa_blender._load(pendulum)
    timeline, tl_hash = bsa_blender._timeline(pendulum, short)
    stamp = freshness.checks_stamp(tl_hash, bsa_blender._check_keys(short, timeline, None))
    report = {"stamp": stamp, "errors": 1, "warnings": 0, "findings": []}
    (pendulum / "review" / "checks.json").write_text(json.dumps(report), encoding="utf-8")
    assert bsa_blender.main(["edit", "pendulum", "--preset", "final"]) == 1
    assert "1 error" in capsys.readouterr().err and "built" not in seen
    report["errors"] = 0
    (pendulum / "review" / "checks.json").write_text(json.dumps(report), encoding="utf-8")
    bsa_blender.main(["edit", "pendulum", "--preset", "final"])
    assert seen.get("built") and tl["n_frames"] > 0



def _checked(short: Path, errors: int = 0) -> None:
    """review/checks.json as `BLENDER check` leaves it for the short as it is now."""
    import bsa_blender
    from bsa import freshness

    s = bsa_blender._load(short)
    timeline, tl_hash = bsa_blender._timeline(short, s)
    fps = bsa_blender._fingerprints(short, s, timeline, tl_hash)
    keys = bsa_blender._check_keys(s, timeline, fps, bsa_blender._parts(short, fps))
    report = {"stamp": freshness.checks_stamp(tl_hash, keys, 5), "errors": errors, "warnings": 0, "findings": []}
    (short / "review" / "checks.json").write_text(json.dumps(report), encoding="utf-8")


def test_post_text_sound_levels_settings_and_mix_never_invalidate_the_final(home, pendulum, monkeypatch, capsys):
    """Issue #12: after the final render and its checks, writing the post text, changing sound levels, rewording a
    shot's title or picture, changing the end-card text and running CLI mix all leave the final edit current."""
    import bsa_blender

    blend = _stale_build(pendulum, newer=False)
    tl = _rendered(home, pendulum, "final", blend=blend)
    monkeypatch.setenv("BSA_HOME", str(home))
    _checked(pendulum)
    probe = {"width": 1080, "height": 1920, "fps": 30.0, "frames": tl["n_frames"], "has_audio": True}
    seen = {}
    _fake_edit(monkeypatch, seen, probe=lambda mp4: probe)
    assert bsa_blender.main(["edit", "pendulum", "--preset", "final"]) == 0, capsys.readouterr().err
    spec_path = pendulum / "source" / "short.json"
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    spec["post"] = {"title": "Why does a pendulum keep time?", "description": "Sources: x. Made in Blender.",
                    "hashtags": ["#physics", "#shorts"]}  # fmt: skip
    for sfx in spec.get("sfx", []):
        sfx["level_db"] -= 4
    spec["shots"][0]["title"] += " (reworded)"
    spec["shots"][0]["picture"] += " Reworded."
    spec_path.write_text(json.dumps(spec, indent=2), encoding="utf-8")
    ok(run(home, "settings", "set", "end_card", "Follow for more physics"))
    ok(run(home, "mix", "pendulum"))
    seen.clear()
    code = bsa_blender.main(["edit", "pendulum", "--preset", "final"])
    assert code == 0, capsys.readouterr().err
    assert seen.get("built")


def test_a_shot_entry_change_is_named_in_the_refusal(home, pendulum, monkeypatch, capsys):
    import bsa_blender

    blend = _stale_build(pendulum, newer=False)
    _rendered(home, pendulum, "final", blend=blend)
    monkeypatch.setenv("BSA_HOME", str(home))
    _checked(pendulum)
    spec_path = pendulum / "source" / "short.json"
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    spec["shots"][1]["labels"] = [*spec["shots"][1].get("labels", []), "A new label"]
    spec_path.write_text(json.dumps(spec, indent=2), encoding="utf-8")
    _fake_edit(monkeypatch, {})
    assert bsa_blender.main(["edit", "pendulum", "--preset", "final"]) == 1
    io = capsys.readouterr()
    sid = spec["shots"][1]["id"]
    assert f"short.json shot {sid} entry changed (fields: labels)" in io.err
    assert "before the final edit" in io.err


def test_a_failed_blender_command_still_prints_its_result(home, pendulum, monkeypatch, capsys):
    """Every BLENDER command prints a BSA_RESULT line, failures too (ok false and the error), so a chain that reads
    only those lines never ends silently."""
    import bsa_blender

    monkeypatch.setenv("BSA_HOME", str(home))
    ok(run(home, "timeline", "pendulum"))
    _fake_edit(monkeypatch, {})
    assert bsa_blender.main(["edit", "pendulum", "--preset", "final"]) == 1
    io = capsys.readouterr()
    result = json.loads(io.out.split("BSA_RESULT ", 1)[1])
    assert result["ok"] is False and "no final frames" in result["error"]
    assert result["error"] in io.err
    assert bsa_blender.main(["edit"]) == 2  # a usage error
    assert json.loads(capsys.readouterr().out.split("BSA_RESULT ", 1)[1])["ok"] is False


def test_edit_needs_current_anchors_for_near_labels(home, pendulum, monkeypatch, capsys):
    import bsa_blender
    from bsa import freshness

    spec = json.loads((pendulum / "source" / "short.json").read_text(encoding="utf-8"))
    spec["shots"][0]["labels"] = [{"text": "Bob", "near": "Bob"}]
    (pendulum / "source" / "short.json").write_text(json.dumps(spec), encoding="utf-8")
    tl = _rendered(home, pendulum, "preview")
    path = pendulum / "source" / "anchors.json"
    good = path.read_text(encoding="utf-8")
    path.unlink()
    probe = {"width": 1080, "height": 1920, "fps": 30.0, "frames": tl["n_frames"], "has_audio": False}
    seen = {}
    _fake_edit(monkeypatch, seen, probe=lambda mp4: probe)
    monkeypatch.setenv("BSA_HOME", str(home))
    assert bsa_blender.main(["edit", "pendulum", "--preset", "preview"]) == 1
    assert "run BLENDER anchors SHORT" in capsys.readouterr().err and "built" not in seen
    stale = {**json.loads(good), "stamp": freshness.anchors_stamp(None, "old")}
    path.write_text(json.dumps(stale), encoding="utf-8")
    assert bsa_blender.main(["edit", "pendulum", "--preset", "preview"]) == 1
    assert "older build or timing" in capsys.readouterr().err and "built" not in seen
    current = json.loads(good)
    current["shots"]["s01"].pop("Bob")  # current stamp, but no entry for this label
    path.write_text(json.dumps(current), encoding="utf-8")
    assert bsa_blender.main(["edit", "pendulum", "--preset", "preview"]) == 1
    err = capsys.readouterr().err
    assert err.startswith("no anchors for label 'Bob'") and "run BLENDER anchors SHORT" in err and "built" not in seen
    path.write_text(good, encoding="utf-8")
    assert bsa_blender.main(["edit", "pendulum", "--preset", "preview"]) == 0, capsys.readouterr().err
    assert seen["anchors"] == json.loads(good)["shots"] and "Bob" in seen["anchors"]["s01"]


@pytest.mark.parametrize("preset", ["preview", "final"])
def test_render_checks_near_label_objects_before_rendering(home, pendulum, monkeypatch, capsys, preset):
    import types

    import bsa_blender
    from bsa import freshness
    from bsa.timeline import timeline_hash

    spec = json.loads((pendulum / "source" / "short.json").read_text(encoding="utf-8"))
    spec["shots"][0]["labels"] = [{"text": "Bob", "near": "Pendulum Z bob"}]
    (pendulum / "source" / "short.json").write_text(json.dumps(spec), encoding="utf-8")
    saved = ok(run(home, "timeline", "pendulum"))
    tl_hash = timeline_hash(json.loads((pendulum / "source" / "timeline.json").read_text(encoding="utf-8")))

    class Scene(dict):
        def __init__(self, name):
            super().__init__({freshness.TIMELINE_PROP: tl_hash})
            self.name, self.objects = name, {"Pendulum A bob": object()}

    scenes = {n: Scene(n) for n in {s["scene"] for s in spec["shots"]}}
    data = types.SimpleNamespace(scenes=scenes, filepath="")
    rendered = []
    fake = types.ModuleType("bsa.blender.render")
    fake.render_range = lambda *a, **k: rendered.append(a) or []
    monkeypatch.setitem(sys.modules, "bsa.blender.render", fake)
    monkeypatch.setattr(bsa_blender, "_open_blend", lambda sdir: data)
    monkeypatch.setattr(freshness, "require_checks", lambda *a: None)
    monkeypatch.setenv("BSA_HOME", str(home))
    assert saved["n_frames"] > 0
    assert bsa_blender.main(["render", "pendulum", "--preset", preset]) == 1
    err = capsys.readouterr().err
    assert err.strip() == ("shot s01 label 'Bob' is near 'Pendulum Z bob', which scene 'Workshop' does not have (use "
                           "the object's exact name)")  # fmt: skip
    assert rendered == []  # nothing rendered


def test_end_card_uses_hero_boxes_and_only_warns_without_anchors(home, pendulum, monkeypatch, capsys):
    import bsa_blender

    ok(run(home, "settings", "set", "handle", "me"))
    tl = _rendered(home, pendulum, "preview")
    path = pendulum / "source" / "anchors.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    boxes = {"f0": tl["shots"][-1]["f0"], "boxes": {"Wheel": [[400, 300, 600, 500]]}}
    data["heroes"] = {tl["shots"][-1]["id"]: boxes}
    path.write_text(json.dumps(data), encoding="utf-8")
    probe = {"width": 1080, "height": 1920, "fps": 30.0, "frames": tl["n_frames"], "has_audio": False}
    seen = {"end_card_note": "the end card covers a hero in every position; it uses the middle, which covers the least"}
    _fake_edit(monkeypatch, seen, probe=lambda mp4: probe)
    monkeypatch.setenv("BSA_HOME", str(home))
    assert bsa_blender.main(["edit", "pendulum", "--preset", "preview"]) == 0, capsys.readouterr().err
    out = json.loads(capsys.readouterr().out.split("BSA_RESULT ", 1)[1])
    assert seen["hero_boxes"] == boxes and out["warnings"] == [seen["end_card_note"]]
    for heroes in (None, {}, "junk", {tl["shots"][-1]["id"]: "junk"}):  # stamped, but no usable hero positions
        if heroes is None:
            data.pop("heroes", None)
        else:
            data["heroes"] = heroes
        path.write_text(json.dumps(data), encoding="utf-8")
        seen.clear()
        assert bsa_blender.main(["edit", "pendulum", "--preset", "preview"]) == 0, capsys.readouterr().err
        out = json.loads(capsys.readouterr().out.split("BSA_RESULT ", 1)[1])
        assert seen["hero_boxes"] is None, heroes
        want = "hero positions not recorded: run BLENDER anchors SHORT (the end card stays in the upper third unchecked)"
        assert out["warnings"] == [want], heroes
    # no anchors.json: the near label in the sample needs it, so use a short without near labels
    spec = json.loads((pendulum / "source" / "short.json").read_text(encoding="utf-8"))
    for shot in spec["shots"]:
        shot["labels"] = [lb for lb in shot["labels"] if isinstance(lb, str)]
    (pendulum / "source" / "short.json").write_text(json.dumps(spec), encoding="utf-8")
    tl = _rendered(home, pendulum, "preview")
    path.unlink()
    seen.clear()
    assert bsa_blender.main(["edit", "pendulum", "--preset", "preview"]) == 0, capsys.readouterr().err
    out = json.loads(capsys.readouterr().out.split("BSA_RESULT ", 1)[1])
    assert seen["hero_boxes"] is None and seen["built"]
    assert len(out["warnings"]) == 1 and "upper third" in out["warnings"][0] and "anchors" in out["warnings"][0]


def _stale_build(short: Path, newer: bool = True) -> int:
    """A blend/short.blend and a source/build.py edited after it (or before, with newer=False); the blend's mtime."""
    blend, build = short / "blend" / "short.blend", short / "source" / "build.py"
    blend.parent.mkdir(parents=True, exist_ok=True)
    blend.write_bytes(b"blend")
    build.write_text("# build", encoding="utf-8")
    t0 = 1_700_000_000_000_000_000
    os.utime(blend, ns=(t0, t0))
    os.utime(build, ns=(t0 + (10**9 if newer else -(10**9)),) * 2)
    return blend.stat().st_mtime_ns


def test_stale_build_py_warns_and_the_final_refuses(home, pendulum, monkeypatch, capsys):
    import bsa_blender

    blend = _stale_build(pendulum)
    tl = _rendered(home, pendulum, "preview", blend=blend)
    probe = {"width": 1080, "height": 1920, "fps": 30.0, "frames": tl["n_frames"], "has_audio": False}
    seen = {}
    _fake_edit(monkeypatch, seen, probe=lambda mp4: probe)
    monkeypatch.setenv("BSA_HOME", str(home))
    assert bsa_blender.main(["edit", "pendulum", "--preset", "preview"]) == 0
    io = capsys.readouterr()
    out = json.loads(io.out.split("BSA_RESULT ", 1)[1])
    assert out["warnings"] == ["build.py changed since the scene was built: rebuild"]
    assert "build.py changed since the scene was built: rebuild" in io.err
    _rendered(home, pendulum, "final", blend=blend)
    seen.clear()
    assert bsa_blender.main(["edit", "pendulum", "--preset", "final"]) == 1
    err = capsys.readouterr().err
    assert "build.py changed since the scene was built" in err and "before the final edit" in err
    assert "built" not in seen
    assert bsa_blender.main(["render", "pendulum", "--preset", "final"]) == 1
    err = capsys.readouterr().err
    assert "build.py changed since the scene was built" in err and "before the final render" in err
    _stale_build(pendulum, newer=False)
    _rendered(home, pendulum, "preview", blend=blend)
    assert bsa_blender.main(["edit", "pendulum", "--preset", "preview"]) == 0
    io = capsys.readouterr()
    assert "warnings" not in json.loads(io.out.split("BSA_RESULT ", 1)[1]) and "build.py" not in io.err


def _rendered(home, short: Path, preset: str, engine=None, blend=None, shift=0) -> dict:
    """Write source/timeline.json and stamped frames for every shot, as `render` leaves them; returns the timeline.
    With `blend` (a blend/short.blend exists), each shot gets a fingerprint, cached as render caches it."""
    from bsa import freshness
    from bsa.timeline import Span, timeline_hash

    tl = ok(run(home, "timeline", short.name))
    tl_hash = timeline_hash(json.loads((short / "source" / "timeline.json").read_text(encoding="utf-8")))
    fps = {s["id"]: f"fp-{s['id']}" for s in tl["shots"]} if blend else None
    if fps:
        freshness.save_fingerprints(short, tl_hash, fps)
    for s in tl["shots"]:
        span = Span(s["id"], s["f0"] + shift, s["f1"] + shift)
        folder = short / "frames" / preset / s["id"]
        folder.mkdir(parents=True, exist_ok=True)
        for f in range(span.f0, span.f1 + 1):
            (folder / f"{f:04d}.png").write_bytes(b"png")
        stamp = freshness.frames_stamp(span, engine, (fps or {}).get(s["id"]), preset)
        (folder / ".render.json").write_text(json.dumps(stamp), encoding="utf-8")
    _anchors(short, fps)  # render writes them too
    return tl


def _anchors(short: Path, fps=None) -> dict:
    """source/anchors.json for the short's near labels, stamped as `render` leaves it; returns its shots."""
    from bsa import freshness
    from bsa.short import load_short, near_labels
    from bsa.timeline import timeline_hash

    saved = json.loads((short / "source" / "timeline.json").read_text(encoding="utf-8"))
    spans = {s["id"]: s for s in saved["shots"]}
    shots: dict = {}
    for shot_id, _, name in near_labels(load_short(short / "source" / "short.json")):
        n = spans[shot_id]["f1"] - spans[shot_id]["f0"] + 1
        shots.setdefault(shot_id, {})[name] = {"f0": spans[shot_id]["f0"], "points": [[500, 800]] * n}
    data = {"stamp": freshness.anchors_stamp(fps, timeline_hash(saved)), "shots": shots}
    (short / "source" / "anchors.json").write_text(json.dumps(data), encoding="utf-8")
    return shots


def _fake_edit(monkeypatch, seen, probe=None, fail_encode=False):
    import types

    def build_edit(short, tl, frames_root, mix_wav, out_mp4, settings, preset="final", anchors=None, hero_boxes=None,
                   label_heroes=None):  # fmt: skip
        seen["anchors"], seen["hero_boxes"], seen["label_heroes"] = anchors, hero_boxes, label_heroes
        seen["existed_at_build"] = Path(out_mp4).exists()
        seen["built"] = True
        return object()

    def encode(scene, progress=None):
        if fail_encode:
            raise RuntimeError("encode failed")
        for i in range(1, 4):
            if progress is not None:
                progress(i, 3)
        return Path("x.mp4")

    fake = types.ModuleType("bsa.blender.edit")
    fake.build_edit, fake.encode, fake.probe = build_edit, encode, (probe or (lambda mp4: {}))
    fake.font_note = lambda scene: seen.get("font_note")
    fake.font_used = lambda scene: seen.get("font_used", "Segoe UI")
    fake.end_card_note = lambda scene: seen.get("end_card_note")
    fake.glyph_fallbacks = lambda scene: seen.get("glyph_fallbacks", [])
    monkeypatch.setitem(sys.modules, "bsa.blender.edit", fake)


def test_edit_deletes_stale_mp4_before_encoding(home, pendulum, monkeypatch):
    """A stale MP4 must never survive a failed re-encode (pure: the Blender edit module is stubbed)."""
    import bsa_blender

    _rendered(home, pendulum, "preview")
    stale = pendulum / "out" / "pendulum-preview.mp4"
    stale.write_bytes(b"old video")
    seen = {}
    _fake_edit(monkeypatch, seen, fail_encode=True)
    monkeypatch.setenv("BSA_HOME", str(home))
    code = bsa_blender.main(["edit", "pendulum", "--preset", "preview"])
    assert code == 1
    assert seen["existed_at_build"] is False
    assert not stale.exists()
    assert "preview" not in jobstate.load_state(pendulum)["stages"]


_TINY_SCENES = """
import bpy, sys
for name in ("Workshop", "Clock"):
    sc = bpy.data.scenes.new(name)
    if sys.argv[-1] != "-":
        sc["bsa_timeline"] = sys.argv[-1]
    sc.render.engine = "CYCLES"
    cam = bpy.data.objects.new(name + "Cam", bpy.data.cameras.new(name + "Cam"))
    cam.location = (0, -6, 1); cam.rotation_euler = (1.5708, 0, 0)
    sc.collection.objects.link(cam); sc.camera = cam
    mesh = bpy.data.meshes.new(name + "Box")
    mesh.from_pydata([(-1,-1,0),(1,-1,0),(1,1,0),(-1,1,0),(-1,-1,2),(1,-1,2),(1,1,2),(-1,1,2)], [],
                     [(0,3,2,1),(4,5,6,7),(0,1,5,4),(1,2,6,5),(2,3,7,6),(3,0,4,7)])
    box = bpy.data.objects.new(name + "Box", mesh)
    mat = bpy.data.materials.new(name + "Mat"); mat.use_nodes = True
    mesh.materials.append(mat)
    sc.collection.objects.link(box)
    light = bpy.data.objects.new(name + "Sun", bpy.data.lights.new(name + "Sun", "SUN"))
    sc.collection.objects.link(light)
bpy.ops.wm.save_as_mainfile(filepath=sys.argv[-2])
"""


@pytest.mark.blender
def test_blender_stills_and_check_smoke(home, pendulum):
    from bsa.locate import find_blender
    from bsa.timeline import timeline_hash

    blender = find_blender()
    blend = pendulum / "blend" / "short.blend"
    env = {**os.environ, "BSA_HOME": str(home)}
    base = [str(blender), "-b", "--factory-startup", "--python-exit-code", "1"]

    def make(stamp):
        made = subprocess.run(base + ["--python-expr", _TINY_SCENES, "--", str(blend), stamp],
                              check=False, capture_output=True, text=True, timeout=300)  # fmt: skip
        assert made.returncode == 0 and blend.is_file(), made.stdout + made.stderr

    def blender_cmd(*args):
        cmd = [str(blender), "-b", str(blend), "--factory-startup", "--python-exit-code", "1",
               "--python", str(ROOT / "bsa_blender.py"), "--", *map(str, args)]  # fmt: skip
        return subprocess.run(cmd, check=False, capture_output=True, text=True, timeout=900, env=env, cwd=str(home))

    ok(run(home, "timeline", "pendulum"))
    make("-")  # a .blend whose scenes carry no timeline stamp
    unstamped = blender_cmd("stills", "pendulum")
    assert unstamped.returncode == 1 and "no timeline stamp" in unstamped.stderr, unstamped.stderr
    make("0123456789abcdef")  # built for another timing
    stale = blender_cmd("stills", "pendulum")
    assert stale.returncode == 1 and "built for another timing" in stale.stderr, stale.stderr
    make(timeline_hash(json.loads((pendulum / "source" / "timeline.json").read_text(encoding="utf-8"))))
    final = blender_cmd("render", "pendulum", "--preset", "final")
    assert final.returncode == 1 and "checks have not run" in final.stderr, final.stderr

    stills = blender_cmd("stills", "pendulum")
    assert stills.returncode == 0, stills.stdout + stills.stderr
    for sid in ("s01", "s02", "s03"):
        assert (pendulum / "stills" / f"{sid}.jpg").stat().st_size > 0
    one = blender_cmd("stills", "pendulum", "--shot", "s01", "--out", pendulum / "stills" / "style-1.jpg")
    assert one.returncode == 0 and (pendulum / "stills" / "style-1.jpg").is_file(), one.stdout + one.stderr

    still = blender_cmd("extremes", "pendulum", "--shot", "s01")  # nothing is tagged hero in the tiny scenes
    assert still.returncode == 0, still.stdout + still.stderr
    result = json.loads(still.stdout.split("BSA_RESULT ", 1)[1].splitlines()[0])
    assert result["stills"] == 0 and "no hero moves" in result["warnings"][0]

    check = blender_cmd("check", "pendulum")
    report = json.loads((pendulum / "review" / "checks.json").read_text(encoding="utf-8"))
    errors = [f for f in report["findings"] if f["severity"] == "error"]
    assert (check.returncode == 0) == (not errors), check.stdout + check.stderr
    assert ("checks" in jobstate.load_state(pendulum)["stages"]) == (not errors)


FAKE_TTS = """
import json, struct, sys, wave
a = sys.argv
lines = json.load(open(a[a.index("--lines") + 1], encoding="utf-8"))
out = a[a.index("--out") + 1]
for item in lines:
    with wave.open(out + "/" + item["id"] + ".wav", "wb") as w:
        w.setnchannels(2); w.setsampwidth(2); w.setframerate(24000)
        w.writeframes(b"".join(struct.pack("<hh", 6000 if i % 2 else -6000, 6000 if i % 2 else -6000) for i in range(24000)))
"""


def test_voice_test_custom_renders_one_line(home, tmp_path):
    tool = tmp_path / "my tools" / "fake tts.py"
    tool.parent.mkdir()
    tool.write_text(FAKE_TTS, encoding="utf-8")
    ok(run(home, "settings", "set", "narrator.provider", "custom"))
    ok(run(home, "settings", "set", "narrator.command", f'"{sys.executable}" "{tool}" --lines {{lines}} --out {{out}}'))
    data = ok(run(home, "voice", "test-custom"))
    assert abs(data["seconds"] - 1.0) < 1 / 30
    with wave.open(data["path"], "rb") as w:
        assert (w.getframerate(), w.getnchannels(), w.getsampwidth()) == (48000, 1, 2)
    assert ok(run(home, "settings", "summary"))["summary"].startswith("your voice tool (")


def test_voice_test_custom_needs_a_command(home):
    proc = run(home, "voice", "test-custom")
    assert proc.returncode == 1 and "narrator.command" in proc.stderr and "Traceback" not in proc.stderr


def test_voice_test_custom_failure_is_one_plain_line(home):
    ok(run(home, "settings", "set", "narrator.command", "no-such-voice-tool-xyz --out {out}"))
    proc = run(home, "voice", "test-custom")
    assert proc.returncode == 1 and "Traceback" not in proc.stderr and len(proc.stderr.strip().splitlines()) == 1


# --------------------------------------------------------------------------- BLENDER preview (the rebuild chain)


def _past_narration(home, short: Path) -> None:
    for stage in jobstate.STAGES[: jobstate.STAGES.index("build")]:
        jobstate.mark_done(short, stage)
    ok(run(home, "timeline", short.name))


def _fake_chain(monkeypatch, home, short: Path, calls: list, fail: str | None = None):
    """Stub the Blender-side steps of BLENDER preview; the CLI steps (timeline, state, mix) run for real."""
    import bsa_blender

    def step(name, out=None):
        def fn(*a, **k):
            calls.append(name)
            if name == fail:
                raise bsa_blender.CliError("2 check error(s); see review/checks.json", {"errors": 2})
            return out or {}

        return fn

    def build(sdir):
        calls.append("build")
        blend = sdir / "blend" / "short.blend"
        blend.parent.mkdir(parents=True, exist_ok=True)
        blend.write_bytes(b"blend")
        return blend

    monkeypatch.setattr(bsa_blender, "_build_script_mode", build)
    monkeypatch.setattr(bsa_blender, "_reopen", lambda sdir: calls.append("reopen"))
    monkeypatch.setattr(bsa_blender, "_scenes_current", lambda *a: True)
    monkeypatch.setattr(bsa_blender, "_fingerprints", lambda *a, **k: None)  # the fake .blend has no scenes
    monkeypatch.setattr(bsa_blender, "cmd_check", step("check", {"errors": 0, "warnings": 1}))
    monkeypatch.setattr(bsa_blender, "cmd_render", step("render", {"shots": [
        {"shot": "s01", "frames": 3, "rendered": 3, "kept": 0, "why": "not rendered yet"},
        {"shot": "s02", "frames": 4, "rendered": 4, "kept": 0, "why": "fingerprint changed"}]}))
    monkeypatch.setattr(bsa_blender, "cmd_frame_check", step("frame-check", {"errors": 0, "warnings": 0}))
    monkeypatch.setattr(bsa_blender, "cmd_edit", step("edit", {"mp4": str(short / "out" / "x-preview.mp4")}))
    monkeypatch.setenv("BSA_HOME", str(home))


def _result(capsys) -> dict:
    return json.loads(capsys.readouterr().out.split("BSA_RESULT ", 1)[1])


def test_preview_chain_runs_every_step_in_order(home, pendulum, monkeypatch, capsys):
    import bsa_blender

    _past_narration(home, pendulum)
    jobstate.mark_done(pendulum, "build")
    jobstate.mark_done(pendulum, "checks")  # from the old build: the rebuild clears it
    calls = []
    _fake_chain(monkeypatch, home, pendulum, calls)
    assert bsa_blender.main(["preview", "pendulum"]) == 0, capsys.readouterr().err
    out = _result(capsys)
    assert calls == ["build", "reopen", "check", "render", "frame-check", "edit"]
    assert [s["step"] for s in out["steps"]] == ["build", "build done", "mix", "check", "render", "frame-check", "edit"]
    assert out["steps"][0]["why"] == "no blend/short.blend yet" and out["steps"][4]["frames"] == 7
    assert (pendulum / "audio" / "mix.wav").is_file()  # the real CLI mix ran
    assert out["mp4"].endswith("x-preview.mp4")
    assert "build" in jobstate.load_state(pendulum)["stages"]
    status = json.loads((pendulum / "review" / "render-status-preview.json").read_text(encoding="utf-8"))
    assert status["kind"] == "preview" and status["state"] == "done" and status["run"]["current"] is None
    by = {s["name"]: s for s in status["run"]["steps"]}
    assert list(by) == ["timeline", "build", "build done", "mix", "check", "render", "frame-check", "edit"]
    assert by["timeline"]["state"] == "skipped" and by["timeline"]["summary"] == {"skipped": "the timing is current"}
    assert all(by[n]["state"] == "done" for n in list(by)[1:])
    assert by["build"]["summary"] == {"why": "no blend/short.blend yet"} and by["render"]["summary"]["frames"] == 7
    assert all(by[n]["started"] <= by[n]["finished"] for n in list(by)[1:])
    page = (pendulum / "review" / "render-preview.html").read_text(encoding="utf-8")
    assert "Steps" in page and "Done" in page
    status_out = ok(run(home, "render-status", "pendulum"))
    assert status_out["idle"] is True and "preview chain done" in status_out["summary"]


def test_preview_chain_stops_at_the_first_failure(home, pendulum, monkeypatch, capsys):
    import bsa_blender

    _past_narration(home, pendulum)
    calls = []
    _fake_chain(monkeypatch, home, pendulum, calls, fail="check")
    assert bsa_blender.main(["preview", "pendulum"]) == 1
    io = capsys.readouterr()
    assert io.err.strip() == "stopped at check: 2 check error(s); see review/checks.json"
    payload = json.loads(io.out.split("BSA_RESULT ", 1)[1])
    assert payload["stopped_at"] == "check" and payload["errors"] == 2
    assert [s["step"] for s in payload["steps"]] == ["build", "build done", "mix"]
    assert calls == ["build", "reopen", "check"]  # nothing rendered
    status = json.loads((pendulum / "review" / "render-status-preview.json").read_text(encoding="utf-8"))
    states = {s["name"]: s["state"] for s in status["run"]["steps"]}
    assert status["state"] == "failed" and "check error" in status["error"]
    assert states["check"] == "failed" and states["mix"] == "done" and states["render"] == "pending"
    out = ok(run(home, "render-status", "pendulum"))
    assert out["step"]["name"] == "check" and out["last_run"]["state"] == "failed"
    assert "failed at check" in out["summary"] and "BLENDER preview SHORT" in out["summary"]


def test_preview_chain_skips_a_current_build_and_reruns_the_timeline(home, pendulum, monkeypatch, capsys):
    import bsa_blender

    _past_narration(home, pendulum)
    _stale_build(pendulum, newer=False)  # build.py older than the .blend
    jobstate.mark_done(pendulum, "build")
    calls = []
    _fake_chain(monkeypatch, home, pendulum, calls)
    assert bsa_blender.main(["preview", "pendulum"]) == 0, capsys.readouterr().err
    out = _result(capsys)
    assert calls == ["check", "render", "frame-check", "edit"]
    assert out["steps"][0] == {"step": "build", "skipped": "the .blend is current"}
    assert "build done" not in [s["step"] for s in out["steps"]]
    # the timing changed: the chain writes the timeline and rebuilds
    spec = json.loads((pendulum / "source" / "short.json").read_text(encoding="utf-8"))
    spec["lines"][0]["text"] += " and then a good many more words that make this line run much longer"
    (pendulum / "source" / "short.json").write_text(json.dumps(spec), encoding="utf-8")
    calls.clear()
    assert bsa_blender.main(["preview", "pendulum"]) == 0, capsys.readouterr().err
    out = _result(capsys)
    assert [s["step"] for s in out["steps"]][:3] == ["timeline", "build", "build done"]
    assert out["steps"][1]["why"] == "the timing changed"


def test_preview_chain_rebuilds_after_build_py_changed(home, pendulum, monkeypatch, capsys):
    import bsa_blender

    _past_narration(home, pendulum)
    _stale_build(pendulum)
    calls = []
    _fake_chain(monkeypatch, home, pendulum, calls)
    assert bsa_blender.main(["preview", "pendulum", "--no-build"]) == 1
    err = capsys.readouterr().err
    assert "build.py changed since the scene was built" in err and "bsa_mcp_build.py" in err and calls == []
    status = json.loads((pendulum / "review" / "render-status-preview.json").read_text(encoding="utf-8"))
    assert status["state"] == "failed" and {s["name"]: s["state"] for s in status["run"]["steps"]}["build"] == "failed"
    assert bsa_blender.main(["preview", "pendulum"]) == 0, capsys.readouterr().err
    assert calls[:2] == ["build", "reopen"]


def test_preview_chain_needs_the_stages_before_the_build(home, pendulum, monkeypatch, capsys):
    import bsa_blender

    jobstate.mark_done(pendulum, "interview")
    calls = []
    _fake_chain(monkeypatch, home, pendulum, calls)
    assert bsa_blender.main(["preview", "pendulum"]) == 1
    assert "story, storyboard, engine, narration" in capsys.readouterr().err and calls == []


def test_grab_needs_the_edited_video(home, pendulum, monkeypatch, capsys):
    import bsa_blender

    monkeypatch.setenv("BSA_HOME", str(home))
    assert bsa_blender.main(["grab", "pendulum", "--preset", "preview", "--at", "1"]) == 1
    assert "run BLENDER edit SHORT --preset preview first" in capsys.readouterr().err
    assert bsa_blender.main(["grab", "pendulum", "--preset", "preview", "--at", "1", "--sheet"]) == 2  # one or other


def test_grab_maps_seconds_to_frames(home, pendulum, monkeypatch, capsys):
    import types

    import bsa_blender

    mp4 = pendulum / "out" / "pendulum-preview.mp4"
    mp4.parent.mkdir(parents=True, exist_ok=True)
    mp4.write_bytes(b"mp4")
    seen = {}

    def frame_at(t, fps, n):
        if not 0 <= t <= n / fps:
            raise ValueError(f"{t:g} s is outside the video")
        return int(t * fps) + 1

    fake_edit = types.ModuleType("bsa.blender.edit")
    fake_edit.probe = lambda p: {"width": 1080, "height": 1920, "fps": 30.0, "frames": 60, "has_audio": True}
    fake_grab = types.ModuleType("bsa.blender.grab")
    fake_grab.frame_at = frame_at
    fake_grab.grab_frames = lambda mp4, frames, folder, stem: seen.update(frames=frames, folder=folder, stem=stem)
    import bsa.blender

    monkeypatch.setitem(sys.modules, "bsa.blender.edit", fake_edit)
    monkeypatch.setitem(sys.modules, "bsa.blender.grab", fake_grab)
    monkeypatch.setattr(bsa.blender, "grab", fake_grab, raising=False)
    monkeypatch.setenv("BSA_HOME", str(home))
    assert bsa_blender.main(["grab", "pendulum", "--preset", "preview", "--at", "0", "1", "1"]) == 0
    out = _result(capsys)
    assert seen["frames"] == [1, 31] and seen["folder"] == pendulum / "review" / "grabs" and seen["stem"] == "preview"
    assert [g["frame"] for g in out["grabs"]] == [1, 31, 31]
    assert out["grabs"][1]["path"] == str(pendulum / "review" / "grabs" / "preview-f0031.png")
    assert bsa_blender.main(["grab", "pendulum", "--preset", "preview", "--at", "5"]) == 1
    assert "outside the video" in capsys.readouterr().err


def test_frame_check_findings_carry_a_redo_command(tmp_path):
    import bsa_blender
    from bsa.image_checks import Finding
    from bsa.timeline import Span

    span = Span("s02", 100, 160)
    flicker = bsa_blender._redo(tmp_path, "preview", Finding("flicker", "warning", None, 120, "m"), span)
    assert flicker["redo_frames"] == [119, 120, 121]
    assert flicker["redo"] == f'BLENDER render "{tmp_path}" --preset preview --shot s02 --frames 119-121'
    edge = bsa_blender._redo(tmp_path, "final", Finding("flicker", "warning", None, 100, "m"), span)
    assert edge["redo_frames"] == [100, 101]  # never outside the shot
    black = bsa_blender._redo(tmp_path, "final", Finding("black_frame", "error", None, 130, "m"), span)
    assert black["redo_frames"] == [130] and black["redo"].endswith("--frames 130")
    stuck = bsa_blender._redo(tmp_path, "final", Finding("stuck_frame", "warning", None, 130, "m"), span)
    assert stuck["redo_frames"] == [129, 130]
    assert bsa_blender._redo(tmp_path, "final", Finding("x", "error", None, None, "m"), span) == {}


def test_redo_flagged_reads_the_last_frame_check(tmp_path):
    import types

    import bsa_blender
    from bsa.timeline import Span

    spans = {"s01": Span("s01", 1, 50), "s02": Span("s02", 51, 90)}
    args = types.SimpleNamespace(frames=None, redo_flagged=True, preset="preview", shot=None)
    with pytest.raises(bsa_blender.CliError, match="run BLENDER frame-check"):
        bsa_blender._redo_targets(tmp_path, args, None, spans)
    (tmp_path / "review").mkdir()
    rows = [{"shot": "s01", "redo_frames": [9, 10, 11]}, {"shot": "s02", "redo_frames": [90, 91]},
            {"shot": "s01", "redo_frames": [11, 12]}, {"shot": None}, {"shot": "s09", "redo_frames": [1]}]
    (tmp_path / "review" / "frame-checks-preview.json").write_text(json.dumps({"findings": rows}), encoding="utf-8")
    assert bsa_blender._redo_targets(tmp_path, args, None, spans) == {"s01": [9, 10, 11, 12], "s02": [90]}
    args.shot = "s02"
    assert bsa_blender._redo_targets(tmp_path, args, None, spans) == {"s02": [90]}
    args = types.SimpleNamespace(frames="5-6,9", redo_flagged=False, preset="preview", shot=None)
    with pytest.raises(bsa_blender.CliError, match="--frames needs --shot"):
        bsa_blender._redo_targets(tmp_path, args, None, spans)
    args.shot = "s01"
    assert bsa_blender._redo_targets(tmp_path, args, None, spans) == {"s01": [5, 6, 9]}


def test_check_findings_from_two_shots_of_one_scene_are_listed_once():
    import bsa_blender

    a = {"check": "names", "severity": "warning", "obj": "Cube.001", "frame": None, "message": "m", "scene": "W",
         "shot": "s01"}
    b = {**a, "shot": "s02"}
    c = {"check": "pop", "severity": "error", "obj": "Cube", "frame": 40, "message": "p", "scene": "W", "shot": "s02"}
    merged = bsa_blender._merge_findings([a, b, c])
    assert [(f["check"], f["shots"]) for f in merged] == [("pop", ["s02"]), ("names", ["s01", "s02"])]
