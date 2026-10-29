"""Bring-your-own narrator: a user's own command line that writes one WAV per line."""

import json
import struct
import subprocess
import wave
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from bsa import settings
from bsa.narrate import contract, install
from bsa.narrate import run as nrun
from bsa.narrate.contract import NarrationError
from bsa.narrate.custom import generate, normalise_take
from bsa.short import Line, Short


@pytest.fixture(autouse=True)
def _uv_on_path(monkeypatch):
    monkeypatch.setattr(install, "uv_path", lambda: Path("uv"))


def tone(seconds: float, rate: int, amp: float = 0.3) -> np.ndarray:
    t = np.arange(int(rate * seconds)) / rate
    return amp * np.sin(2 * np.pi * 220 * t)


def write_pcm(path: Path, samples: np.ndarray, rate: int, channels: int = 1, bits: int = 16) -> None:
    """A WAV by hand: 16/24-bit PCM, or 32-bit float (format tag 3) which the stdlib wave module cannot write."""
    path.parent.mkdir(parents=True, exist_ok=True)
    x = np.repeat(samples[:, None], channels, axis=1) if channels > 1 else samples[:, None]
    if bits == 16:
        data, tag = (x * 32767).astype("<i2").tobytes(), 1
    elif bits == 24:
        ints = (x * 8388607).astype("<i4")
        data, tag = b"".join(ints.tobytes()[i : i + 3] for i in range(0, ints.size * 4, 4)), 1
    else:
        data, tag, bits = x.astype("<f4").tobytes(), 3, 32
    block = channels * bits // 8
    fmt = struct.pack("<HHIIHH", tag, channels, rate, rate * block, block, bits)
    body = b"WAVE" + b"fmt " + struct.pack("<I", len(fmt)) + fmt + b"data" + struct.pack("<I", len(data)) + data
    path.write_bytes(b"RIFF" + struct.pack("<I", len(body)) + body)


def lines(*ids):
    return [Line(id=i, text=f"Text for {i}.", shot="s1") for i in ids]


def fake_run(calls, rate=48000, skip=()):
    """Stands in for subprocess.run: records the args and writes <out>/<id>.wav for each line in --lines."""

    def run(cmd, **kw):
        lines_file = Path(cmd[cmd.index("--lines") + 1])
        out = Path(cmd[cmd.index("--out") + 1])
        items = json.loads(lines_file.read_text(encoding="utf-8"))
        calls.append((cmd, kw, [i["id"] for i in items]))
        for item in items:
            if item["id"] not in skip:
                write_pcm(out / f"{item['id']}.wav", tone(1.0, rate), rate)
        return subprocess.CompletedProcess(cmd, 0, "", "")

    return run


def test_placeholders_filled_per_argument_without_a_shell(tmp_path):
    out = tmp_path / "my folder" / "vo"
    calls = []
    cmd = '"tts tool" speak --lines {lines} --out {out} --seed {seed} --voice {voice} --speed {speed}'
    d = generate(cmd, lines("l01"), out, seed=9, voice="deep one", speed=1.2, run=fake_run(calls))
    args, kw, _ = calls[0]
    assert isinstance(args, list) and not kw.get("shell")
    assert args[0] == "tts tool"
    assert args[args.index("--out") + 1] == str(out) and " " in str(out)
    assert args[args.index("--lines") + 1] == str(out / "lines.json")
    assert args[args.index("--seed") + 1] == "9"
    assert args[args.index("--voice") + 1] == "deep one"
    assert args[args.index("--speed") + 1] == "1.2"
    assert "timeout" not in kw
    assert d == {"l01": 1.0}


def test_takes_in_other_formats_are_normalised_by_generate(tmp_path):
    d = generate("tts --lines {lines} --out {out}", lines("l01"), tmp_path, run=fake_run([], rate=24000))
    assert contract.check_take(tmp_path / "l01.wav") == [] and abs(d["l01"] - 1.0) < 1 / 30


def test_tools_durations_json_is_ignored(tmp_path):
    (tmp_path / "durations.json").write_text('{"l01": 99}', encoding="utf-8")
    d = generate("tts --lines {lines} --out {out}", lines("l01"), tmp_path, run=fake_run([]))
    assert d == {"l01": 1.0}
    assert contract.read_durations(tmp_path, ["l01"]) == {"l01": 1.0}


def test_voice_placeholder_without_a_voice_is_an_error(tmp_path):
    with pytest.raises(NarrationError, match="voice"):
        generate("tts --voice {voice} --lines {lines} --out {out}", lines("l01"), tmp_path, run=fake_run([]))


def test_command_not_found(tmp_path):
    def run(cmd, **kw):
        raise FileNotFoundError(cmd[0])

    with pytest.raises(NarrationError, match=r"nope.*was not found.*put the program that runs it first, e\.g\. python script\.py"):
        generate("nope --lines {lines} --out {out}", lines("l01"), tmp_path, run=run)


def test_nonzero_exit_reports_code_and_last_stderr_line(tmp_path):
    def run(cmd, **kw):
        return subprocess.CompletedProcess(cmd, 3, "", "warming up\nCUDA exploded\n")

    with pytest.raises(NarrationError, match=r"exit 3.*CUDA exploded"):
        generate("tts --lines {lines} --out {out}", lines("l01"), tmp_path, run=run)


def test_missing_wav_names_the_line(tmp_path):
    with pytest.raises(NarrationError, match="l02"):
        generate("tts --lines {lines} --out {out}", lines("l01", "l02"), tmp_path, run=fake_run([], skip={"l02"}))


