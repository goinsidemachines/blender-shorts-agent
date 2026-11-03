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
WHISPER_MODEL = "whisper-small.en"
RETRIES = 2  # an unclear take is spoken again with its seed + 1, then + 2
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


def whisper_installed() -> bool:
    """The Whisper check's environment and weights are in <bsa_home>/voices (no process started, nothing synced)."""
    try:
        return bool(install.installed().get(WHISPER_MODEL))
    except (OSError, ValueError, KeyError, TypeError):
        return False


def hear(lines, out_dir: Path, run=None) -> dict[str, dict] | None:
    """What Whisper heard in each take and its word error rate, {id: {"said", "wer"}}; None when Whisper isn't
    installed. Nothing is started when it isn't (a `uv run` on a missing runner could sync, i.e. download)."""
    if not whisper_installed():
        return None
    run = run or subprocess.run
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
    return {
        i: {"said": str(v.get("text") or ""), "wer": float(v["wer"])}
        for i, v in data.items()
        if isinstance(v, dict) and "wer" in v
    }


def word_times(lines, takes_dir: Path, run=None) -> dict[str, list[tuple[str, float, float]]] | None:
    """Whisper's word timings per take, {id: [(word, start_s, end_s), ...]}. Best-effort: None when Whisper isn't
    installed or the run fails; a take whose words are malformed is left out."""
    try:
        if not whisper_installed():
            return None
        run = run or subprocess.run
        takes_dir = Path(takes_dir)
        lines_file = takes_dir / "lines.json"
        contract.write_lines(lines_file, lines)
        proc = run(
            _base("verify", "--lines", str(lines_file), "--takes", str(takes_dir), "--words"),
            check=False, capture_output=True, text=True,
        )  # fmt: skip
        if proc.returncode != 0:
            return None
        out: dict[str, list[tuple[str, float, float]]] = {}
        for i, v in _last_json(proc.stdout).items():
            try:
                out[i] = [(str(w[0]), float(w[1]), float(w[2])) for w in v["words"] if len(w) == 3]
                if len(out[i]) != len(v["words"]):
                    del out[i]
            except (KeyError, TypeError, ValueError, IndexError):
                out.pop(i, None)
        return out
    except (NarrationError, OSError):
        return None


def verify(lines, out_dir: Path, run=None) -> dict[str, float] | None:
    """Word error rate per line, or None when Whisper isn't installed."""
    heard = hear(lines, out_dir, run=run)
    return None if heard is None else {i: v["wer"] for i, v in heard.items()}
