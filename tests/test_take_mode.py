"""Paragraph take mode through the CLI's `narrate`, with a made-up voice tool (a custom command).

The tool writes, for each item it is asked for, one tone per sentence of the item's text with 0.3 s of silence after
each, except after a sentence holding the marker word "joined" (that one runs straight into the next). So a paragraph
take cuts cleanly at every line break by construction, and a "joined" line has no pause after it.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from bsa.narrate import contract, takes
from bsa.narrate import run as nrun
from bsa.short import Line, Short, Shot, save_short
from tests.test_narration_golden import _run, golden

ROOT = Path(__file__).resolve().parent.parent
TOOL = """\
import json, math, os, re, struct, sys, wave
a = sys.argv
items = json.load(open(a[a.index('--lines') + 1], encoding='utf-8'))
out = a[a.index('--out') + 1]
seed = int(a[a.index('--seed') + 1])
take = a[a.index('--take') + 1] if '--take' in a else None
here = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(here, 'calls.txt'), 'a', encoding='utf-8') as f:
    f.write(json.dumps({'ids': [it['id'] for it in items], 'texts': [it['text'] for it in items], 'seed': seed,
                        'take': take, 'items': items}) + '\\n')
SR = 48000
for it in items:
    sentences = [s for s in re.split(r'(?<=[.!?])\\s+', it['text'].strip()) if s]
    frames = [0] * int(SR * 0.2)
    for k, s in enumerate(sentences):
        f0 = 200 + 60 * k
        frames += [int(9000 * math.sin(2 * math.pi * f0 * i / SR)) for i in range(int(SR * 0.025 * len(s)))]
        if 'joined' not in s or k == len(sentences) - 1:
            frames += [0] * int(SR * 0.3)
    w = wave.open(os.path.join(out, it['id'] + '.wav'), 'wb')
    w.setnchannels(1); w.setsampwidth(2); w.setframerate(SR)
    w.writeframes(struct.pack('<%dh' % len(frames), *frames))
    w.close()
