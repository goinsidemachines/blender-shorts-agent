"""narrate SHORT --line ID [--say TEXT]: re-take one line with the next seed."""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from bsa.narrate import run as nrun
from bsa.short import Line, Short, Shot, line_id_problem, load_short, validate

ROOT = Path(__file__).resolve().parent.parent
TOOL = """\
import json, math, os, struct, sys, wave
a = sys.argv
items = json.load(open(a[a.index('--lines') + 1], encoding='utf-8'))
out = a[a.index('--out') + 1]
seed = int(a[a.index('--seed') + 1])
here = os.path.dirname(os.path.abspath(__file__))
for it in items:
    if it['id'] == 'closing' and os.path.exists(os.path.join(here, 'fail_closing')):
        sys.exit(3)
    with open(os.path.join(here, 'seeds.txt'), 'a') as f:
        f.write(str(seed) + '\\n')
    w = wave.open(out + '/' + it['id'] + '.wav', 'wb'); w.setnchannels(1); w.setsampwidth(2)
    w.setframerate(48000)
    w.writeframes(b''.join(struct.pack('<h', int(9000 * math.sin(i / (10 + seed)))) for i in range(48000)))
    w.close()
"""


class Env:
    def __init__(self, tmp_path):
        self.env = {**os.environ, "BSA_HOME": str(tmp_path / "home")}
        (tmp_path / "home").mkdir()

    def run(self, *args):
        return subprocess.run([sys.executable, str(ROOT / "bsa_cli.py"), *map(str, args)], capture_output=True,
                              text=True, env=self.env, check=False)  # fmt: skip

    def cli(self, *args):
        proc = self.run(*args)
        assert proc.returncode == 0, proc.stdout + proc.stderr
        return json.loads(proc.stdout)


def setup(tmp_path, *, closing=None):
    """A short with its storyboard approved and a custom voice tool that logs each seed it is given to seeds.txt
    and pitches the take by it; narrator.verify is off. Nothing is narrated yet."""
    e = Env(tmp_path)
    tool = tmp_path / "tool.py"
    tool.write_text(TOOL, encoding="utf-8")
    e.cli("settings", "set", "workspace", tmp_path / "ws")
    short = Path(e.cli("new-short", "Pendulum")["short"])
    path = short / "source" / "short.json"
    shutil.copyfile(ROOT / "tests" / "e2e" / "pendulum" / "source" / "short.json", path)
    if closing:
        data = json.loads(path.read_text(encoding="utf-8"))
        data["closing"] = closing
        path.write_text(json.dumps(data), encoding="utf-8")
    e.cli("state", short, "done", "interview")
    for stage in ("story", "storyboard"):
        e.cli("page", "decide", short, stage, "approve", "--by", "chat")
    e.cli("settings", "set", "narrator.provider", "custom")
    command = f'"{sys.executable}" "{tool}" --lines {{lines}} --out {{out}} --seed {{seed}}'
    e.cli("settings", "set", "narrator.command", command)
    e.cli("settings", "set", "narrator.verify", "false")
    e.short, e.seeds = short, tmp_path / "seeds.txt"
    return e


@pytest.fixture
def voiced(tmp_path):
    e = setup(tmp_path)
    e.cli("narrate", e.short)
    e.seeds.unlink()
    return e


def wavs(short):
    return {p.name: p.read_bytes() for p in (short / "audio" / "vo").glob("*.wav")}


def test_retake_remakes_only_that_line_with_the_next_seed(voiced):
    before = wavs(voiced.short)
    out = voiced.cli("narrate", voiced.short, "--line", "l02")
    assert out["retake"] == {"line": "l02", "seed": 8} and voiced.seeds.read_text().split() == ["8"]
    after = wavs(voiced.short)
    assert after["l02.wav"] != before["l02.wav"] and all(after[k] == before[k] for k in before if k != "l02.wav")
    assert out["timeline"]["takes"] is True