@pytest.mark.parametrize(
    ("rate", "channels", "bits"), [(24000, 2, 16), (44100, 1, 32), (48000, 1, 24), (22050, 2, 24), (48000, 1, 16)]
)
def test_normalise_take_to_contract(tmp_path, rate, channels, bits):
    p = tmp_path / "a.wav"
    write_pcm(p, tone(1.5, rate), rate, channels, bits)
    normalise_take(p)
    assert contract.check_take(p) == []
    with wave.open(str(p), "rb") as w:
        assert (w.getframerate(), w.getnchannels(), w.getsampwidth()) == (48000, 1, 2)
        assert abs(w.getnframes() / 48000 - 1.5) <= 1 / 30
        peak = np.abs(np.frombuffer(w.readframes(w.getnframes()), dtype="<i2")).max()
    assert 0.25 * 32767 < peak < 0.35 * 32767  # level preserved


def test_normalise_take_rejects_mp3(tmp_path):
    p = tmp_path / "a.wav"
    p.write_bytes(b"ID3\x03\x00\x00\x00\x00\x00\x00" + b"\xff\xfb\x90\x00" * 50)
    with pytest.raises(NarrationError, match="adapter"):
        normalise_take(p)


def test_narrate_missing_wav_is_captions_only_once_takes_kept(tmp_path):
    short = Short(slug="t", title="T", length_s=10, lines=lines("l01", "l02"))
    cfg = {"narrator": {"provider": "custom", "command": "tts --lines {lines} --out {out}", "verify": False}}
    mod = SimpleNamespace(generate=lambda *a, **k: generate(*a, **k, run=fake_run([], skip={"l02"})))
    res = nrun.narrate(tmp_path, short, cfg, custom=mod)
    assert res.status == "captions-only" and res.durations is None
    assert res.message.count("captions only") == 1 and "l02" in res.message
    assert (tmp_path / "audio" / "vo" / "l01.wav").is_file()


def test_regeneration_runs_only_bad_lines_with_seeds_8_then_9(tmp_path):
    short = Short(slug="t", title="T", length_s=10, lines=lines("l01", "l02"))
    cfg = {"narrator": {"provider": "custom", "command": "tts --lines {lines} --out {out} --seed {seed}"}}
    calls = []
    mod = SimpleNamespace(generate=lambda *a, **k: generate(*a, **k, run=fake_run(calls)))

    def verify(lns, out):
        return {x.id: 0.5 if x.id == "l02" else 0.0 for x in lns}

    res = nrun.narrate(tmp_path, short, cfg, custom=mod, local=SimpleNamespace(verify=verify))
    seen = [(c[2], c[0][c[0].index("--seed") + 1]) for c in calls]
    assert seen == [(["l01", "l02"], "7"), (["l02"], "8"), (["l02"], "9")]
    assert res.status == "ok" and "l02" in res.message


def test_resume_speaks_only_missing_lines(tmp_path):
    short = Short(slug="t", title="T", length_s=10, lines=lines("l01", "l02"))
    write_pcm(tmp_path / "audio" / "vo" / "l01.wav", tone(1.0, 48000), 48000)
    cfg = {"narrator": {"provider": "custom", "command": "tts --lines {lines} --out {out}", "verify": False}}
    calls = []
    mod = SimpleNamespace(generate=lambda *a, **k: generate(*a, **k, run=fake_run(calls)))
    res = nrun.narrate(tmp_path, short, cfg, custom=mod)
    assert [c[2] for c in calls] == [["l02"]] and res.status == "ok"


def test_changed_command_clears_takes(tmp_path):
    short = Short(slug="t", title="T", length_s=10, lines=lines("l01"))
    out = tmp_path / "vo"
    a = {"provider": "custom", "command": "toolA --out {out}"}
    nrun.clear_stale_takes(out, short, a)  # records the narrator
    write_pcm(out / "l01.wav", tone(1.0, 48000), 48000)
    assert nrun.clear_stale_takes(out, short, a) == []
    assert nrun.clear_stale_takes(out, short, {**a, "command": "toolB --out {out}"}) == ["l01"]


def test_split_command_keeps_quoted_paths():
    assert settings.split_command('"a b/c" x --o "d e"') == ["a b/c", "x", "--o", "d e"]


def test_tool_cannot_prompt_and_output_decodes_leniently(tmp_path):
    calls = []
    generate("tts --lines {lines} --out {out}", lines("l01"), tmp_path, run=fake_run(calls))
    kw = calls[0][1]
    assert kw["stdin"] == subprocess.DEVNULL and kw["errors"] == "replace"


def test_oserror_starting_the_command_is_plain(tmp_path):
    def run(cmd, **kw):
        raise OSError(193, "%1 is not a valid Win32 application")

    with pytest.raises(NarrationError, match=r"couldn't start voice\.py.*put the program that runs it first"):
        generate("voice.py --lines {lines} --out {out}", lines("l01"), tmp_path, run=run)


def test_impossible_wav_header_is_a_plain_error(tmp_path):
    p = tmp_path / "a.wav"
    write_pcm(p, tone(0.1, 48000), 48000)
    raw = bytearray(p.read_bytes())
    raw[34:36] = (0).to_bytes(2, "little")  # bits per sample = 0
    p.write_bytes(bytes(raw))
    with pytest.raises(NarrationError, match="not a WAV I can read"):
        normalise_take(p)


def test_float_wav_with_nan_and_inf_is_cleaned(tmp_path):
    p = tmp_path / "a.wav"
    x = tone(0.5, 48000)
    x[10], x[11], x[12] = np.nan, np.inf, -np.inf
    write_pcm(p, x, 48000, bits=32)
    normalise_take(p)
    with wave.open(str(p), "rb") as w:
        out = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2")
    assert (out[10], out[11], out[12]) == (0, 32767, -32767)