"""
TEXTS = ["The pendulum swings out wide.", "Gravity pulls it back down.", "Each swing takes the same time.",
         "Only its length can change that."]  # fmt: skip


def _short(texts=TEXTS) -> Short:
    return Short(
        slug="takes", title="Takes", length_s=20, topic="pendulums", audience="curious adults",
        shots=[Shot("s1", "One", "a", "a"), Shot("s2", "Two", "b", "b")],
        lines=[Line(f"l0{i + 1}", t, "s1" if i < 2 else "s2") for i, t in enumerate(texts)],
    )  # fmt: skip


class Env:
    def __init__(self, tmp_path: Path, *, take_placeholder: bool = True, texts=TEXTS):
        home = tmp_path / "home"
        home.mkdir()
        self.env = {**os.environ, "BSA_HOME": str(home)}
        tool = tmp_path / "tool.py"
        tool.write_text(TOOL, encoding="utf-8")
        self.calls_file = tmp_path / "calls.txt"
        self.cli("settings", "set", "workspace", tmp_path / "ws")
        self.short = Path(self.cli("new-short", "Takes")["short"])
        save_short(_short(texts), self.short / "source" / "short.json")
        self.cli("state", self.short, "done", "interview")
        for stage in ("story", "storyboard"):
            self.cli("page", "decide", self.short, stage, "approve", "--by", "chat")
        self.cli("settings", "set", "narrator.provider", "custom")
        command = f'"{sys.executable}" "{tool}" --lines {{lines}} --out {{out}} --seed {{seed}}'
        if take_placeholder:
            command += " --take {take}"
        self.cli("settings", "set", "narrator.command", command)
        self.cli("settings", "set", "narrator.verify", "false")
        self.cli("settings", "set", "narrator.take_mode", "paragraph")
        self.vo = self.short / "audio" / "vo"

    def cli(self, *args):
        proc = subprocess.run([sys.executable, str(ROOT / "bsa_cli.py"), *map(str, args)], capture_output=True,
                              text=True, env=self.env, check=False)  # fmt: skip
        assert proc.returncode == 0, proc.stdout + proc.stderr
        return json.loads(proc.stdout)

    def calls(self) -> list[dict]:
        return [json.loads(x) for x in self.calls_file.read_text(encoding="utf-8").splitlines()]


def test_paragraph_mode_cuts_takes_into_line_takes(tmp_path):
    e = Env(tmp_path)
    out = e.cli("narrate", e.short)
    assert out["status"] == "ok", out
    groups = takes.group(_short().lines, _short().shots)
    calls = e.calls()
    assert len(calls) == len(groups) and all(len(c["ids"]) == 1 and c["take"] for c in calls)  # one call per take
    assert [c["texts"][0] for c in calls] == [takes.joined(g) for g in groups]
    # the take's item also names its lines and their texts, for a tool that wants a pause between them
    assert [(c["items"][0]["lines"], c["items"][0]["texts"]) for c in calls] == [
        ([ln.id for ln in g], [ln.text for ln in g]) for g in groups
    ]
    ids = [ln.id for ln in _short().lines]
    for lid in ids:
        assert contract.check_take(e.vo / f"{lid}.wav") == []
    assert out["sources"] == {lid: "take" for lid in ids} and "fallbacks" not in out
    record = json.loads((e.vo / nrun.RECORD_FILE).read_text(encoding="utf-8"))
    assert record["takes"] == [
        {"id": takes.take_id(i + 1), "lines": [ln.id for ln in g], "seed": 7, "file": f"takes/{takes.take_id(i + 1)}.wav"}
        for i, g in enumerate(groups)
    ]
    for t in record["takes"]:
        assert (e.vo / t["file"]).is_file()
    assert out["timeline"]["takes"] is True
    secs = {lid: round(contract.wav_seconds(e.vo / f"{lid}.wav"), 3) for lid in ids}
    assert out["durations"] == secs
    # each cut line is about one tone: 0.025 s per character, plus the kept pause around it
    for lid, text in zip(ids, TEXTS, strict=True):
        assert 0.025 * len(text) < secs[lid] < 0.025 * len(text) + 0.8


def test_paragraph_fallback_speaks_only_the_uncut_lines(tmp_path):
    # Without Whisper the cut goes by characters and falls back on any break a pause could as well belong to, so
    # the lines around the missing pause are long enough that each remaining pause can only be its own break's.
    texts = ["The pendulum swings out wide and then slowly back.", "It is joined at the top.", "Each swing is even.",
             "Only the length of its string can ever change that."]  # fmt: skip
    e = Env(tmp_path, texts=texts)
    out = e.cli("narrate", e.short)
    assert out["status"] == "ok", out
    calls = e.calls()
    assert [c["ids"] for c in calls] == [["t01"], ["l02", "l03"]] and calls[1]["take"] == ""
    assert out["sources"] == {"l01": "take", "l02": "fallback", "l03": "fallback", "l04": "take"}
    assert out["fallbacks"] == {"l02": "no clear pause between l02 and l03", "l03": "no clear pause between l02 and l03"}
    assert nrun.line_sources(e.vo)["l02"] == {"source": "fallback", "reason": "no clear pause between l02 and l03"}
    for lid in ("l01", "l02", "l03", "l04"):
        assert contract.check_take(e.vo / f"{lid}.wav") == []


def test_a_command_without_take_stays_line_by_line_and_says_so(tmp_path):
    e = Env(tmp_path, take_placeholder=False)
    out = e.cli("narrate", e.short)
    assert out["status"] == "ok", out
    assert [c["ids"] for c in e.calls()] == [["l01", "l02", "l03", "l04"]]
    assert "take mode needs {take} in your voice command, so the lines were spoken one by one" in out["message"]
    assert out["sources"] == {lid: "line" for lid in ("l01", "l02", "l03", "l04")}
    assert "takes" not in json.loads((e.vo / nrun.RECORD_FILE).read_text(encoding="utf-8"))


def test_a_second_run_keeps_the_cut_takes_and_their_record(tmp_path):
    e = Env(tmp_path)
    e.cli("narrate", e.short)
    before = {p.name: p.read_bytes() for p in e.vo.glob("*.wav")}
    record = json.loads((e.vo / nrun.RECORD_FILE).read_text(encoding="utf-8"))
    e.calls_file.unlink()
    out = e.cli("narrate", e.short)
    assert not e.calls_file.exists() and {p.name: p.read_bytes() for p in e.vo.glob("*.wav")} == before
    assert json.loads((e.vo / nrun.RECORD_FILE).read_text(encoding="utf-8"))["takes"] == record["takes"]
    assert set(out["sources"].values()) == {"take"}
    e.cli("settings", "set", "narrator.take_mode", "line")  # another mode: the takes and their record go
    e.cli("narrate", e.short)
    assert not list((e.vo / nrun.TAKES_DIR).glob("*.wav"))
    assert "takes" not in json.loads((e.vo / nrun.RECORD_FILE).read_text(encoding="utf-8"))


def test_line_mode_is_unchanged(tmp_path):
    for provider in ("local", "custom"):
        assert _run(tmp_path / provider, provider) == golden(provider)
