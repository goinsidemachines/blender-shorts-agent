"""`bsa-tts speak|voices|check|verify|fetch`: the one entry point blender-shorts-agent calls for free local voices.

This environment stays small (numpy, scipy, soundfile). Each engine runs in its own uv environment under
`<voices>/engines/<env>/.venv` through workers/engine_worker.py, loading weights from `<voices>/weights/<model>/`
only. The runner resamples whatever an engine produces to 48 kHz mono 16-bit WAV and writes durations.json.

Layout (`<voices>` is the folder that holds this runner, normally `<bsa_home>/voices`):
    runner/          this project
    engines/<env>/   one uv project per engine (kokoro, chatterbox, whisper)
    weights/<model>/ downloaded weights; `.complete` marks a finished download
    refs/            reference clips Chatterbox clones voices from (`me.wav` is the user's own voice)

Log lines go to stderr. The last stdout line is always JSON: a summary, or {"ok": false, "exit", "message"}.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

from bsa_tts import EXIT_ERROR, EXIT_GPU_BUSY, EXIT_NOT_INSTALLED, EXIT_OK, EXIT_OOM, EXIT_USAGE, gpu

OUT_SR = 48000
REGISTRY = Path(__file__).resolve().parent / "registry.json"
WORKER = Path(__file__).resolve().parent / "workers" / "engine_worker.py"
COMPLETE = ".complete"
ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,99}$")
_HF = "https://huggingface.co/{repo}/resolve/{revision}/{file}"


class CliError(Exception):
    def __init__(self, message: str, code: int):
        super().__init__(message)
        self.code = code


def log(msg: str) -> None:
    print(f"[bsa-tts] {msg}", file=sys.stderr, flush=True)


def root() -> Path:
    """The voices folder: `$BSA_TTS_ROOT`, else the folder that holds this runner."""
    env = os.environ.get("BSA_TTS_ROOT")
    return Path(env) if env else Path(__file__).resolve().parents[2]


def engine_python(env_dir: Path, os_name: str = os.name) -> Path:
    """The Python inside an engine's uv environment, on Windows or POSIX."""
    if os_name == "nt":
        return Path(env_dir) / ".venv" / "Scripts" / "python.exe"
    return Path(env_dir) / ".venv" / "bin" / "python"


def load_registry() -> dict:
    return json.loads(REGISTRY.read_text(encoding="utf-8"))


def weights_dir(name: str) -> Path:
    return root() / "weights" / name


def is_installed(name: str, model: dict) -> bool:
    return engine_python(root() / "engines" / model["env"]).is_file() and (weights_dir(name) / COMPLETE).is_file()


def get_model(reg: dict, name: str, kind: str = "tts") -> dict:
    models = {k: m for k, m in reg["models"].items() if m["kind"] == kind}
    if name not in models:
        raise CliError(f"unknown model {name!r}; have: {', '.join(models)}", EXIT_USAGE)
    return models[name]


def require_installed(name: str, model: dict) -> None:
    if not is_installed(name, model):
        raise CliError(
            f"{name} is not installed (expected {engine_python(root() / 'engines' / model['env'])} "
            f"and {weights_dir(name) / COMPLETE})",
            EXIT_NOT_INSTALLED,
        )


def load_lines(path: Path) -> list[dict]:
    if not path.is_file():
        raise CliError(f"lines file not found: {path}", EXIT_USAGE)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, UnicodeDecodeError) as e:
        raise CliError(f"cannot parse {path}: {e}", EXIT_USAGE) from e
    if not isinstance(data, list) or not data:
        raise CliError('lines file must be a non-empty JSON list of {"id", "text"}', EXIT_USAGE)
    seen: set[str] = set()
    lines = []
    for i, item in enumerate(data):
        if not isinstance(item, dict) or "id" not in item or "text" not in item:
            raise CliError(f'line {i}: expected {{"id", "text"}}', EXIT_USAGE)
        lid, txt = str(item["id"]), str(item["text"]).strip()
        if not ID_RE.match(lid):
            raise CliError(f"line {i}: id {lid!r} must be letters, digits, '_', '-' or '.'", EXIT_USAGE)
        if lid in seen:
            raise CliError(f"line {i}: duplicate id {lid!r}", EXIT_USAGE)
        if not txt:
            raise CliError(f"line {i}: empty text", EXIT_USAGE)
        seen.add(lid)
        lines.append({"id": lid, "text": txt})
    return lines


def resolve_voice(name: str, model: dict, voice_name: str | None) -> tuple[str, dict]:
    voice_name = voice_name or model["default_voice"]
    voices = model["voices"]
    if voice_name not in voices:
        raise CliError(f"unknown voice {voice_name!r} for {name}; have: {', '.join(voices)}", EXIT_USAGE)
    voice = voices[voice_name]
    ref = voice.get("ref")
    if ref and not (root() / "refs" / ref).is_file():
        hint = " Record one as described in voices/README.md." if voice_name == "me" else ""
        missing = root() / "refs" / ref
        raise CliError(f"reference clip for voice {voice_name!r} is missing: {missing}.{hint}", EXIT_NOT_INSTALLED)
    return voice_name, voice


