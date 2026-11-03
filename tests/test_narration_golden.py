"""The narration golden: what narrating a short writes today, byte for byte, through the CLI's `narrate`.

Without take_mode / lead_in set, narration must stay byte-identical, whatever is added to it later. `_run` narrates a
4-line short (two shots) with a fake local engine and a fake custom tool and returns {relative path: sha256} for every
file under audio/vo/ (the WAVs, durations.json, heard.json, ...) except narrator.json and narrator.log, plus
source/timeline.json. Later tasks reuse it: `_run(tmp_path, provider, **narrator)` merges extra narrator settings
(each `narrator.<key>` is set through the CLI) and `golden(provider)` is the recorded answer. Regenerate the fixture
(only when narration is meant to change for everyone) with:
    uv run python -c "from tests.test_narration_golden import write_fixture; write_fixture()"
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import subprocess
import tempfile
import wave
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

import bsa_cli
from bsa.narrate import custom
from bsa.narrate import run as nrun
from bsa.short import Line, Short, Shot, save_short

FIXTURE = Path(__file__).parent / "fixtures" / "narration-golden.json"
PROVIDERS = ("local", "custom")
SKIPPED = {"narrator.json", "narrator.log"}
SR = 48000


def _tone_for(line_id: str) -> bytes:
    """0.2 s of silence, then a square wave whose pitch comes from the id (integer maths: the same on any machine)."""
    n = int(line_id.lstrip("l") or 0)
    half = SR // (2 * (200 + 40 * n))  # samples per half period
    voice = np.arange(int(SR * (0.6 + 0.1 * n)))
    wave_ = np.where((voice // half) % 2 == 0, 8000, -8000).astype("<i2")
    return np.concatenate([np.zeros(int(SR * 0.2), dtype="<i2"), wave_]).tobytes()


def _write_take(path: Path, line_id: str) -> float:
    path.parent.mkdir(parents=True, exist_ok=True)
    frames = _tone_for(line_id)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(frames)
    return len(frames) / 2 / SR


def _short() -> Short:
    return Short(
        slug="golden", title="Golden", length_s=20, topic="pendulums", audience="curious adults",
        shots=[Shot("s1", "One", "a", "a"), Shot("s2", "Two", "b", "b")],
        lines=[Line("l01", "A pendulum swings.", "s1"), Line("l02", "Its period holds.", "s1"),
               Line("l03", "Length sets the beat.", "s2"), Line("l04", "Mass does not matter.", "s2")],
    )  # fmt: skip


def _local_fake() -> SimpleNamespace:
    def generate(model, lines, voice, speed, out_dir, seed=7):
        got = {ln.id: round(_write_take(Path(out_dir) / f"{ln.id}.wav", ln.id), 3) for ln in lines}
        path = Path(out_dir) / "durations.json"
        had = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
        path.write_text(json.dumps({**had, **got}), encoding="utf-8")
        return got

    def hear(lines, out_dir):
        return {ln.id: {"said": ln.text.lower().rstrip("."), "wer": 0.0} for ln in lines}

    return SimpleNamespace(generate=generate, hear=hear)


def _custom_fake() -> SimpleNamespace:
    def run(cmd, **kw):
        items = json.loads(Path(cmd[cmd.index("--lines") + 1]).read_text(encoding="utf-8"))
        out = Path(cmd[cmd.index("--out") + 1])
        for item in items:
            _write_take(out / f"{item['id']}.wav", item["id"])
        return subprocess.CompletedProcess(cmd, 0, "", "")

    return SimpleNamespace(generate=lambda *a, **k: custom.generate(*a, **k, run=run))


def _cli(*args) -> str:
    """bsa_cli.main(args) in this process; its JSON (stdout) is returned."""
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = bsa_cli.main([str(a) for a in args])
    assert code == 0, out.getvalue()
    return out.getvalue()


def _digests(root: Path) -> dict[str, str]:
    found = {}
    vo = root / "audio" / "vo"
    for path in sorted(p for p in vo.rglob("*") if p.is_file() and p.name not in SKIPPED):
        found[path.relative_to(root).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    timeline = root / "source" / "timeline.json"
    found["source/timeline.json"] = hashlib.sha256(timeline.read_bytes()).hexdigest()
    return found


def _run(tmp_path: Path, provider: str = "local", **narrator) -> dict[str, str]:
    """Narrate the golden short through `bsa_cli narrate` with a fake local engine or custom tool; see the module doc."""
    home = Path(tmp_path) / "bsa_home"
    home.mkdir(parents=True, exist_ok=True)
    keep_env = os.environ.get("BSA_HOME")
    keep_mods = (nrun._local, nrun._custom)
    os.environ["BSA_HOME"] = str(home)
    nrun._local, nrun._custom = _local_fake(), _custom_fake()
    try:
        _cli("settings", "set", "workspace", Path(tmp_path) / "ws")
        sdir = Path(json.loads(_cli("new-short", "Golden"))["short"])
        save_short(_short(), sdir / "source" / "short.json")
        _cli("state", sdir, "done", "interview")
        for stage in ("story", "storyboard"):
            _cli("page", "decide", sdir, stage, "approve", "--by", "chat")
        if provider == "custom":
            _cli("settings", "set", "narrator.provider", "custom")
            _cli("settings", "set", "narrator.command", "tts --lines {lines} --out {out}")
        else:
            _cli("settings", "set", "narrator.provider", "kokoro")
            _cli("settings", "set", "narrator.voice", "v")
        for key, value in narrator.items():
            _cli("settings", "set", f"narrator.{key}", value)
        _cli("narrate", sdir)
        return _digests(sdir)
    finally:
        nrun._local, nrun._custom = keep_mods
        if keep_env is None:
            os.environ.pop("BSA_HOME", None)
        else:
            os.environ["BSA_HOME"] = keep_env


def golden(provider: str) -> dict[str, str]:
    """The recorded digests for `provider` ("local" or "custom")."""
    return json.loads(FIXTURE.read_text(encoding="utf-8"))[provider]


def write_fixture() -> None:
    data = {}
    for provider in PROVIDERS:
        with tempfile.TemporaryDirectory() as tmp:
            data[provider] = _run(Path(tmp), provider)
    FIXTURE.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


@pytest.mark.parametrize("provider", PROVIDERS)
def test_line_mode_without_lead_in_is_byte_identical(tmp_path, provider):
    got = _run(tmp_path, provider)
    assert got == golden(provider)
    assert {"source/timeline.json", "audio/vo/l01.wav", "audio/vo/heard.json"} <= set(got)
    assert ("audio/vo/durations.json" in got) == (provider == "local")  # a custom tool's is never trusted, so not kept
