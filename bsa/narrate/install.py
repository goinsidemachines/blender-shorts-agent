"""Install the free local voices into <bsa_home>/voices: the bsa-tts runner, one uv project per engine, the weights
(from the official Hugging Face repos) and the reference clips Chatterbox clones its voices from.

Nothing downloads until the user has said yes to `download_plan()`. The engines need uv (astral.sh/uv); everything
here is stdlib so it runs in Blender's Python.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from bsa import proc as _proc
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


UV_INSTALL_PAGE = "https://docs.astral.sh/uv/getting-started/installation/"


def uv_install_hint() -> str:
    """How to install uv with the system's own package manager, never a downloaded script piped into a shell."""
    system = platform.system()
    if system == "Windows":
        return f"winget install --id=astral-sh.uv -e (or see {UV_INSTALL_PAGE})"
    if system == "Darwin":
        return f"brew install uv (or see {UV_INSTALL_PAGE})"
    return f"your package manager, or the steps at {UV_INSTALL_PAGE}"


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
        proc = _proc.run(
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


def install(models: list[str], run=_proc.run) -> Path:
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


def sample(model: str, voice: str, run=_proc.run, params: dict | None = None) -> Path:
    """Render one short test line with an installed voice so the user can listen before choosing; `params`
    (narrator.params) give the engine's delivery as narration would."""
    from bsa.narrate import local

    out = voices_dir() / "samples" / f"{model}-{voice}"
    local.generate(model, [Line("sample", SAMPLE_TEXT, "sample")], voice, 1.0, out, run=run, params=params or None)
    return out / "sample.wav"


# --- use what the user already has: weights and a Python environment, linked in, nothing downloaded -------------------

LOCAL_FILE = ".local.json"  # what a model's weights (or an engine environment) were linked from
EXACT_PACKAGES = ("torch", "torchaudio", "transformers")  # these must match the engine's pin exactly
_MODULES = {"chatterbox-tts": "chatterbox", "misaki": "misaki", "soundfile": "soundfile"}  # import name by package
_HASH_CHUNK = 1 << 24
_BIG_FILE = 50_000_000  # hashing a file this large prints a progress line
_PROBE = (
    "import importlib, importlib.metadata as m, json, sys\n"
    "out = {'prefix': sys.prefix, 'base': sys.base_prefix, 'version': list(sys.version_info[:3]), 'found': {}}\n"
    "for dist, mod in json.loads(sys.argv[1]).items():\n"
    "    try:\n"
    "        importlib.import_module(mod)\n"
    "        out['found'][dist] = m.version(dist)\n"
    "    except Exception:\n"
    "        out['found'][dist] = None\n"
    "print(json.dumps(out))\n"
)


def _progress(msg: str) -> None:
    print(f"[voice use-local] {msg}", file=sys.stderr, flush=True)


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(_HASH_CHUNK):
            h.update(chunk)
    return h.hexdigest()


def check_weights(model: str, folder: Path) -> list[str]:
    """What is wrong with `folder` as the weights of a registry model ([] when every listed file is there with the
    right size and sha256). Reads the files, writes nothing."""
    files = _registry()["models"][model]["weights"]["files"]
    bad = []
    for name, meta in files.items():
        path = folder / name
        if not path.is_file():
            bad.append(f"{name} is missing")
        elif path.stat().st_size != meta["bytes"]:
            bad.append(f"{name} is {path.stat().st_size} bytes, expected {meta['bytes']}")
        else:
            if meta["bytes"] >= _BIG_FILE:
                _progress(f"checking {name} ({meta['bytes'] / 1e6:.0f} MB)")
            if _sha256(path) != meta["sha256"]:
                bad.append(f"{name} has the wrong sha256 (not this model's file)")
    return bad


def _pins(env: str) -> dict[str, str]:
    """{package: pinned version} for the `==` pins in the engine's pyproject (extras and markers stripped)."""
    import tomllib

    text = (PLUGIN_VOICES / "engines" / env / "pyproject.toml").read_text(encoding="utf-8")
    pins = {}
    for dep in tomllib.loads(text)["project"]["dependencies"]:
        name = re.split(r"[\[=<>!~ ;]", dep, maxsplit=1)[0]
        pinned = re.search(r"==\s*([^\s;,]+)", dep)
        pins[name] = pinned.group(1) if pinned else ""
    return pins


def check_python(env: str, python: Path, run=_proc.run) -> tuple[Path, list[str]]:
    """(the environment's root, what differs from the engine's pinned packages) for an existing Python. Runs the
    Python once to import each package and read its version; exact for EXACT_PACKAGES (a `+cu128` suffix is
    ignored), importable for the rest."""
    pins = _pins(env)
    modules = {name: _MODULES.get(name, name.replace("-", "_")) for name in pins}
    try:
        proc = run([str(python), "-B", "-c", _PROBE, json.dumps(modules)], check=False, capture_output=True, text=True,
                   timeout=300)  # fmt: skip
        info = json.loads((proc.stdout or "").strip().splitlines()[-1])
    except (OSError, subprocess.SubprocessError, ValueError, IndexError) as exc:
        raise NarrationError(f"couldn't run {python} to check its packages: {exc}") from exc
    if proc.returncode != 0:
        raise NarrationError(f"couldn't run {python} to check its packages (exit {proc.returncode})")
    if info["prefix"] == info["base"]:
        raise NarrationError(
            f"{python} is not inside a virtual environment, so it can't be linked. Pass the python of a venv "
            "(for example <env folder>/bin/python)."
        )
    differs = []
    for name, want in pins.items():
        got = info["found"].get(name)
        if got is None:
            differs.append(f"{name} is not installed (needs {want or 'any version'})")
        elif name in EXACT_PACKAGES and want and got.split("+")[0] != want:
            differs.append(f"{name} is {got}, needs {want}")
    return Path(info["prefix"]), differs


def _link_file(src: Path, dst: Path) -> str:
    """Link src at dst: a symlink, else a hard link, else a copy (Windows without the symlink right). Returns which."""
    dst.unlink(missing_ok=True)
    for how, make in (("symlink", lambda: os.symlink(src, dst)), ("hard link", lambda: os.link(src, dst))):
        try:
            make()
            return how
        except (OSError, NotImplementedError):
            dst.unlink(missing_ok=True)
    shutil.copyfile(src, dst)
    return "copy"


def _link_dir(src: Path, dst: Path) -> str:
    """Make the directory dst point at the directory src (a symlink; a junction on Windows where symlinks aren't
    allowed)."""
    try:
        os.symlink(src, dst, target_is_directory=True)
        return "symlink"
    except (OSError, NotImplementedError) as exc:
        if os.name == "nt":
            proc = _proc.run(["cmd", "/c", "mklink", "/J", str(dst), str(src)], check=False, capture_output=True)
            if proc.returncode == 0:
                return "junction"
        raise NarrationError(f"couldn't link {dst} to {src}: {exc}") from exc


def _write_local(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, indent=1), encoding="utf-8")