def pick_device(model: dict, name: str, force_cpu: bool, cpu_fallback: bool) -> str:
    cpu_ok = "cpu" in model["devices"]
    if force_cpu:
        if not cpu_ok:
            raise CliError(f"{name} needs an NVIDIA GPU; it cannot run on the CPU", EXIT_USAGE)
        return "cpu"
    need = int(model["min_free_vram_mb"])
    state = gpu.query()
    if state.available and state.free_mb >= need:
        log(f"GPU has {state.free_mb} MB free (need about {need} MB)")
        return "cuda"
    if not state.available:
        reason = f"no NVIDIA GPU found (nvidia-smi is not available); {name} needs one with about {need} MB free"
    else:
        reason = (
            f"the GPU is busy: {state.free_mb} MB free of {state.total_mb} MB, but {name} needs about {need} MB. "
            "Close other programs that use the GPU, or wait until they finish"
        )
    if cpu_fallback and cpu_ok:
        log(f"{reason}; using the CPU instead")
        return "cpu"
    raise CliError(reason, EXIT_GPU_BUSY)


def run_worker(model: dict, job: dict, workdir: Path) -> dict:
    job_path = workdir / "job.json"
    job_path.write_text(json.dumps(job, indent=1), encoding="utf-8")
    python = engine_python(root() / "engines" / model["env"])
    env = dict(os.environ)
    # Weights load from disk only: no network at run time.
    env.update(
        HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1", HF_HUB_DISABLE_TELEMETRY="1", HF_DATASETS_OFFLINE="1",
        PYTHONIOENCODING="utf-8", PYTHONUTF8="1",
    )  # fmt: skip
    proc = subprocess.run([str(python), "-I", str(WORKER), str(job_path)], env=env, check=False)
    if proc.returncode == EXIT_OOM:
        raise CliError("CUDA ran out of memory while generating. Close other programs that use the GPU.", EXIT_OOM)
    if proc.returncode != 0:
        code = proc.returncode if proc.returncode in (EXIT_NOT_INSTALLED, EXIT_USAGE) else EXIT_ERROR
        raise CliError(f"the {job['engine']} worker failed (exit {proc.returncode}); see the log above", code)
    return json.loads((workdir / "result.json").read_text(encoding="utf-8"))


