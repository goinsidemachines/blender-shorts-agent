"""CLI doctor: one health check before the work starts. Each check is {"id", "level": ok|warn|error, "text", "fix"}.

Blender (found, new enough), the GPU lock (another session rendering), the voice (set up), the disk (room for
frames), the caption font (installed, not a variable font) and the review-page server. Nothing here changes anything.
"""

from __future__ import annotations

import json
import os
import shutil
import time
from pathlib import Path

DISK_WARN_BYTES = 10 * 10**9
FIRST_RUN = "skills/make-short/playbook/00-first-run.md"
LOCAL_VOICES = ("kokoro", "chatterbox", "chatterbox-turbo")


def _check(id_: str, level: str, text: str, fix: str | None = None) -> dict:
    return {"id": id_, "level": level, "text": text, "fix": fix}


def _blender(settings: dict) -> dict:
    from bsa import locate

    try:
        exe = locate.find_blender(settings)
    except locate.BlenderNotFound as exc:
        need = f"{locate.MIN_BLENDER[0]}.{locate.MIN_BLENDER[1]}"
        return _check("blender", "error", "Blender was not found.",
                      f'Install Blender {need} or newer, or set "blender_exe" (settings set blender_exe PATH) or '
                      f"the BLENDER_EXE variable. ({exc})")  # fmt: skip
    try:
        version = locate.blender_version(exe)
    except Exception as exc:  # noqa: BLE001 - a Blender that won't run is one more finding, not a crash
        return _check("blender", "error", f"Blender at {exe} did not run: {exc}",
                      'Check the install, or point "blender_exe" at a working Blender.')  # fmt: skip
    label = f"{version[0]}.{version[1]}"
    if version < locate.MIN_BLENDER:
        need = f"{locate.MIN_BLENDER[0]}.{locate.MIN_BLENDER[1]}"
        return _check("blender", "error", f"Blender {label} at {exe} is too old (needs {need} or newer).",
                      f'Install Blender {need} or newer and set "blender_exe" to it.')  # fmt: skip
    return _check("blender", "ok", f"Blender {label} at {exe}")


def _gpu(workspace: Path | None) -> dict:
    from bsa import gpulock, render_status

    if not workspace:
        return _check("gpu", "ok", "No workspace is set yet, so there is no GPU lock to check.")
    holder = gpulock.read(gpulock.lock_path(workspace))
    if holder and render_status.is_alive(holder):
        return _check("gpu", "warn", f"The GPU is busy: {gpulock.describe_holder(holder)}.",
                      "Wait for that job to end (a new render waits for it by itself).")  # fmt: skip
    return _check("gpu", "ok", "The GPU is free.")


def _voice(settings: dict) -> dict:
    from bsa.narrate import install

    narrator = settings.get("narrator") or {}
    provider = narrator.get("provider")
    fix = f"Choose a narrator with settings set narrator.provider (see {FIRST_RUN}), or none for captions only."
    if provider == "none":
        return _check("voice", "ok", "Captions only: no voice.")
    if provider in LOCAL_VOICES:
        if install.installed().get(provider):
            return _check("voice", "ok", f"Voice {provider} is installed.")
        return _check("voice", "warn", f"Voice {provider} is chosen but not installed.",
                      f"Run voice plan {provider}, then voice install {provider} once the user says yes "
                      f"({FIRST_RUN}).")  # fmt: skip
    if provider == "custom":
        if narrator.get("command"):
            return _check("voice", "ok", "A custom narrator command is set.")
        return _check("voice", "warn", "The custom narrator has no command yet.",
                      f'settings set narrator.command "..." (see {FIRST_RUN}).')  # fmt: skip
    if provider == "elevenlabs":
        if os.environ.get("ELEVENLABS_API_KEY"):
            return _check("voice", "ok", "ElevenLabs is chosen and its key is set.")
        return _check("voice", "warn", "ElevenLabs is chosen but ELEVENLABS_API_KEY is not set.",
                      f"The user sets the ELEVENLABS_API_KEY environment variable, never in chat ({FIRST_RUN}).")  # fmt: skip
    if provider:
        return _check("voice", "warn", f"Unknown narrator provider {provider!r}.", fix)
    return _check("voice", "warn", "No narrator is chosen yet.", fix)


