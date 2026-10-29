"""Install the free local voices into <bsa_home>/voices: the bsa-tts runner, one uv project per engine, the weights
(from the official Hugging Face repos) and the reference clips Chatterbox clones its voices from.

Nothing downloads until the user has said yes to `download_plan()`. The engines need uv (astral.sh/uv); everything
here is stdlib so it runs in Blender's Python.
"""

from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import tempfile
from pathlib import Path

from bsa.machine import Machine
from bsa.narrate.contract import NarrationError
from bsa.settings import bsa_home
from bsa.short import Line

PLUGIN_VOICES = Path(__file__).resolve().parents[2] / "voices"
REGISTRY = PLUGIN_VOICES / "runner" / "bsa_tts" / "registry.json"
VOICE_MODELS = ("kokoro", "chatterbox", "chatterbox-turbo")
SUGGEST_GPU_MB = 4000
# Windows can't load a DLL whose path passes 260 characters, and the engines' deepest files sit ~110 characters
# below the voices folder, so that folder must stay short there.
WINDOWS_MAX_VOICES_PATH = 140
SAMPLE_TEXT = "Inside this skyscraper hangs a six-hundred-and-sixty-tonne steel ball."
_COPY_IGNORE = shutil.ignore_patterns(".venv", "__pycache__", "tests", ".pytest_cache", ".ruff_cache", "*.pyc")


def _registry() -> dict:
    return json.loads(REGISTRY.read_text(encoding="utf-8"))


def voices_dir() -> Path:
    return bsa_home() / "voices"


def _engine_python(env_dir: Path) -> Path:
    if os.name == "nt":
        return env_dir / ".venv" / "Scripts" / "python.exe"
    return env_dir / ".venv" / "bin" / "python"


def uv_path() -> Path | None:
    """uv on PATH, or in the folders its installer uses (Blender may not see the user's PATH)."""
    found = shutil.which("uv")
    if found:
        return Path(found)
    exe = "uv.exe" if os.name == "nt" else "uv"
    for folder in (Path.home() / ".local" / "bin", Path.home() / ".cargo" / "bin"):
        if (folder / exe).is_file():
            return folder / exe
    return None


def uv_install_hint() -> str:
    if platform.system() == "Windows":
        return 'powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"'
    return "curl -LsSf https://astral.sh/uv/install.sh | sh"


def installed() -> dict[str, bool]:
    """Per model in the registry (the three voices and the Whisper check): engine environment and weights present."""
    voices = voices_dir()
    return {
        name: _engine_python(voices / "engines" / m["env"]).is_file()
        and (voices / "weights" / name / ".complete").is_file()
        for name, m in _registry()["models"].items()
    }


def _with_dependencies(models: list[str]) -> list[str]:
    """The models to install, in order: Kokoro first when a Chatterbox voice needs its reference clips."""
    reg = _registry()
    for m in models:
        if m not in reg["models"]:
            raise NarrationError(f"unknown voice model {m!r}; have: {', '.join(reg['models'])}")
    needed = list(dict.fromkeys(models))
    clones = any(reg["models"][m]["engine"] == "chatterbox" for m in needed)
    if clones and "kokoro" not in needed and not installed().get("kokoro"):
        needed.insert(0, "kokoro")
    order = list(reg["models"])
    return sorted(needed, key=order.index)


def _needs_python() -> bool:
    """True when uv would have to download Python 3.12 for the engine environments."""
    uv = uv_path()
    if uv is None:
        return True
    try:
        proc = subprocess.run(
            [str(uv), "python", "find", "3.12"], check=False, capture_output=True, text=True, timeout=30
        )
    except (OSError, subprocess.SubprocessError):
        return True
    return proc.returncode != 0


def download_plan(models: list[str]) -> list[dict]:
    """Everything install() would download, for the user to approve first: each model's weights (Hugging Face repo,
    bytes, licence), then each uv environment not yet set up (with its measured size on disk) and, when uv has to
    fetch it, Python 3.12."""
    reg = _registry()
    needed = _with_dependencies(models)
    plan = []
    for name in needed:
        m = reg["models"][name]
        plan.append(
            {"model": name, "repo": m["weights"]["repo"], "bytes": m["weights"]["bytes"], "licence": m["licence"]}
        )
    voices = voices_dir()
    envs = reg["environments"]
    missing = [
        env
        for env, folder in [("runner", voices / "runner")]
        + [(e, voices / "engines" / e) for e in dict.fromkeys(reg["models"][m]["env"] for m in needed)]
        if not _engine_python(folder).is_file()
    ]
    for env in missing:
        e = envs[env]
        plan.append({"model": f"env:{env}", "repo": e["repo"], "bytes": e["bytes"], "licence": e["licence"]})
    if any(env != "runner" for env in missing) and _needs_python():
        e = envs["python-3.12"]
        plan.append({"model": "python-3.12", "repo": e["repo"], "bytes": e["bytes"], "licence": e["licence"]})
    return plan