def to_48k_mono(src: Path, dst: Path) -> float:
    from math import gcd

    import numpy as np
    import soundfile as sf
    from scipy.signal import resample_poly

    audio, sr = sf.read(src, dtype="float32", always_2d=True)
    audio = audio.mean(axis=1)
    if sr != OUT_SR:
        g = gcd(OUT_SR, sr)
        audio = resample_poly(audio, OUT_SR // g, sr // g).astype(np.float32)
    peak = float(np.max(np.abs(audio))) if audio.size else 0.0
    if peak > 0.999:
        audio *= 0.999 / peak
    sf.write(dst, audio, OUT_SR, subtype="PCM_16")
    return round(len(audio) / OUT_SR, 3)


def cmd_speak(a: argparse.Namespace) -> dict:
    reg = load_registry()
    model = get_model(reg, a.model)
    lines = load_lines(Path(a.lines))
    if not 0.5 <= a.speed <= 2.0:
        raise CliError("--speed must be between 0.5 and 2.0", EXIT_USAGE)
    require_installed(a.model, model)
    voice_name, voice = resolve_voice(a.model, model, a.voice)
    device = pick_device(model, a.model, a.cpu, a.cpu_fallback)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    weights = weights_dir(a.model)
    params = {**model.get("params", {}), **voice.get("params", {})}
    t0 = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix="bsa-tts-", dir=out) as tmp:
        job = {
            "engine": model["engine"], "variant": model.get("variant"), "weights": str(weights),
            "voice_file": str(weights / voice["file"]) if voice.get("file") else None,
            "ref": str(root() / "refs" / voice["ref"]) if voice.get("ref") else None,
            "lang": voice.get("engine_lang"), "params": params,
            "speed": a.speed, "seed": a.seed, "device": device, "lines": lines, "outdir": tmp,
        }  # fmt: skip
        log(f"{a.model} / {voice_name} on {device}, {len(lines)} line(s)")
        try:
            result = run_worker(model, job, Path(tmp))
        except CliError as e:
            if e.code == EXIT_OOM and a.cpu_fallback and "cpu" in model["devices"] and device == "cuda":
                log("out of memory on the GPU; retrying on the CPU")
                job["device"] = device = "cpu"
                result = run_worker(model, job, Path(tmp))
            else:
                raise
        durations = {ln["id"]: to_48k_mono(Path(tmp) / f"{ln['id']}.wav", out / f"{ln['id']}.wav") for ln in lines}
    (out / "durations.json").write_text(json.dumps(durations, indent=2), encoding="utf-8")
    meta = {
        "ok": True, "model": a.model, "voice": voice_name, "speed": a.speed, "seed": a.seed, "device": device,
        "sample_rate": OUT_SR, "load_seconds": result.get("load_seconds"), "gen_seconds": result.get("gen_seconds"),
        "peak_vram_mb": result.get("peak_vram_mb"), "wall_seconds": round(time.perf_counter() - t0, 2),
        "durations": durations, "out": str(out.resolve()),
    }  # fmt: skip
    (out / "render.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    return meta


def cmd_voices(a: argparse.Namespace) -> dict:
    reg = load_registry()
    listing = {}
    for name, m in reg["models"].items():
        if m["kind"] != "tts" or (a.model and name != a.model):
            continue
        print(f"{name}  ({m['licence']}, devices: {'/'.join(m['devices'])})")
        for vid, v in m["voices"].items():
            print(f"  {vid:<12} {v['gender']:<7} {v['language']:<6} {v.get('description', '')}")
        listing[name] = list(m["voices"])
    if a.model and not listing:
        raise CliError(f"unknown model {a.model!r}", EXIT_USAGE)
    return {"ok": True, "voices": listing}


def cmd_check(a: argparse.Namespace) -> dict:
    reg = load_registry()
    state = gpu.query()
    info = {
        "ok": True,
        "gpu": {"available": state.available, "total_mb": state.total_mb, "free_mb": state.free_mb},
        "models": {},
    }
    for name, m in reg["models"].items():
        info["models"][name] = {
            "kind": m["kind"],
            "installed": is_installed(name, m),
            "gpu_ready": state.available and state.free_mb >= m["min_free_vram_mb"],
            "cpu": "cpu" in m["devices"],
        }
    return info


# --- verify: Whisper transcribes each take; word error rate against the script --------------------------------------

_ONES = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen", "nineteen"]  # fmt: skip
_TENS = ["_", "_", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"]
_SCALES = ((10**9, "billion"), (10**6, "million"), (1000, "thousand"), (100, "hundred"))
_FILLERS = {"and", "a", "the"}  # words Whisper drops or adds freely
_SPELLING = {"tonne": "ton", "tonnes": "tons"}


def _number_words(n: int) -> str:
    if n < 20:
        return _ONES[n]
    if n < 100:
        return _TENS[n // 10] + ("" if n % 10 == 0 else " " + _ONES[n % 10])
    for size, word in _SCALES:
        if n >= size:
            rest = n % size
            return f"{_number_words(n // size)} {word}" + (f" {_number_words(rest)}" if rest else "")
    return str(n)


def words(text: str) -> list[str]:
    s = text.lower().replace("’", "'").replace(",", "")
    s = re.sub(r"\d+", lambda m: f" {_number_words(int(m.group()))} ", s)
    s = re.sub(r"[^a-z' ]", " ", s.replace("-", " "))
    return [_SPELLING.get(w, w) for w in s.split() if w not in _FILLERS]


def wer(reference: str, hypothesis: str) -> float:
    ref, hyp = words(reference), words(hypothesis)
    d = list(range(len(hyp) + 1))
    for i, r in enumerate(ref, 1):
        prev, d[0] = d[0], i
        for j, h in enumerate(hyp, 1):
            prev, d[j] = d[j], min(d[j] + 1, d[j - 1] + 1, prev + (r != h))
    return round(d[-1] / max(1, len(ref)), 3)


def cmd_verify(a: argparse.Namespace) -> dict:
    reg = load_registry()
    name = "whisper-small.en"
    model = get_model(reg, name, kind="asr")
    require_installed(name, model)
    lines = load_lines(Path(a.lines))
    takes = Path(a.takes)
    files = []
    for ln in lines:
        wav = takes / f"{ln['id']}.wav"
        if not wav.is_file():
            raise CliError(f"take {wav} is missing", EXIT_USAGE)
        files.append({"id": ln["id"], "path": str(wav)})
    device = pick_device(model, name, a.cpu, cpu_fallback=True)
    with tempfile.TemporaryDirectory(prefix="bsa-tts-") as tmp:
        job = {"engine": "whisper", "weights": str(weights_dir(name)), "device": device, "files": files, "outdir": tmp}
        texts = run_worker(model, job, Path(tmp))["texts"]
    return {ln["id"]: {"text": texts[ln["id"]], "wer": wer(ln["text"], texts[ln["id"]])} for ln in lines}


# --- fetch: download a model's weights from its official Hugging Face repo ----------------------------------------


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(1 << 24):
            h.update(chunk)
    return h.hexdigest()


def _download(url: str, dest: Path, size: int, sha256: str, attempts: int = 6) -> None:
    """Download to `<dest>.part` (resuming), check its size and sha256, then move it into place."""
    part = dest.with_name(dest.name + ".part")
    for attempt in range(1, attempts + 1):
        have = part.stat().st_size if part.exists() else 0
        req = urllib.request.Request(url, headers={"User-Agent": "bsa-tts/0.1"})
        if have:
            req.add_header("Range", f"bytes={have}-")
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                mode = "ab" if have and resp.status == 206 else "wb"
                with open(part, mode) as f:
                    while chunk := resp.read(1 << 20):
                        f.write(chunk)
        except (urllib.error.URLError, OSError, TimeoutError) as e:
            if attempt == attempts:
                raise CliError(f"download failed: {url}: {e}", EXIT_ERROR) from e
            log(f"{dest.name}: {e}; retrying ({attempt}/{attempts - 1})")
            time.sleep(min(30, 3 * attempt))
            continue
        if part.stat().st_size == size:
            actual = _sha256(part)
            if actual != sha256:
                part.unlink()
                raise CliError(
                    f"{dest.name} failed its integrity check: sha256 is {actual}, expected {sha256}. "
                    "The download was deleted; run the install again.",
                    EXIT_ERROR,
                )
            part.replace(dest)
            return
        if part.stat().st_size > size:
            part.unlink()
    raise CliError(f"download of {url} did not reach the expected {size} bytes", EXIT_ERROR)


def cmd_fetch(a: argparse.Namespace) -> dict:
    reg = load_registry()
    if a.model not in reg["models"]:
        raise CliError(f"unknown model {a.model!r}; have: {', '.join(reg['models'])}", EXIT_USAGE)
    w = reg["models"][a.model]["weights"]
    dest = weights_dir(a.model)
    done = dest / COMPLETE
    if done.is_file():
        return {"ok": True, "model": a.model, "bytes": w["bytes"], "already": True}
    for file, meta in w["files"].items():
        target = dest / file
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.is_file() and target.stat().st_size == meta["bytes"] and _sha256(target) == meta["sha256"]:
            continue
        log(f"{a.model}: {file} ({meta['bytes'] / 1e6:.1f} MB) from huggingface.co/{w['repo']}@{w['revision'][:8]}")
        url = _HF.format(repo=w["repo"], revision=w["revision"], file=file)
        _download(url, target, meta["bytes"], meta["sha256"])
    done.write_text(json.dumps({"repo": w["repo"], "revision": w["revision"]}), encoding="utf-8")
    return {"ok": True, "model": a.model, "bytes": w["bytes"], "already": False}


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="bsa-tts", description="Free local text to speech (offline once installed).")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("speak", help="render lines to <out>/<id>.wav (48 kHz mono 16-bit) plus durations.json")
    s.add_argument("--model", required=True, help="kokoro, chatterbox or chatterbox-turbo")
    s.add_argument("--voice", help="a voice of that model (see `bsa-tts voices`); default: the model's default")
    s.add_argument("--lines", required=True, help='JSON list of {"id", "text"}; ids become file names')
    s.add_argument("--out", required=True, help="output folder")
    s.add_argument("--speed", type=float, default=1.0, help="0.5-2.0 (default 1.0)")
    s.add_argument("--seed", type=int, default=0, help="same text, voice and seed give the same take (default 0)")
    s.add_argument("--cpu", action="store_true", help="force the CPU (Kokoro only)")
    s.add_argument("--cpu-fallback", action="store_true", help="use the CPU if the GPU is busy (Kokoro only)")
    s.set_defaults(fn=cmd_speak)

    v = sub.add_parser("voices", help="list models and voices")
    v.add_argument("--model")
    v.set_defaults(fn=cmd_voices)

    c = sub.add_parser("check", help="GPU free memory and which models are installed (JSON)")
    c.set_defaults(fn=cmd_check)

    r = sub.add_parser("verify", help="transcribe each take with Whisper and score word error rate (JSON)")
    r.add_argument("--lines", required=True)
    r.add_argument("--takes", required=True)
    r.add_argument("--cpu", action="store_true", help="force the CPU")
    r.set_defaults(fn=cmd_verify)

    f = sub.add_parser("fetch", help="download a model's weights from Hugging Face (used by the installer)")
    f.add_argument("--model", required=True)
    f.set_defaults(fn=cmd_fetch)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        summary = args.fn(args)
    except CliError as e:
        log(f"error: {e}")
        print(json.dumps({"ok": False, "exit": e.code, "message": str(e)}))
        return e.code
    except KeyboardInterrupt:
        return EXIT_ERROR
    print(json.dumps(summary))
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
