import io
import json
import subprocess
import urllib.error
import wave
from pathlib import Path
from types import SimpleNamespace

import pytest

from bsa import jobstate
from bsa.narrate import contract, elevenlabs, install, local
from bsa.narrate import run as nrun
from bsa.short import Line, Short

KEY = "sk_test_SECRET_KEY_12345"


@pytest.fixture(autouse=True)
def _uv_on_path(monkeypatch):
    """Local-engine commands start with uv; pin it so these tests don't depend on this machine's uv."""
    monkeypatch.setattr(install, "uv_path", lambda: Path("uv"))


def pcm(seconds: float, amp: int = 8000) -> bytes:
    n = int(48000 * seconds)
    return b"".join((amp if i % 2 else -amp).to_bytes(2, "little", signed=True) for i in range(n))


def write_wav(path: Path, seconds: float = 1.0, rate: int = 48000, amp: int = 8000) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    n = int(rate * seconds)
    frames = b"".join((amp if i % 2 else -amp).to_bytes(2, "little", signed=True) for i in range(n))
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(frames)


def fake_http(script):
    """script: list of bytes (success) or int (HTTP error code), consumed in order. Records requests."""
    calls = []

    def http(req, timeout=None):
        calls.append(req)
        item = script.pop(0)
        if isinstance(item, int):
            raise urllib.error.HTTPError(req.full_url, item, "err", {}, io.BytesIO(b""))
        return io.BytesIO(item)

    http.calls = calls
    return http


def lines(*ids):
    return [Line(id=i, text=f"Text for {i}.", shot="s1") for i in ids]


def make_short(*ids):
    return Short(slug="t", title="T", length_s=10, lines=lines(*ids))


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(elevenlabs, "_sleep", lambda s: None)


def test_contract_rejects_wrong_rate(tmp_path):
    p = tmp_path / "a.wav"
    write_wav(p, 1.0, rate=24000)
    problems = contract.check_take(p)
    assert problems and any("48000" in x for x in problems)


def test_contract_accepts_good_and_flags_silent(tmp_path):
    write_wav(tmp_path / "ok.wav")
    write_wav(tmp_path / "quiet.wav", amp=0)
    assert contract.check_take(tmp_path / "ok.wav") == []
    assert any("silent" in x for x in contract.check_take(tmp_path / "quiet.wav"))


def test_read_durations_cross_checks(tmp_path):
    write_wav(tmp_path / "l01.wav", 1.0)
    (tmp_path / "durations.json").write_text(json.dumps({"l01": 1.0}))
    assert contract.read_durations(tmp_path, ["l01"]) == {"l01": 1.0}
    (tmp_path / "durations.json").write_text(json.dumps({"l01": 2.0}))
    with pytest.raises(contract.NarrationError):
        contract.read_durations(tmp_path, ["l01"])


def test_write_lines(tmp_path):
    contract.write_lines(tmp_path / "l.json", lines("l01"))
    assert json.loads((tmp_path / "l.json").read_text()) == [{"id": "l01", "text": "Text for l01."}]


def test_the_voice_speaks_say_when_a_line_has_one(tmp_path):
    """`say` is what the narrator reads (and Whisper checks); `text` stays the caption."""
    line = Line("l01", "30 × 2.7 = 80 × 1", "s1", say="Thirty times two point seven, eighty times one.")
    contract.write_lines(tmp_path / "l.json", [line, *lines("l02")])
    assert json.loads((tmp_path / "l.json").read_text(encoding="utf-8")) == [
        {"id": "l01", "text": "Thirty times two point seven, eighty times one."},
        {"id": "l02", "text": "Text for l02."},
    ]
    http = fake_http([pcm(1.0)])
    elevenlabs.generate([line], "voiceX", 1.0, tmp_path / "vo", api_key=KEY, http=http)
    assert json.loads(http.calls[0].data)["text"].startswith("Thirty times")


def test_changing_say_remakes_the_take(tmp_path):
    vo = tmp_path / "vo"
    narrator = {"provider": "kokoro", "voice": "am_michael", "speed": 1.0}
    short = _take_short()
    nrun.clear_stale_takes(vo, short, narrator)
    for lid in ("l01", "l02"):
        (vo / f"{lid}.wav").write_bytes(b"x")
    short.lines[0].say = "Hello, there."
    assert nrun.clear_stale_takes(vo, short, narrator) == ["l01"]


def test_elevenlabs_writes_takes_and_durations(tmp_path):
    http = fake_http([pcm(1.0)])
    d = elevenlabs.generate(lines("l01"), "voiceX", 1.0, tmp_path, api_key=KEY, http=http)
    assert (tmp_path / "l01.wav").exists()
    assert d == {"l01": 1.0}
    req = http.calls[0]
    assert req.full_url == "https://api.elevenlabs.io/v1/text-to-speech/voiceX?output_format=pcm_48000"
    body = json.loads(req.data)
    assert body["model_id"] == "eleven_v3" and body["voice_settings"] == {"speed": 1.0}
    assert contract.check_take(tmp_path / "l01.wav") == []


