import json
import re
import subprocess
from pathlib import Path

import pytest

from bsa.machine import Gpu, Machine
from bsa.narrate import install
from bsa.narrate.contract import NarrationError

ROOT = Path(__file__).resolve().parent.parent
REGISTRY = json.loads((ROOT / "voices" / "runner" / "bsa_tts" / "registry.json").read_text(encoding="utf-8"))


def _machine(gpus):
    return Machine("Windows", "cpu", 8, 32.0, gpus)


def test_suggest_gpu():
    # 16303 MiB total, 4303 MiB in use: about 12000 MB free.
    s = install.suggest(_machine([Gpu("NVIDIA GeForce RTX 4070 Ti", "nvidia", 15.9, 4.2)]))
    assert s["suggested"] == "chatterbox"
    by_model = {o["model"]: o for o in s["options"]}
    assert set(by_model) == {"kokoro", "chatterbox", "chatterbox-turbo"}
    assert all(o["fits"] for o in s["options"])
    assert "faster, nearly as good" in by_model["chatterbox-turbo"]["why"]
    assert all(o["download_gb"] > 0 for o in s["options"])


def test_suggest_cpu_only():
    s = install.suggest(_machine([]))
    assert s["suggested"] == "kokoro"
    by_model = {o["model"]: o for o in s["options"]}
    assert set(by_model) == {"kokoro", "chatterbox", "chatterbox-turbo"}
    assert by_model["kokoro"]["fits"]
    for name in ("chatterbox", "chatterbox-turbo"):
        assert by_model[name]["fits"] is False
        assert "NVIDIA" in by_model[name]["why"]


def test_suggest_mac_and_busy_gpu():
    mac = Machine("Darwin", "arm", 8, 16.0, [Gpu("Apple M2", "apple", None, None)])
    assert install.suggest(mac)["suggested"] == "kokoro"
    busy = install.suggest(_machine([Gpu("RTX", "nvidia", 15.9, 14.0)]))
    assert busy["suggested"] == "kokoro"
    cb = next(o for o in busy["options"] if o["model"] == "chatterbox")
    assert cb["fits"] is False
    assert "free" in cb["why"]


@pytest.fixture(autouse=True)
def _python_present(monkeypatch):
    """Whether uv must fetch Python 3.12 depends on this machine; default to "no" unless a test says otherwise."""
    monkeypatch.setattr(install, "_needs_python", lambda: False)


def _weights(plan):
    return [p for p in plan if p["model"] in REGISTRY["models"]]


def test_download_plan_sizes_and_licences(bsa_home):
    plan = _weights(install.download_plan(["kokoro", "chatterbox"]))
    assert [p["model"] for p in plan] == ["kokoro", "chatterbox"]
    assert [p["licence"] for p in plan] == ["Apache-2.0", "MIT"]
    for p in plan:
        assert p["bytes"] == REGISTRY["models"][p["model"]]["weights"]["bytes"]
        assert p["repo"] == REGISTRY["models"][p["model"]]["weights"]["repo"]


def test_download_plan_adds_kokoro_for_reference_clips(bsa_home):
    plan = _weights(install.download_plan(["chatterbox-turbo"]))
    assert [p["model"] for p in plan] == ["kokoro", "chatterbox-turbo"]


def test_download_plan_lists_environments(bsa_home, monkeypatch):
    monkeypatch.setattr(install, "_needs_python", lambda: True)
    plan = install.download_plan(["chatterbox"])
    envs = REGISTRY["environments"]
    by_model = {p["model"]: p for p in plan}
    assert set(by_model) == {"kokoro", "chatterbox", "env:runner", "env:kokoro", "env:chatterbox", "python-3.12"}
    for name in ("runner", "kokoro", "chatterbox"):
        entry = by_model[f"env:{name}"]
        assert entry["bytes"] == envs[name]["bytes"] > 0
        assert entry["repo"] and entry["licence"]
    assert by_model["python-3.12"]["bytes"] == envs["python-3.12"]["bytes"]
    total = sum(p["bytes"] for p in plan)
    weights = REGISTRY["models"]["kokoro"]["weights"]["bytes"] + REGISTRY["models"]["chatterbox"]["weights"]["bytes"]
    assert total == weights + sum(envs[n]["bytes"] for n in ("runner", "kokoro", "chatterbox", "python-3.12"))


def test_download_plan_skips_ready_environments(bsa_home, monkeypatch):
    monkeypatch.setattr(install, "_needs_python", lambda: True)
    for env in ("runner", "engines/kokoro"):
        py = install._engine_python(bsa_home / "voices" / env)
        py.parent.mkdir(parents=True)
        py.write_bytes(b"")
    models = [p["model"] for p in install.download_plan(["kokoro"])]
    assert models == ["kokoro"]