def test_retake_with_say_writes_the_respelling(voiced):
    out = voiced.cli("narrate", voiced.short, "--line", "l03", "--say", "Four times longer, two times slower.")
    assert out["retake"]["seed"] == 8 and voiced.seeds.read_text().split() == ["8"]  # the seed really used
    assert load_short(voiced.short / "source" / "short.json").lines[2].say == "Four times longer, two times slower."


@pytest.mark.parametrize(
    "args, message",
    [
        (["--line", "l99"], "l01, l02, l03, l04"),
        (["--say", "Hello there."], "--say needs --line"),
        (["--line", "l02", "--say", "  "], "can't be empty"),
        (["--line", "l01", "--say", "It takes 5 seconds."], "isn't valid"),  # a number without a fact
    ],
)
def test_retake_refuses_and_touches_nothing(voiced, args, message):
    spec = voiced.short / "source" / "short.json"
    before, spec_before = wavs(voiced.short), spec.read_bytes()
    proc = voiced.run("narrate", voiced.short, *args)
    assert proc.returncode == 1 and message in proc.stderr, proc.stdout + proc.stderr
    assert wavs(voiced.short) == before and spec.read_bytes() == spec_before and not voiced.seeds.exists()


def test_retake_refuses_before_narration_and_for_captions_only(tmp_path):
    e = setup(tmp_path)
    proc = e.run("narrate", e.short, "--line", "l01")
    assert proc.returncode == 1 and "Narrate the short first: CLI narrate SHORT" in proc.stderr
    e.cli("narrate", e.short)
    e.cli("settings", "set", "narrator.provider", "none")
    proc = e.run("narrate", e.short, "--line", "l01")
    assert proc.returncode == 1 and "captions only, so there is no take to re-take" in proc.stderr


def test_a_failed_closing_line_keeps_the_storys_voice_check(tmp_path):
    e = setup(tmp_path, closing={"website": "example.com", "voice_line": "Visit example dot com."})
    (tmp_path / "fail_closing").write_text("x", encoding="utf-8")
    out = e.cli("narrate", e.short)
    assert out["status"] == "ok" and "closing card is silent" in out["warnings"][0]
    heard = json.loads((e.short / "audio" / "vo" / nrun.HEARD_FILE).read_text(encoding="utf-8"))
    assert sorted(heard["lines"]) == ["l01", "l02", "l03", "l04"]
    assert heard["why_not"] != nrun.CAPTIONS_ONLY


BAD_IDS = ["C:x", "l:01", "a:b", "CON", "nul", "com1.v2", "a.", "../x", "a/b", "a\\b", "", " a", ".a", "a b"]
GOOD_ID = "l01.v2_a-b"  # the widest an id can be


def _short_with(line_id):
    shot = Shot(id="s1", title="t", picture="p", scene="s")
    return Short(slug="s", title="T", length_s=10, lines=[Line(line_id, "Hello there.", "s1")], shots=[shot])


@pytest.mark.parametrize("bad", BAD_IDS)
def test_bad_line_ids_are_refused_by_validate_and_retake(tmp_path, bad):
    assert line_id_problem(bad) and any("rename it" in e for e in validate(_short_with(bad)))
    vo = tmp_path / "vo"
    nrun.clear_stale_takes(vo, _short_with("l01"), {"provider": "kokoro"})
    with pytest.raises(ValueError):
        nrun.retake(vo, bad)
    assert nrun.retake_problem(vo, bad)


def test_the_widest_valid_line_id_is_accepted_by_validate_and_retake(tmp_path):
    short = _short_with(GOOD_ID)
    assert line_id_problem(GOOD_ID) is None and validate(short) == []
    vo = tmp_path / "vo"
    nrun.clear_stale_takes(vo, short, {"provider": "kokoro"})
    (vo / f"{GOOD_ID}.wav").write_bytes(b"x")
    assert nrun.retake(vo, GOOD_ID) == 8 and not (vo / f"{GOOD_ID}.wav").exists()


