"""Runs inside an engine's own environment: python -I engine_worker.py job.json

Speech engines (kokoro, chatterbox) write <outdir>/<id>.wav at the engine's native rate; the whisper engine
transcribes the job's files. Both write <outdir>/result.json. Weights load from disk only (the runner sets
HF_HUB_OFFLINE=1). Exit codes: 0 ok, 4 CUDA out of memory, 5 missing files, 1 anything else.
Self-contained on purpose (-I means no imports from the runner package).
"""

import json
import logging
import random
import sys
import time
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")
logging.getLogger("torch.distributed.elastic").setLevel(logging.ERROR)

EXIT_OOM = 4
EXIT_NOT_INSTALLED = 5


def log(msg):
    print(f"[worker] {msg}", file=sys.stderr, flush=True)


def seed_all(torch, seed):
    random.seed(seed)
    try:
        import numpy as np

        np.random.seed(seed % 2**32)
    except ImportError:
        pass
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)


def load_kokoro(job, torch):
    from kokoro import KModel, KPipeline

    w = Path(job["weights"])
    model = (
        KModel(repo_id="hexgrad/Kokoro-82M", config=str(w / "config.json"), model=str(w / "kokoro-v1_0.pth"))
        .to(job["device"])
        .eval()
    )
    pipe = KPipeline(lang_code=job["lang"], repo_id="hexgrad/Kokoro-82M", model=model)
    voice = job["voice_file"]
    if not Path(voice).is_file():
        log(f"voice file missing: {voice}")
        sys.exit(EXIT_NOT_INSTALLED)

    def speak(text):
        chunks = [
            r.audio for r in pipe(text, voice=voice, speed=job["speed"], split_pattern=r"\n+") if r.audio is not None
        ]
        return torch.cat(chunks).cpu().numpy()

    return speak, 24000


def load_chatterbox(job, torch):
    w = Path(job["weights"])
    p = job["params"]
    if job["variant"] == "turbo":
        from chatterbox.tts_turbo import ChatterboxTurboTTS

        model = ChatterboxTurboTTS.from_local(w, job["device"])
        gen_kw = {"temperature": p.get("temperature", 0.8)}
        if job["ref"]:
            model.prepare_conditionals(job["ref"])
    else:
        from chatterbox.tts import ChatterboxTTS

        model = ChatterboxTTS.from_local(w, job["device"])
        gen_kw = {k: p[k] for k in ("exaggeration", "cfg_weight", "temperature") if k in p}
        if job["ref"]:
            model.prepare_conditionals(job["ref"], exaggeration=p.get("exaggeration", 0.5))

    speed = job["speed"]

    def speak(text):
        wav = model.generate(text, **gen_kw).squeeze(0).cpu().numpy()
        if speed != 1.0:
            # Chatterbox has no native speed control; this is a time-stretch afterwards.
            import librosa

            wav = librosa.effects.time_stretch(wav, rate=speed)
        return wav

    return speak, model.sr


def run_speech(job, torch, out, result):
    import soundfile as sf

    device = job["device"]
    t0 = time.perf_counter()
    loader = {"kokoro": load_kokoro, "chatterbox": load_chatterbox}[job["engine"]]
    speak, sr = loader(job, torch)
    if device == "cuda":
        seed_all(torch, job["seed"])
        speak("Warming up.")  # the first CUDA call compiles kernels; keep it out of per-line timings
    result["load_seconds"] = round(time.perf_counter() - t0, 2)
    result["gen_seconds"] = {}
    for line in job["lines"]:
        seed_all(torch, job["seed"])  # reset per line: same text, voice and seed give the same take
        t = time.perf_counter()
        wav = speak(line["text"])
        if device == "cuda":
            torch.cuda.synchronize()
        result["gen_seconds"][line["id"]] = round(time.perf_counter() - t, 3)
        sf.write(out / f"{line['id']}.wav", wav, sr)
        log(f"{line['id']}: {len(wav) / sr:.2f}s of audio in {result['gen_seconds'][line['id']]:.2f}s")


def run_whisper(job, torch, out, result):
    import soundfile as sf
    from scipy.signal import resample_poly
    from transformers import pipeline

    t0 = time.perf_counter()
    asr = pipeline("automatic-speech-recognition", model=job["weights"], device=job["device"])
    result["load_seconds"] = round(time.perf_counter() - t0, 2)
    result["texts"] = {}
    for item in job["files"]:
        path = Path(item["path"])
        if not path.is_file():
            log(f"take missing: {path}")
            sys.exit(EXIT_NOT_INSTALLED)
        audio, sr = sf.read(path, dtype="float32", always_2d=True)
        audio = audio.mean(axis=1)
        if sr != 16000:  # Whisper's feature extractor expects 16 kHz
            from math import gcd

            g = gcd(16000, sr)
            audio = resample_poly(audio, 16000 // g, sr // g).astype("float32")
        result["texts"][item["id"]] = asr({"raw": audio, "sampling_rate": 16000})["text"].strip()
        log(f"{item['id']}: {result['texts'][item['id']]}")


def main():
    job = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    import torch

    device = job["device"]
    if device == "cuda":
        if not torch.cuda.is_available():
            log("CUDA is not available in this environment")
            sys.exit(1)
        torch.cuda.reset_peak_memory_stats()
    out = Path(job["outdir"])
    result = {"device": device}
    try:
        with torch.inference_mode():
            if job["engine"] == "whisper":
                run_whisper(job, torch, out, result)
            else:
                run_speech(job, torch, out, result)
    except torch.cuda.OutOfMemoryError as e:
        log(f"CUDA out of memory: {e}")
        sys.exit(EXIT_OOM)
    if device == "cuda":
        result["peak_vram_mb"] = round(torch.cuda.max_memory_reserved() / 2**20)
    (out / "result.json").write_text(json.dumps(result), encoding="utf-8")


if __name__ == "__main__":
    main()