def _step(run, cmd: list[str], what: str) -> None:
    proc = run(cmd, check=False)
    if proc.returncode != 0:
        raise NarrationError(f"{what} failed (exit {proc.returncode}); see the output above")


def _copy(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(src, dst, ignore=_COPY_IGNORE, dirs_exist_ok=True)


def _make_refs(uv: str, runner: Path, run) -> None:
    """Render Chatterbox's reference clips with Kokoro, on this machine, from the fixed script in the registry."""
    reg = _registry()
    refs = voices_dir() / "refs"
    refs.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="bsa-refs-") as tmp:
        script = Path(tmp) / "ref_script.json"
        script.write_text(json.dumps(reg["ref_script"]), encoding="utf-8")
        ref_id = reg["ref_script"][0]["id"]
        for filename, kokoro_voice in reg["clone_refs"].items():
            if (refs / filename).is_file():
                continue
            out = Path(tmp) / kokoro_voice
            cmd = [uv, "run", "--project", str(runner), "bsa-tts", "speak", "--model", "kokoro"]
            cmd += ["--voice", kokoro_voice, "--lines", str(script), "--out", str(out), "--cpu-fallback"]
            _step(run, cmd, f"making the {kokoro_voice} reference clip")
            shutil.copyfile(out / f"{ref_id}.wav", refs / filename)


def install(models: list[str], run=subprocess.run) -> Path:
    """Copy the runner and the needed engines to <bsa_home>/voices, `uv sync` each, download the weights and make the
    Chatterbox reference clips. Returns the runner's folder. Call only after the user approved download_plan()."""
    uv_exe = uv_path()
    if uv_exe is None:
        raise NarrationError(
            "The free voices need uv, a small Python tool installer (https://astral.sh/uv). Install it with: "
            f"{uv_install_hint()} , then try again."
        )
    uv = str(uv_exe)
    reg = _registry()
    needed = _with_dependencies(models)
    voices = voices_dir()
    if os.name == "nt" and len(str(voices)) > WINDOWS_MAX_VOICES_PATH:
        raise NarrationError(
            f"The voices folder {voices} is too long a path for Windows to load the voice engines from. "
            "Set the BSA_HOME environment variable to a short folder (for example C:\\bsa) and try again."
        )
    runner = voices / "runner"
    _copy(PLUGIN_VOICES / "runner", runner)
    shutil.copyfile(PLUGIN_VOICES / "README.md", voices / "README.md")
    _step(run, [uv, "sync", "--locked", "--project", str(runner)], "setting up the voice runner (uv sync)")
    for env in dict.fromkeys(reg["models"][m]["env"] for m in needed):
        project = voices / "engines" / env
        _copy(PLUGIN_VOICES / "engines" / env, project)
        _step(run, [uv, "sync", "--locked", "--project", str(project)], f"setting up the {env} engine (uv sync)")
    for name in needed:
        _step(run, [uv, "run", "--project", str(runner), "bsa-tts", "fetch", "--model", name], f"downloading {name}")
    if any(reg["models"][m]["engine"] == "chatterbox" for m in needed):
        _make_refs(uv, runner, run)
    return runner


def suggest(m: Machine) -> dict:
    """Which free voice suits this machine. All three are listed; `fits` is False (with the reason) when one can't run."""
    reg = _registry()
    nvidia = [g for g in m.gpus if g.vendor == "nvidia" and g.free_gb is not None]
    free_mb = max((round(g.free_gb * 1024) for g in nvidia), default=None)
    options = []
    for name in VOICE_MODELS:
        model = reg["models"][name]
        need = int(model["min_free_vram_mb"])
        gb = round(sum(p["bytes"] for p in download_plan([name])) / 1e9, 2)
        if "cpu" in model["devices"]:
            fits, why = True, "light and quick, runs on any computer (the CPU is fine), clear but a little flat"
        elif free_mb is None:
            fits, why = (
                False,
                f"needs an NVIDIA graphics card with about {need / 1000:.1f} GB free; this computer has none",
            )
        elif free_mb < need:
            fits = False
            why = (f"needs about {need / 1000:.1f} GB of free video memory, but only {free_mb / 1000:.1f} GB is free "
                   "right now (other programs are using the rest)")  # fmt: skip
        else:
            fits = True
            why = "the most natural voice" if name == "chatterbox" else "faster, nearly as good"
        options.append({"model": name, "why": why, "download_gb": gb, "fits": fits})
    suggested = "chatterbox" if free_mb is not None and free_mb >= SUGGEST_GPU_MB else "kokoro"
    return {"suggested": suggested, "options": options}


def sample(model: str, voice: str, run=subprocess.run) -> Path:
    """Render one short test line with an installed voice so the user can listen before choosing."""
    from bsa.narrate import local

    out = voices_dir() / "samples" / f"{model}-{voice}"
    local.generate(model, [Line("sample", SAMPLE_TEXT, "sample")], voice, 1.0, out, run=run)
    return out / "sample.wav"
