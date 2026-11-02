"""narrate SHORT --line ID [--say TEXT]: re-take one line with the next seed."""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from bsa.narrate import run as nrun
from bsa.short import Line, Short, Shot, load_short, validate

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
    voiced.cli("narrate", voiced.short, "--line", "l03", "--say", "Four times longer, two times slower.")
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


WIDEST = "l 01.v2 é-À:x"  # anything short.validate allows that can't leave the folder


def test_the_widest_valid_line_id_can_be_retaken(tmp_path):
    shot = Shot(id="s1", title="t", picture="p", scene="s")
    short = Short(slug="s", title="T", length_s=10, lines=[Line(WIDEST, "Hello there.", "s1")], shots=[shot])
    assert validate(short) == []
    vo = tmp_path / "vo"
    nrun.clear_stale_takes(vo, short, {"provider": "kokoro"})
    (vo / f"{WIDEST}.wav").write_bytes(b"x")
    assert nrun.retake(vo, WIDEST) == 8 and not (vo / f"{WIDEST}.wav").exists()
    for bad in ("..", "a..b", "a/b", "a\\b", "a\x00b", "a\nb"):
        with pytest.raises(ValueError):
            nrun.retake(vo, bad)