def use_local(model: str, weights: Path, python: Path | None = None, run=_proc.run) -> dict:
    """Make an existing model folder (and, with `python`, an existing environment) count as installed. Nothing is
    downloaded and the user's folders are never changed: each weights file is checked (size and sha256) and linked in,
    the environment's folder is linked as the engine's `.venv`, and the runner is copied. Refuses, naming the
    problem, before it links anything."""
    reg = _registry()
    if model not in reg["models"]:
        raise NarrationError(f"unknown voice model {model!r}; have: {', '.join(reg['models'])}")
    m = reg["models"][model]
    weights = Path(weights).expanduser().resolve()
    if not weights.is_dir():
        raise NarrationError(f"{weights} is not a folder")
    voices = voices_dir()
    env_dir = voices / "engines" / m["env"]
    venv = env_dir / ".venv"
    prefix = None
    if python is not None:
        prefix, differs = check_python(m["env"], Path(python).expanduser(), run=run)
        if differs:
            raise NarrationError(f"{python} doesn't match the {m['env']} engine: " + "; ".join(differs))
        if venv.exists() and not venv.is_symlink():
            raise NarrationError(
                f"{venv} is already an environment of its own (installed by CLI voice install); "
                "use it, or remove that folder first."
            )
    elif not _engine_python(env_dir).is_file():
        raise NarrationError(
            f"the {m['env']} engine has no environment yet: pass --python PYTHON (an existing environment "
            "with the engine's packages) or run CLI voice install."
        )
    bad = check_weights(model, weights)
    if bad:
        raise NarrationError(f"{weights} is not the {model} weights: " + "; ".join(bad))
    dest = voices / "weights" / model
    dest.mkdir(parents=True, exist_ok=True)
    hows = {}
    for name in m["weights"]["files"]:
        target = dest / name
        size = m["weights"]["files"][name]["bytes"]
        if target.is_file() and not target.is_symlink() and target.stat().st_size == size:
            hows[name] = "kept"  # already a real file of the right size: never replace it with a link
            continue
        hows[name] = _link_file(weights / name, target)
    _write_local(dest / LOCAL_FILE, {"weights_from": str(weights), "files": hows})
    (dest / ".complete").write_text(
        json.dumps({"repo": m["weights"]["repo"], "revision": m["weights"]["revision"]}), encoding="utf-8"
    )
    out: dict = {"model": model, "weights_from": str(weights), "linked_how": sorted(set(hows.values()))}
    if prefix is not None:
        _copy(PLUGIN_VOICES / "engines" / m["env"], env_dir)
        if venv.is_symlink():
            venv.unlink()
        _link_dir(prefix, venv)
        _write_local(env_dir / LOCAL_FILE, {"python_from": str(prefix)})
        out["python_from"] = str(prefix)
    runner = voices / "runner"
    _copy(PLUGIN_VOICES / "runner", runner)
    shutil.copyfile(PLUGIN_VOICES / "README.md", voices / "README.md")
    out["runner_ready"] = _sync_runner_offline(runner, run)
    out["installed"] = installed()
    return out


def _sync_runner_offline(runner: Path, run) -> bool:
    """Set up the runner's own small environment from uv's cache only (never the network). False when that isn't
    possible (no uv, or the packages aren't cached): the next voice command then lets uv set it up."""
    uv = uv_path()
    if uv is None:
        return False
    try:
        proc = run([str(uv), "sync", "--locked", "--offline", "--project", str(runner)], check=False,
                   capture_output=True, text=True, timeout=300)  # fmt: skip
    except (OSError, subprocess.SubprocessError):
        return False
    return proc.returncode == 0


def local_links() -> dict[str, dict]:
    """{model: {"weights_from", "python_from"}} for the models whose weights (or environment) were linked from the
    user's own folders by use_local."""
    out = {}
    voices = voices_dir()
    for name, m in _registry()["models"].items():
        info = {}
        for path in (voices / "weights" / name / LOCAL_FILE, voices / "engines" / m["env"] / LOCAL_FILE):
            try:
                info.update({k: v for k, v in json.loads(path.read_text(encoding="utf-8")).items()
                             if k in ("weights_from", "python_from")})  # fmt: skip
            except (OSError, ValueError, AttributeError):
                continue
        if info:
            out[name] = info
    return out