def test_key_never_saved(tmp_path, bsa_home):
    out = tmp_path / "vo"
    elevenlabs.generate(lines("l01"), "v", 1.0, out, api_key=KEY, http=fake_http([pcm(0.5)]))
    for root in (bsa_home, out):
        for f in root.rglob("*"):
            if f.is_file():
                assert KEY.encode() not in f.read_bytes()


def test_key_from_env(tmp_path, monkeypatch):
    monkeypatch.setenv("ELEVENLABS_API_KEY", KEY)
    http = fake_http([pcm(0.5)])
    elevenlabs.generate(lines("l01"), "v", 1.0, tmp_path, http=http)
    assert http.calls[0].get_header("Xi-api-key") == KEY


def test_missing_key_is_narration_error(tmp_path, monkeypatch):
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    with pytest.raises(contract.NarrationError):
        elevenlabs.generate(lines("l01"), "v", 1.0, tmp_path, http=fake_http([]))


def test_retry_once_on_429(tmp_path):
    http = fake_http([429, pcm(0.5)])
    d = elevenlabs.generate(lines("l01"), "v", 1.0, tmp_path, api_key=KEY, http=http)
    assert len(http.calls) == 2 and d == {"l01": 0.5}


def test_second_failure_raises_without_key(tmp_path):
    http = fake_http([500, 500])
    with pytest.raises(contract.NarrationError) as e:
        elevenlabs.generate(lines("l01"), "v", 1.0, tmp_path, api_key=KEY, http=http)
    assert KEY not in str(e.value) and "500" in str(e.value)


def test_existing_take_skipped(tmp_path):
    write_wav(tmp_path / "l01.wav", 1.0)
    http = fake_http([pcm(0.5)])
    d = elevenlabs.generate(lines("l01", "l02"), "v", 1.0, tmp_path, api_key=KEY, http=http)
    assert len(http.calls) == 1 and d == {"l01": 1.0, "l02": 0.5}


def test_list_voices():
    body = json.dumps({"voices": [{"voice_id": "a", "name": "A"}]}).encode()
    assert elevenlabs.list_voices(api_key=KEY, http=fake_http([body])) == [{"voice_id": "a", "name": "A"}]


def test_partial_failure_falls_back(tmp_path):
    short = make_short("l01", "l02")
    settings = {"narrator": {"provider": "elevenlabs", "voice": "v", "speed": 1.0}}
    http = fake_http([pcm(0.5), 400])

    def fake(lns, voice, speed, out):
        return elevenlabs.generate(lns, voice, speed, out, api_key=KEY, http=http)

    res = nrun.narrate(tmp_path, short, settings, elevenlabs=fake)
    assert res.status == "captions-only" and res.durations is None and res.message
    assert KEY not in res.message
    assert (tmp_path / "audio" / "vo" / "l01.wav").exists()
    rec = jobstate.load_state(tmp_path)["stages"]["narration"]
    assert rec["status"] == "captions-only" and rec["provider"] == "elevenlabs"


def test_elevenlabs_ok_path(tmp_path):
    short = make_short("l01")
    settings = {"narrator": {"provider": "elevenlabs", "voice": "v", "speed": 1.1}}
    seen = {}

    def fake(lns, voice, speed, out):
        seen.update(voice=voice, speed=speed)
        write_wav(out / "l01.wav")
        return {"l01": 1.0}

    res = nrun.narrate(tmp_path, short, settings, elevenlabs=fake)
    assert res.status == "ok" and res.durations == {"l01": 1.0} and res.message is None
    assert seen == {"voice": "v", "speed": 1.1}
    assert jobstate.load_state(tmp_path)["stages"]["narration"]["status"] == "ok"


@pytest.mark.parametrize("provider", [None, "none"])
def test_none_provider_is_captions_only(tmp_path, provider):
    res = nrun.narrate(tmp_path, make_short("l01"), {"narrator": {"provider": provider}})
    assert res.status == "captions-only" and res.durations is None and res.message is None
    assert jobstate.load_state(tmp_path)["stages"]["narration"]["status"] == "captions-only"


def completed(code, out="", err=""):
    return subprocess.CompletedProcess([], code, stdout=out, stderr=err)


