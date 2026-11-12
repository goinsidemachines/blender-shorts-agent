"""The user's own command after a final: `hooks.after_final`.

The command is split like the custom narrator's (bsa.settings.split_command, no shell) and each placeholder is filled
inside its own argument, so a path with spaces or a quote in it stays one argument. It runs once, with a timeout; its
output and exit code come back as data. A failing hook is a warning for the caller, never a failed final.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

from bsa import proc, settings

PLACEHOLDERS = ("short_dir", "mp4", "deliver_dir", "slug")
TIMEOUT_S = 300
_FIELD = re.compile(r"\{(\w+)\}")
TAIL = 2000  # characters of output kept, from the end


def problems(command) -> list[str]:
    """Why `command` can't be a hooks.after_final value ([] when it can)."""
    if not isinstance(command, str) or not command.strip():
        return ["hooks.after_final must be a command, e.g. 'bash /path/backup.sh {mp4}'"]
    try:
        parts = settings.split_command(command)
    except ValueError as exc:
        return [f"hooks.after_final can't be read ({exc})"]
    if not parts:
        return ["hooks.after_final has no command in it"]
    bad = sorted({m for m in _FIELD.findall(command) if m not in PLACEHOLDERS})
    if bad:
        return [f"hooks.after_final uses {{{bad[0]}}}: use only {', '.join('{' + p + '}' for p in PLACEHOLDERS)}"]
    return []


def argv(command: str, values: dict[str, str | None]) -> list[str]:
    """The command's arguments with each {placeholder} filled. ValueError for a command that can't be read or a
    placeholder with no value (deliver_dir when render.deliver_dir is not set)."""
    wrong = problems(command)
    if wrong:
        raise ValueError(wrong[0])
    out = []
    for part in settings.split_command(command):
        for key, val in values.items():
            if f"{{{key}}}" in part:
                if val is None:
                    raise ValueError(f"hooks.after_final uses {{{key}}} but there is nothing to put there "
                                     f"(for deliver_dir: set render.deliver_dir)")  # fmt: skip
                part = part.replace(f"{{{key}}}", val)
        out.append(part)
    return out


def _tail(text: str | None) -> str:
    text = (text or "").strip()
    return text[-TAIL:]


def run_after_final(s: dict, sdir: Path, slug: str, mp4: Path, deliver_dir: Path | None, run=proc.run,
                    timeout: float = TIMEOUT_S) -> dict | None:  # fmt: skip
    """None when no hook is set. Else {"command", "exit_code", "output", "ok", "warning"?}: never raises."""
    command = ((s or {}).get("hooks") or {}).get("after_final")
    if not command:
        return None
    values = {"short_dir": str(sdir), "mp4": str(mp4), "slug": slug,
              "deliver_dir": str(deliver_dir) if deliver_dir else None}  # fmt: skip
    try:
        args = argv(command, values)
    except ValueError as exc:
        return {
            "command": command,
            "exit_code": None,
            "output": "",
            "ok": False,
            "warning": f"after-final hook not run: {exc}",
        }
    try:
        proc = run(args, check=False, capture_output=True, text=True, errors="replace", stdin=subprocess.DEVNULL,
                   timeout=timeout, cwd=str(sdir))  # fmt: skip
    except subprocess.TimeoutExpired as exc:
        out = _tail(exc.stdout if isinstance(exc.stdout, str) else "")
        return {"command": args, "exit_code": None, "output": out, "ok": False,
                "warning": f"after-final hook stopped after {timeout:g} s (the final is fine)"}  # fmt: skip
    except OSError as exc:
        return {"command": args, "exit_code": None, "output": "", "ok": False,
                "warning": f"after-final hook couldn't start: {exc.strerror or exc} (the final is fine)"}  # fmt: skip
    output = _tail((proc.stdout or "") + (("\n" + proc.stderr) if proc.stderr else ""))
    res = {"command": args, "exit_code": proc.returncode, "output": output, "ok": proc.returncode == 0}
    if proc.returncode != 0:
        res["warning"] = f"after-final hook exited {proc.returncode} (the final is fine)"
    return res