def test_a_refused_retake_does_not_save_the_respelling(voiced):
    vo = voiced.short / "audio" / "vo"
    record = json.loads((vo / nrun.RECORD_FILE).read_text(encoding="utf-8"))
    del record["lines"]["l02"]  # the takes are not recorded as being for l02
    (vo / nrun.RECORD_FILE).write_text(json.dumps(record), encoding="utf-8")
    spec = voiced.short / "source" / "short.json"
    before = spec.read_bytes()
    proc = voiced.run("narrate", voiced.short, "--line", "l02", "--say", "Something else.")
    assert proc.returncode == 1 and "not a line of this short" in proc.stderr
    assert spec.read_bytes() == before


def test_the_closing_line_can_be_retaken_but_not_respelled(tmp_path):
    e = setup(tmp_path, closing={"website": "example.com", "voice_line": "Visit example dot com."})
    e.cli("narrate", e.short)
    e.seeds.unlink()
    before = wavs(e.short)
    out = e.cli("narrate", e.short, "--line", "closing")
    assert out["retake"] == {"line": "closing", "seed": 8} and e.seeds.read_text().split() == ["8"]
    after = wavs(e.short)
    assert after["closing.wav"] != before["closing.wav"] and after["l01.wav"] == before["l01.wav"]
    proc = e.run("narrate", e.short, "--line", "closing", "--say", "Hello.")
    assert proc.returncode == 1 and "voice_line" in proc.stderr


@pytest.mark.parametrize("bad", ["C:x", "../x", "a/b", "nul"])
def test_narrate_refuses_a_bad_line_id_before_touching_anything(tmp_path, bad):
    e = setup(tmp_path)
    spec = e.short / "source" / "short.json"
    data = json.loads(spec.read_text(encoding="utf-8"))
    data["lines"][0]["id"] = bad
    spec.write_text(json.dumps(data), encoding="utf-8")  # never went through validate
    outside = e.short / "audio" / "x.wav"
    outside.parent.mkdir(parents=True, exist_ok=True)
    outside.write_bytes(b"keep")
    before = sorted(str(p) for p in e.short.rglob("*"))
    proc = e.run("narrate", e.short)
    assert proc.returncode == 1 and "rename it" in proc.stderr, proc.stdout + proc.stderr
    assert outside.read_bytes() == b"keep" and sorted(str(p) for p in e.short.rglob("*")) == before
    assert not e.seeds.exists()


def test_the_narrator_entry_points_refuse_a_bad_line_id(tmp_path):
    short = _short_with("C:x")
    with pytest.raises(ValueError, match="rename it"):
        nrun.narrate(tmp_path, short, {"narrator": {"provider": "kokoro"}})
    with pytest.raises(ValueError, match="rename it"):
        nrun.clear_stale_takes(tmp_path / "vo", short, {"provider": "kokoro"})
    assert not (tmp_path / "vo").exists()


def test_retake_saves_the_next_seed_before_throwing_the_take_away(tmp_path, monkeypatch):
    vo = tmp_path / "vo"
    vo.mkdir()
    (vo / nrun.RECORD_FILE).write_text(json.dumps({"lines": {"l01": {}}, "seeds": {"l01": 7}}), encoding="utf-8")
    (vo / "l01.wav").write_bytes(b"RIFF")
    seen = []
    real = nrun._record_seeds

    def record(out_dir, seeds):
        seen.append((vo / "l01.wav").is_file())  # the take is still there when the seed is saved
        return real(out_dir, seeds)

    monkeypatch.setattr(nrun, "_record_seeds", record)
    assert nrun.retake(vo, "l01") == 8 and seen == [True] and not (vo / "l01.wav").exists()
    assert nrun.take_seeds(vo)["l01"] == 8
    (vo / "l01.wav").write_bytes(b"RIFF")
    monkeypatch.setattr(nrun, "_record_seeds", lambda out_dir, seeds: "can't save")
    with pytest.raises(OSError, match="can't save"):
        nrun.retake(vo, "l01")
    assert (vo / "l01.wav").is_file() and nrun.take_seeds(vo)["l01"] == 8  # no new seed: the take is kept