def _disk(workspace: Path | None) -> dict:
    where = Path(workspace) if workspace else Path.home()
    while not where.exists() and where != where.parent:
        where = where.parent
    try:
        free = shutil.disk_usage(where)[2]
    except OSError as exc:
        return _check("disk", "warn", f"Could not read the free disk space at {where}: {exc}")
    text = f"{free / 1e9:.1f} GB free at {where}"
    if free < DISK_WARN_BYTES:
        return _check("disk", "warn", f"Only {text}.",
                      "Free some space before rendering: frames and videos take several GB.")  # fmt: skip
    return _check("disk", "ok", text)


def _font(settings: dict, short, cache_dirs: list[Path] | None = None) -> dict:
    from bsa import fonts

    name = fonts.caption_font_name(short, settings)  # as the edit and validate read it
    if not name:
        return _check("font", "ok", "No caption font is set: captions use the default.")
    path, used, note = fonts.resolve(name, cache_dirs=cache_dirs)
    if path is None or note:
        return _check("font", "warn", (note or f"font {name!r} is not installed").rstrip("."),
                      "Install the font, or pick an installed one (CLI fonts shows some).")  # fmt: skip
    info = fonts.font_info_or_none(path)
    cached = fonts.cached_weights(info.get("family") or used or name, cache_dirs) if info and info.get("variable") else []
    warning = fonts.variable_warning(info, used or name, cached) if info else None
    if warning:
        return _check("font", "warn", f"{used or name} is a variable font.", warning)
    return _check("font", "ok", f"Caption font {used or name} is installed.")


def _server(server: dict | None) -> dict:
    if not server:
        return _check("server", "warn", "The review page server is not started yet.",
                      "It starts with page SHORT --open.")  # fmt: skip
    return _check("server", "ok", f"The review page server is running on port {server.get('port')}.")


def run(settings: dict, workspace: Path | None, short=None, server: dict | None = None,
        cache_dirs: list[Path] | None = None) -> list[dict]:  # fmt: skip
    settings = settings or {}
    return [_blender(settings), _gpu(workspace), _voice(settings), _disk(workspace), _font(settings, short, cache_dirs),
            _server(server)]  # fmt: skip


def run_cheap(settings: dict, workspace: Path | None, short=None, server: dict | None = None,
              cache_dirs: list[Path] | None = None, saved: list[dict] | None = None) -> list[dict]:  # fmt: skip
    """The checks that cost nothing (GPU lock, voice, disk, font, server) run again; the Blender check (it starts
    Blender) is carried over from `saved`, the checks an earlier doctor wrote, and only run when there is none. This
    keeps the page's health banner current after things change (an assets download, a voice install, a font)."""
    settings = settings or {}
    old = next((c for c in saved or [] if isinstance(c, dict) and c.get("id") == "blender"), None)
    return [old or _blender(settings), _gpu(workspace), _voice(settings), _disk(workspace),
            _font(settings, short, cache_dirs), _server(server)]  # fmt: skip


def saved_checks(sdir: Path) -> list[dict]:
    """The checks in <short>/review/doctor.json ([] when there is none or it can't be read)."""
    try:
        checks = json.loads((Path(sdir) / "review" / "doctor.json").read_text("utf-8")).get("checks")
    except (OSError, ValueError, AttributeError):
        return []
    return [c for c in checks if isinstance(c, dict)] if isinstance(checks, list) else []


def save(sdir: Path, checks: list[dict]) -> Path:
    """Write <short>/review/doctor.json for the review page."""
    path = Path(sdir) / "review" / "doctor.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps({"at": time.time(), "checks": checks}, indent=2), encoding="utf-8")
    os.replace(tmp, path)
    return path