def test_local_command_shape(tmp_path, bsa_home):
    seen = {}

    def run(cmd, **kw):
        seen["cmd"] = cmd
        write_wav(tmp_path / "l01.wav")
        (tmp_path / "durations.json").write_text(json.dumps({"l01": 1.0}))
        return completed(0, '{"ok": true}\n')

    d = local.generate("kokoro", lines("l01"), "af_heart", 1.0, tmp_path, run=run)
    cmd = seen["cmd"]
    assert d == {"l01": 1.0}
    assert cmd[:4] == ["uv", "run", "--project", str(bsa_home / "voices" / "runner")]
    assert cmd[4:7] == ["bsa-tts", "speak", "--model"] and cmd[7] == "kokoro"
    assert cmd[cmd.index("--voice") + 1] == "af_heart"
    assert cmd[cmd.index("--seed") + 1] == "7" and "--cpu-fallback" in cmd


def test_missing_local_engine_falls_back(tmp_path):
    fake_local = SimpleNamespace(
        generate=lambda *a, **k: local.generate(*a, run=lambda cmd, **kw: completed(5), **k),
        verify=local.verify,
    )
    settings = {"narrator": {"provider": "chatterbox", "voice": "v", "speed": 1.0}}
    res = nrun.narrate(tmp_path, make_short("l01"), settings, local=fake_local)
    assert res.status == "captions-only" and "not installed" in res.message and res.durations is None


@pytest.mark.parametrize("code", [3, 4])
def test_gpu_codes_raise(tmp_path, code):
    out = json.dumps({"message": "GPU is busy with another job"}) + "\n"
    with pytest.raises(contract.NarrationError, match="GPU"):
        local.generate("kokoro", lines("l01"), "v", 1.0, tmp_path, run=lambda c, **k: completed(code, out))


def test_verify_none_when_not_installed(tmp_path):
    assert local.verify(lines("l01"), tmp_path, run=lambda c, **k: completed(5)) is None


def test_verify_parses_wer(tmp_path):
    out = json.dumps({"l01": {"text": "x", "wer": 0.25}}) + "\n"
    assert local.verify(lines("l01"), tmp_path, run=lambda c, **k: completed(0, "noise\n" + out)) == {"l01": 0.25}


def test_bad_take_regenerated(tmp_path):
    short = make_short("l01", "l02")
    settings = {"narrator": {"provider": "kokoro", "voice": "v", "speed": 1.0}}
    gens, vers = [], []

    def gen(model, lns, voice, speed, out, seed=7):
        gens.append((model, [x.id for x in lns], seed))
        return {x.id: 1.0 for x in lns}

    def ver(lns, out):
        vers.append([x.id for x in lns])
        bad = len(vers) == 1
        return {x.id: (0.2 if bad and x.id == "l02" else 0.0) for x in lns}

    res = nrun.narrate(tmp_path, short, settings, local=SimpleNamespace(generate=gen, verify=ver))
    assert gens == [("kokoro", ["l01", "l02"], 7), ("kokoro", ["l02"], 8)]
    assert res.status == "ok" and res.message is None
    assert res.durations == {"l01": 1.0, "l02": 1.0}


def test_still_bad_after_retries_reported(tmp_path):
    short = make_short("l01")
    settings = {"narrator": {"provider": "kokoro", "voice": "v"}}
    seeds = []

    def gen(model, lns, voice, speed, out, seed=7):
        seeds.append(seed)
        return {x.id: 1.0 for x in lns}

    fake = SimpleNamespace(generate=gen, verify=lambda lns, out: {x.id: 0.5 for x in lns})
    res = nrun.narrate(tmp_path, short, settings, local=fake)
    assert seeds == [7, 8, 9]
    assert res.status == "ok" and "l01" in res.message


def test_verify_not_installed_or_disabled_skips(tmp_path):
    short = make_short("l01")
    gens = []

    def gen(model, lns, voice, speed, out, seed=7):
        gens.append(seed)
        return {"l01": 1.0}

    def must_not_call(lns, out):
        raise AssertionError("verify called")

    for verify_setting, ver in ((True, lambda lns, out: None), (False, must_not_call)):
        settings = {"narrator": {"provider": "kokoro", "voice": "v", "verify": verify_setting}}
        res = nrun.narrate(tmp_path, short, settings, local=SimpleNamespace(generate=gen, verify=ver))
        assert res.status == "ok"
    assert gens == [7, 7]


def _uv_exe_name():
    import os

    return "uv.exe" if os.name == "nt" else "uv"


