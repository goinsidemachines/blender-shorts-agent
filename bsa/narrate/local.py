"""Free local voices (Kokoro, Chatterbox) through the bsa-tts runner, which lives in its own uv project."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from bsa.messages import trim
from bsa.narrate import contract, install
from bsa.narrate.contract import NarrationError
from bsa.settings import bsa_home

WER_LIMIT = 0.15
RETRY_SEEDS = (8, 9)
_GPU_DEFAULT = "the GPU is busy or out of memory"


def _base(*args: str) -> list[str]:
    # Blender may not inherit the user's PATH, so find uv where its installer puts it too.
    uv = install.uv_path()
    if uv is None:
        raise NarrationError(
            f"uv is not installed (https://astral.sh/uv); install it with: {install.uv_install_hint()}"
        )
    return [str(uv), "run", "--project", str(bsa_home() / "voices" / "runner"), "bsa-tts", *args]


def _last_json(stdout: str) -> dict:
    for ln in reversed((stdout or "").strip().splitlines()):
        try:
            data = json.loads(ln)
        except ValueError:
            continue
        return data if isinstance(data, dict) else {}
    return {}


def _tail(proc) -> str:
    text = (proc.stderr or "").strip() or (proc.stdout or "").strip()
    return trim(text.splitlines()[-1]) if text else "no output"


def generate(
    model: str, lines, voice, speed: float, out_dir: Path, run=subprocess.run, seed: int = 7
) -> dict[str, float]:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    lines_file = out_dir / "lines.json"
    contract.write_lines(lines_file, lines)
    cmd = _base("speak", "--model", model)
    if voice:
        cmd += ["--voice", str(voice)]
    cmd += ["--speed", str(speed), "--lines", str(lines_file), "--out", str(out_dir)]
    cmd += ["--seed", str(seed), "--cpu-fallback"]
    proc = run(cmd, check=False, capture_output=True, text=True)
    code = proc.returncode
    if code == 5:
        raise NarrationError(f"the {model} voice engine is not installed")
    if code in (3, 4):
        raise NarrationError(trim(str(_last_json(proc.stdout).get("message") or _GPU_DEFAULT)))
    if code != 0:
        raise NarrationError(f"the {model} voice engine failed (exit {code}): {_tail(proc)}")
    for ln in lines:
        problems = contract.check_take(out_dir / f"{ln.id}.wav")
        if problems:
            raise NarrationError("; ".join(problems))
    return contract.read_durations(out_dir, [ln.id for ln in lines])


def verify(lines, out_dir: Path, run=subprocess.run) -> dict[str, float] | None:
    """Word error rate per line, or None when Whisper isn't installed."""
    out_dir = Path(out_dir)
    lines_file = out_dir / "lines.json"
    contract.write_lines(lines_file, lines)
    cmd = _base("verify", "--lines", str(lines_file), "--takes", str(out_dir))
    proc = run(cmd, check=False, capture_output=True, text=True)
    if proc.returncode == 5:
        return None
    if proc.returncode != 0:
        raise NarrationError(f"verification failed (exit {proc.returncode}): {_tail(proc)}")
    data = _last_json(proc.stdout)
    return {i: float(v["wer"]) for i, v in data.items() if isinstance(v, dict) and "wer" in v}