def test_suggest_download_includes_environments(bsa_home):
    s = install.suggest(_machine([]))
    for o in s["options"]:
        plan = install.download_plan([o["model"]])
        assert o["download_gb"] == round(sum(p["bytes"] for p in plan) / 1e9, 2)
    kokoro = next(o for o in s["options"] if o["model"] == "kokoro")
    assert kokoro["download_gb"] > 5  # the Kokoro weights are 0.34 GB; its environment is most of it


def _fake_run(calls):
    def run(cmd, **kwargs):
        assert isinstance(cmd, list)
        calls.append(cmd)
        if "speak" in cmd:
            out = Path(cmd[cmd.index("--out") + 1])
            out.mkdir(parents=True, exist_ok=True)
            (out / "ref.wav").write_bytes(b"RIFF")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    return run


def test_install_copies_and_syncs(bsa_home, monkeypatch):
    monkeypatch.setattr(install, "uv_path", lambda: Path("uv"))
    calls = []
    runner = install.install(["chatterbox"], run=_fake_run(calls))
    voices = bsa_home / "voices"
    assert runner == voices / "runner"
    assert (runner / "pyproject.toml").is_file()
    assert (runner / "bsa_tts" / "registry.json").is_file()
    assert not (runner / "tests").exists()
    assert (voices / "engines" / "kokoro" / "pyproject.toml").is_file()
    assert (voices / "engines" / "chatterbox" / "pyproject.toml").is_file()
    assert not (voices / "engines" / "whisper").exists()

    syncs = [c for c in calls if c[1] == "sync"]
    assert all("--locked" in c for c in syncs)
    synced = {Path(c[c.index("--project") + 1]) for c in syncs}
    assert synced == {runner, voices / "engines" / "kokoro", voices / "engines" / "chatterbox"}
    fetched = [c[c.index("--model") + 1] for c in calls if "fetch" in c]
    assert fetched == ["kokoro", "chatterbox"]
    for kid in ("am_michael", "bm_george", "af_heart", "bf_emma"):
        assert (voices / "refs" / f"kokoro_{kid}.wav").is_file()

    banned = "Local" + "AI"
    assert not any(banned in str(part) for c in calls for part in c)
    for f in voices.rglob("*"):
        if f.is_file() and f.suffix in (".py", ".toml", ".json", ".md", ".lock"):
            assert banned not in f.read_text(encoding="utf-8", errors="replace"), f


def test_install_step_failure_raises(bsa_home, monkeypatch):
    monkeypatch.setattr(install, "uv_path", lambda: Path("uv"))

    def run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="boom")

    with pytest.raises(NarrationError):
        install.install(["kokoro"], run=run)


def test_missing_uv_message(bsa_home, monkeypatch):
    monkeypatch.setattr(install, "uv_path", lambda: None)
    with pytest.raises(NarrationError) as e:
        install.install(["kokoro"])
    assert "astral.sh/uv" in str(e.value)


def test_installed_reports_each_model(bsa_home):
    status = install.installed()
    assert set(status) == set(REGISTRY["models"])
    assert not any(status.values())


def test_no_personal_paths():
    """Nothing in the repo points at one person's machine: no absolute paths on another drive (Z: is the tests' made-up drive), and no user folder
    other than the `you` placeholder."""
    banned = [re.compile(r"\b[D-Y]:[\/]"), re.compile(r"Users[\/](?!you\b)[A-Za-z]\w*[\/]")]
    files = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
        cwd=ROOT, check=True, capture_output=True, text=True,
    ).stdout.splitlines()  # fmt: skip
    hits = []
    for name in files:
        path = ROOT / name
        if name.endswith((".lock", ".gif", ".jpg", ".png")) or not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        hits += [f"{name}: {m.group(0)}" for pat in banned for m in pat.finditer(text)]
    assert hits == []


@pytest.mark.slow
def test_kokoro_end_to_end(bsa_home, tmp_path):
    from bsa.narrate import local
    from bsa.narrate.contract import check_take
    from bsa.short import Line

    if install.uv_path() is None:
        pytest.skip("uv not installed")
    install.install(["kokoro"])
    lines = [
        Line("line1", "Inside this skyscraper hangs a six-hundred-and-sixty-tonne steel ball.", "s1"),
        Line("line2", "As the tower sways, the ball lags behind and pulls it back.", "s1"),
    ]
    out = tmp_path / "takes"
    durations = local.generate("kokoro", lines, "am_michael", 1.0, out)
    assert set(durations) == {"line1", "line2"}
    for ln in lines:
        assert check_take(out / f"{ln.id}.wav") == []


def test_long_windows_path_refused(tmp_path, monkeypatch):
    monkeypatch.setenv("BSA_HOME", str(tmp_path / ("x" * 150)))
    monkeypatch.setattr(install, "uv_path", lambda: Path("uv"))
    monkeypatch.setattr(install.os, "name", "nt")
    with pytest.raises(NarrationError) as e:
        install.install(["kokoro"], run=_fake_run([]))
    assert "BSA_HOME" in str(e.value)