def test_local_finds_uv_outside_path(tmp_path, bsa_home, monkeypatch):
    # Blender may not inherit the user's PATH: uv's installer puts it in ~/.local/bin.
    monkeypatch.undo()
    monkeypatch.setenv("BSA_HOME", str(bsa_home))
    home = tmp_path / "home"
    uv = home / ".local" / "bin" / _uv_exe_name()
    uv.parent.mkdir(parents=True)
    uv.write_bytes(b"")
    monkeypatch.setattr(install.shutil, "which", lambda name: None)
    monkeypatch.setattr(install.Path, "home", lambda: home)
    seen = {}

    def run(cmd, **kw):
        seen["cmd"] = cmd
        return completed(5)

    with pytest.raises(contract.NarrationError):
        local.generate("kokoro", lines("l01"), "af_heart", 1.0, tmp_path / "out", run=run)
    assert seen["cmd"][0] == str(uv)
    assert local.verify(lines("l01"), tmp_path / "out", run=run) is None
    assert seen["cmd"][0] == str(uv)


def test_local_without_uv_says_how_to_install(tmp_path, bsa_home, monkeypatch):
    monkeypatch.setattr(install, "uv_path", lambda: None)
    with pytest.raises(contract.NarrationError) as e:
        local.generate("kokoro", lines("l01"), "af_heart", 1.0, tmp_path, run=lambda c, **k: completed(0))
    assert "astral.sh/uv" in str(e.value)


def _take_short(text="Hello there."):
    return Short(slug="t", title="T", length_s=10, lines=[Line("l01", text, "s01"), Line("l02", "Bye now.", "s01")])


def test_clear_stale_takes_keeps_current_takes(tmp_path):
    vo = tmp_path / "vo"
    narrator = {"provider": "kokoro", "voice": "am_michael", "speed": 1.0}
    assert nrun.clear_stale_takes(vo, _take_short(), narrator) == []
    for lid in ("l01", "l02"):
        (vo / f"{lid}.wav").write_bytes(b"x")
    assert nrun.clear_stale_takes(vo, _take_short(), narrator) == []  # same voice, same text: resume
    assert nrun.clear_stale_takes(vo, _take_short("Changed line."), narrator) == ["l01"]
    assert (vo / "l02.wav").exists()
    assert nrun.clear_stale_takes(vo, _take_short("Changed line."), {**narrator, "voice": "af_heart"}) == ["l02"]


def test_takes_durations_only_after_a_voiced_narration(tmp_path):
    short = _take_short()
    assert nrun.takes_durations(tmp_path, short) is None
    jobstate.mark_done(tmp_path, "narration", status="captions-only")
    assert nrun.takes_durations(tmp_path, short) is None
    jobstate.mark_done(tmp_path, "narration", status="ok")
    with pytest.raises(contract.NarrationError, match="narrate SHORT"):
        nrun.takes_durations(tmp_path, short)


def test_elevenlabs_interrupted_write_leaves_no_take(tmp_path, monkeypatch):
    """A take is written to <id>.wav.part and renamed, so an interrupted write never passes for a finished take."""
    real = elevenlabs._write_wav

    def crash(path, data):
        real(path, data[: len(data) // 2])
        raise OSError("disk full")

    monkeypatch.setattr(elevenlabs, "_write_wav", crash)
    with pytest.raises(OSError):
        elevenlabs.generate(lines("l01"), "v", 1.0, tmp_path, api_key=KEY, http=fake_http([pcm(0.5)]))
    assert not (tmp_path / "l01.wav").exists()


@pytest.mark.parametrize("seconds", [0.0, None])
def test_elevenlabs_bad_existing_take_is_regenerated(tmp_path, seconds):
    if seconds is None:
        (tmp_path / "l01.wav").write_bytes(b"RIFF")  # a truncated file
    else:
        write_wav(tmp_path / "l01.wav", seconds)  # zero-length take
    http = fake_http([pcm(0.5), pcm(0.25)])
    d = elevenlabs.generate(lines("l01", "l02"), "v", 1.0, tmp_path, api_key=KEY, http=http)
    assert len(http.calls) == 2 and d == {"l01": 0.5, "l02": 0.25}
    assert not list(tmp_path.glob("*.part"))


def test_local_engine_resumes_only_missing_or_bad_takes(tmp_path):
    """Local engines regenerate only the lines without a good take on disk (as ElevenLabs does)."""
    short = make_short("l01", "l02", "l03")
    vo = tmp_path / "audio" / "vo"
    write_wav(vo / "l01.wav", 1.25)  # a good take from an interrupted run: kept
    write_wav(vo / "l03.wav", 0.0)  # an empty one: remade
    gens = []

    def gen(model, lns, voice, speed, out, seed=7):
        gens.append([x.id for x in lns])
        return {x.id: 2.0 for x in lns}

    settings = {"narrator": {"provider": "kokoro", "voice": "v", "speed": 1.0, "verify": False}}
    res = nrun.narrate(tmp_path, short, settings, local=SimpleNamespace(generate=gen, verify=lambda *a: None))
    assert gens == [["l02", "l03"]]
    assert res.status == "ok" and res.durations == {"l01": 1.25, "l02": 2.0, "l03": 2.0}
