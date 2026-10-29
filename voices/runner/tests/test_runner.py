import hashlib
import io
import json
import re
import subprocess
from pathlib import Path

import pytest
from bsa_tts import cli, gpu


@pytest.fixture
def root(tmp_path, monkeypatch):
    r = tmp_path / "voices"
    r.mkdir()
    monkeypatch.setenv("BSA_TTS_ROOT", str(r))
    return r


def fake_install(root: Path, model: str) -> None:
    m = cli.load_registry()["models"][model]
    py = cli.engine_python(root / "engines" / m["env"])
    py.parent.mkdir(parents=True, exist_ok=True)
    py.write_bytes(b"")
    w = root / "weights" / model
    w.mkdir(parents=True, exist_ok=True)
    (w / cli.COMPLETE).write_text("ok", encoding="utf-8")


def lines_file(tmp_path: Path, items) -> Path:
    p = tmp_path / "lines.json"
    p.write_text(json.dumps(items), encoding="utf-8")
    return p


def run(capsys, *argv):
    code = cli.main(list(argv))
    out = capsys.readouterr().out.strip().splitlines()
    return code, json.loads(out[-1])


def speak(tmp_path, capsys, model, *extra, items=None):
    items = items or [{"id": "line1", "text": "Hello there."}]
    return run(
        capsys, "speak", "--model", model, "--lines", str(lines_file(tmp_path, items)), "--out", str(tmp_path / "out"),
        *extra,
    )  # fmt: skip


def test_lines_validation(root, tmp_path, capsys):
    fake_install(root, "kokoro")
    items = [{"id": "a", "text": "One."}, {"id": "a", "text": "Two."}]
    code, summary = speak(tmp_path, capsys, "kokoro", "--cpu", items=items)
    assert code == 2
    assert "duplicate" in summary["message"]


def test_unknown_model_exit_2(root, tmp_path, capsys):
    code, summary = speak(tmp_path, capsys, "tacotron")
    assert code == 2
    assert "tacotron" in summary["message"]


def test_not_installed_exit_5(root, tmp_path, capsys):
    code, summary = speak(tmp_path, capsys, "kokoro", "--voice", "am_michael")
    assert code == 5
    assert "not installed" in summary["message"]


def test_gpu_busy_exit_3(root, tmp_path, capsys, monkeypatch):
    fake_install(root, "chatterbox")

    def fake_smi(cmd, **kwargs):
        assert cmd[0] == "nvidia-smi"
        return subprocess.CompletedProcess(cmd, 0, stdout="16303, 15803\n", stderr="")

    monkeypatch.setattr(gpu, "which", lambda name: name)
    monkeypatch.setattr(gpu, "_run", fake_smi)
    code, summary = speak(tmp_path, capsys, "chatterbox", "--voice", "default", "--cpu-fallback")
    assert code == 3
    assert "500 MB" in summary["message"]
    assert "4000 MB" in summary["message"]


def test_no_nvidia_gpu(monkeypatch):
    monkeypatch.setattr(gpu, "which", lambda name: None)
    state = gpu.query()
    assert not state.available
    assert state.free_mb == 0


def test_portable_python_path():
    env = Path("engines") / "kokoro"
    assert cli.engine_python(env, os_name="nt") == env / ".venv" / "Scripts" / "python.exe"
    assert cli.engine_python(env, os_name="posix") == env / ".venv" / "bin" / "python"


def test_verify_not_installed_exit_5(root, tmp_path, capsys):
    items = [{"id": "line1", "text": "Hello there."}]
    code = cli.main(["verify", "--lines", str(lines_file(tmp_path, items)), "--takes", str(tmp_path)])
    assert code == 5


def test_wer_normalises_numbers_and_fillers():
    ref = "Inside this skyscraper hangs a six-hundred-and-sixty-tonne steel ball."
    assert cli.wer(ref, "Inside this skyscraper hangs a 660-ton steel ball.") == 0.0
    assert cli.wer("one two three four", "one two three") == 0.25


def test_registry_shape():
    reg = cli.load_registry()
    for name, m in reg["models"].items():
        assert m["licence"] in ("Apache-2.0", "MIT"), name
        w = m["weights"]
        assert w["repo"] and w["files"] and w["bytes"] == sum(f["bytes"] for f in w["files"].values()), name
        assert re.fullmatch(r"[0-9a-f]{40}", w["revision"]), f"{name}: revision must be a pinned commit"
        for file, meta in w["files"].items():
            assert re.fullmatch(r"[0-9a-f]{64}", meta["sha256"]), f"{name}/{file}"
        assert isinstance(m["min_free_vram_mb"], int), name
    assert {"michael", "george", "heart", "emma", "me", "default"} <= set(reg["models"]["chatterbox"]["voices"])
    assert reg["ref_script"]


class _Response(io.BytesIO):
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def _serve(monkeypatch, payload: bytes):
    monkeypatch.setattr(cli.urllib.request, "urlopen", lambda req, timeout=None: _Response(payload))


def test_download_rejects_sha_mismatch(tmp_path, monkeypatch):
    _serve(monkeypatch, b"tampered")
    dest = tmp_path / "model.bin"
    expected = hashlib.sha256(b"original").hexdigest()
    with pytest.raises(cli.CliError) as e:
        cli._download("https://example.invalid/model.bin", dest, 8, expected)
    assert e.value.code == 1
    assert "sha256" in str(e.value)
    assert not dest.exists()
    assert not dest.with_name(dest.name + ".part").exists()


def test_download_accepts_matching_sha(tmp_path, monkeypatch):
    _serve(monkeypatch, b"original")
    dest = tmp_path / "model.bin"
    cli._download("https://example.invalid/model.bin", dest, 8, hashlib.sha256(b"original").hexdigest())
    assert dest.read_bytes() == b"original"


def test_fetch_rejects_tampered_existing_file(root, capsys, monkeypatch):
    # A file of the right size but the wrong content is downloaded again, not trusted.
    reg = cli.load_registry()
    w = reg["models"]["kokoro"]["weights"]
    first, meta = next(iter(w["files"].items()))
    target = root / "weights" / "kokoro" / first
    target.parent.mkdir(parents=True)
    target.write_bytes(b"x" * meta["bytes"])
    seen = []

    def fake_download(url, dest, size, sha256):
        seen.append(dest.name)
        raise cli.CliError("stop", 1)

    monkeypatch.setattr(cli, "_download", fake_download)
    code, _ = run(capsys, "fetch", "--model", "kokoro")
    assert code == 1
    assert seen == [Path(first).name]
