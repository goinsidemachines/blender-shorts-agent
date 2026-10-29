"""Recorded sound effects: the library of downloaded clips by sfx kind, and how each `sfx` event gets its sound.

Each event of a kind with cached recordings plays one of them (variants rotate so consecutive events of a kind
differ), trimmed of leading silence, cut to the event's dur_s with a short fade, peak-normalised, and given a small
random pitch and gain change so repeats don't sound copied. A kind with no recording falls back to synthesis
(bsa.audio.sfx), and the mix result says so. `sound_policy` "natural-only" forbids the synthetic kinds (blip, whoosh,
hum) and synthesis altogether: such events are left out of the mix, and `validate` warns about them first. It also
leaves out clips from designed UI packs (interface sounds, tonal and partly synthesised, `designed` in the library),
whatever kind they are tagged with.

Each clip also stores its `flatness` (spectral flatness of its first 0.25 s, 200 Hz-16 kHz: near 1 for noise, near 0
for a pure tone) when it was a WAV at download time. It is shown, not used for picking: real wood and metal knocks
ring too, so a threshold would also drop natural recordings.

The library is `<workspace>/.assets-cache/sounds/library.json`, written by `CLI assets download` for sound assets:
{"clips": [{"path", "kinds", "asset", "name", "source", "license", "author", "page", "designed", "flatness"}]}, paths
relative to the cache.
"""

from __future__ import annotations

import json
import zlib
from pathlib import Path

import numpy as np

from bsa.audio import decode

SR = decode.SR
POLICIES = ("any", "natural-only")
SYNTHETIC_KINDS = ("blip", "whoosh", "hum")  # designed tones with no real-world recording behind them
PITCH_JITTER_ST = 0.6  # +- semitones
GAIN_JITTER_DB = 1.5  # +- dB
FADE_S = 0.03
TRIM_DB = -40  # leading samples quieter than this below the clip's peak are cut
PREROLL_S = 0.004


def sound_policy(short, settings: dict) -> str:
    """A hard rule's policy (natural_sounds_only: no short loosens it), else the short's sound_policy, else the
    `sound_policy` setting, else "any"."""
    from bsa import rules

    forced = rules.forced_sound_policy(rules.effective(settings, short))
    if forced:
        return forced
    return str(getattr(short, "sound_policy", "") or settings.get("sound_policy") or "any")


FLATNESS_WINDOW_S = 0.25
FLATNESS_BAND_HZ = (200.0, 16000.0)


def flatness(x: np.ndarray, sr: int = SR) -> float | None:
    """Spectral flatness (geometric over arithmetic mean power, 200 Hz-16 kHz) of the first 0.25 s after the onset:
    near 1 for noise, near 0 for a pure tone. None for silence."""
    x = np.asarray(x, dtype=np.float64)
    if x.ndim > 1:
        x = x.mean(axis=1)
    if len(x) == 0 or not np.abs(x).max():
        return None
    loud = np.flatnonzero(np.abs(x) > np.abs(x).max() * 10 ** (TRIM_DB / 20))
    x = x[loud[0] : loud[0] + round(FLATNESS_WINDOW_S * sr)]
    n = 2048
    x = np.pad(x, (0, max(0, n - len(x))))
    frames = [x[i : i + n] * np.hanning(n) for i in range(0, len(x) - n + 1, n // 2)]
    p = np.mean([np.abs(np.fft.rfft(f)) ** 2 for f in frames], axis=0)
    f = np.fft.rfftfreq(n, 1 / sr)
    p = p[(f >= FLATNESS_BAND_HZ[0]) & (f <= FLATNESS_BAND_HZ[1])] + 1e-20
    return round(float(np.exp(np.mean(np.log(p))) / np.mean(p)), 4)


def is_designed(clip: dict) -> bool:
    """A clip from a designed UI pack (flagged at download, or by its pack for libraries written before the flag)."""
    from bsa.assets.sounds import PACKS  # here: bsa.assets.sounds imports this module

    if clip.get("designed"):
        return True
    pid = str(clip.get("asset", "")).removeprefix("sfx-")
    return bool(PACKS.get(pid, {}).get("designed"))


def library_path(cache_dir) -> Path:
    return Path(cache_dir) / "sounds" / "library.json"


class Library:
    """The recorded clips cached in a workspace's `.assets-cache`."""

    def __init__(self, cache_dir):
        self.cache_dir = Path(cache_dir)
        path = library_path(cache_dir)
        try:
            self.clips = list(json.loads(path.read_text(encoding="utf-8")).get("clips", []))
        except (OSError, ValueError, AttributeError):
            self.clips = []
        self._audio: dict[str, np.ndarray] = {}

    def file(self, clip: dict) -> Path:
        parts = str(clip["path"]).split("/")
        if any(p in ("", ".", "..") or "\\" in p or ":" in p for p in parts):
            raise ValueError(f"unsafe clip path {clip['path']!r}")
        return self.cache_dir.joinpath(*parts)

    def add(self, entries: list[dict]) -> None:
        by_path = {c["path"]: c for c in self.clips}
        for e in entries:
            by_path[e["path"]] = e
        self.clips = sorted(by_path.values(), key=lambda c: c["path"])

    def save(self) -> None:
        path = library_path(self.cache_dir)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps({"clips": self.clips}, indent=1, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)

    def clips_for(self, kind: str) -> list[dict]:
        """Cached clips tagged with `kind`, by path (files that were removed from the cache are left out)."""
        return sorted((c for c in self.clips if kind in c.get("kinds", []) and self.file(c).is_file()),
                      key=lambda c: c["path"])  # fmt: skip

    def find(self, name: str) -> dict | None:
        """A clip by its cache path, its file name without extension, or "<asset>/<file stem>"."""
        want = str(name).replace("\\", "/").lower()
        for match in (lambda c: c["path"].lower() == want, lambda c: Path(c["path"]).stem.lower() == want,
                      lambda c: f"{c.get('asset', '')}/{Path(c['path']).stem}".lower() == want):  # fmt: skip
            hit = next((c for c in self.clips if match(c) and self.file(c).is_file()), None)
            if hit:
                return hit
        return None

    def samples(self, clip: dict) -> np.ndarray:
        """The clip as mono float64 at 48 kHz (decoded once per library)."""
        if clip["path"] not in self._audio:
            self._audio[clip["path"]] = decode.load(self.file(clip)).mean(axis=1)
        return self._audio[clip["path"]]


def resolve(short, library: Library | None, policy: str = "any") -> list[dict]:
    """For each sfx event, in order: {"kind", "shot", "at_s", "use": "recording" | "synth" | "skipped", "clip",
    "source", "license", "reason"}. Deterministic for a given short and library."""
    used: dict[str, int] = {}
    out = []
    for fx in short.sfx:
        r = {"kind": fx.kind, "shot": fx.shot, "at_s": fx.at_s, "use": "synth", "clip": None, "source": None,
             "license": None, "reason": None}  # fmt: skip
        clips = library.clips_for(fx.kind) if library is not None else []
        designed_only = False
        if policy == "natural-only":
            natural = [c for c in clips if not is_designed(c)]
            designed_only, clips = bool(clips) and not natural, natural
        if policy == "natural-only" and fx.kind in SYNTHETIC_KINDS:
            r.update(use="skipped", reason=f"{fx.kind!r} is a synthetic kind, which sound_policy natural-only forbids")
        elif clips:
            n = used.get(fx.kind, 0)
            used[fx.kind] = n + 1
            clip = clips[(zlib.crc32(fx.kind.encode()) + n) % len(clips)]
            r.update(use="recording", clip=clip["path"], source=clip.get("source"), license=clip.get("license"))
        elif designed_only:
            r.update(use="skipped", reason=f"the only cached {fx.kind!r} clips come from a designed UI pack (tonal "
                                           "interface sounds), which sound_policy natural-only does not use")  # fmt: skip
        elif policy == "natural-only":
            r.update(use="skipped", reason=f"no recording of {fx.kind!r} is cached, and sound_policy natural-only "
                                           "allows no synthesis")  # fmt: skip
        else:
            r["reason"] = f"no recording of {fx.kind!r} is cached"
        out.append(r)
    return out


def prepare(x: np.ndarray, dur_s: float, seed: int = 0) -> tuple[np.ndarray, float]:
    """(the clip ready to place, gain jitter in dB): leading silence trimmed, pitch shifted by a little, cut to dur_s
    with a fade-out, peak-normalised."""
    rng = np.random.default_rng(seed)
    x = np.asarray(x, dtype=np.float64)
    if len(x) == 0 or not np.abs(x).max():
        return np.zeros(0), 0.0
    loud = np.flatnonzero(np.abs(x) > np.abs(x).max() * 10 ** (TRIM_DB / 20))
    x = x[max(0, loud[0] - round(PREROLL_S * SR)) :]
    ratio = 2 ** (rng.uniform(-1, 1) * PITCH_JITTER_ST / 12)
    x = decode.resample(x, SR, SR / ratio)  # played `ratio` times faster: higher and shorter
    n = round(dur_s * SR)
    if len(x) > n:
        x = x[:n].copy()
        k = max(1, min(round(FADE_S * SR), n // 4))
        x[-k:] *= 0.5 * (1 + np.cos(np.linspace(0, np.pi, k)))
    peak = np.abs(x).max() if len(x) else 0.0
    return (x / peak if peak else x), float(rng.uniform(-GAIN_JITTER_DB, GAIN_JITTER_DB))


def _kinds(res: list[dict], use: str) -> list[str]:
    return sorted({r["kind"] for r in res if r["use"] == use})


def report(res: list[dict]) -> dict:
    """What the mix used, for its JSON result: counts, the recordings with their licences, and plain notes."""
    notes = []
    synth, skipped = _kinds(res, "synth"), _kinds(res, "skipped")
    if synth:
        notes.append(f"{sum(r['use'] == 'synth' for r in res)} effect(s) ({', '.join(synth)}) used synthesis: no "
                     "recording of those kinds is cached. Real recordings sound better: CLI assets search sounds "
                     "\"<kind>\", then plan them and get the user's yes.")  # fmt: skip
    if skipped:
        notes.append(f"{sum(r['use'] == 'skipped' for r in res)} effect(s) ({', '.join(skipped)}) were left out: "
                     + "; ".join(dict.fromkeys(r["reason"] for r in res if r["use"] == "skipped")))  # fmt: skip
    recordings = {r["clip"]: {"clip": r["clip"], "source": r["source"], "license": r["license"]}
                  for r in res if r["use"] == "recording"}  # fmt: skip
    return {"recorded": sum(r["use"] == "recording" for r in res), "synthesised": sum(r["use"] == "synth" for r in res),
            "skipped": sum(r["use"] == "skipped" for r in res), "clips": list(recordings.values()), "notes": notes,
            "events": res}  # fmt: skip


def policy_warnings(short, library: Library | None, policy: str = "any") -> list[str]:
    """validate warnings: synthetic kinds under natural-only, and kinds with no cached recording."""
    res = resolve(short, library, policy)
    out = []
    forbidden = sorted({r["kind"] for r in res if r["use"] == "skipped" and r["kind"] in SYNTHETIC_KINDS})
    if forbidden:
        out.append(f"sfx kinds {', '.join(forbidden)} are synthetic and sound_policy natural-only forbids them: the mix "
                   "leaves them out. Use a natural kind (click, thud, impact, scrape, slide, creak...) instead.")
    missing = sorted({r["kind"] for r in res if r["use"] != "recording" and r["kind"] not in forbidden})
    if missing:
        then = ("the mix leaves them out" if policy == "natural-only" else "the mix falls back to synthesis, which "
                "sounds fake next to real recordings")  # fmt: skip
        designed = sorted({r["kind"] for r in res if r["use"] == "skipped" and "designed" in (r["reason"] or "")})
        if designed:
            then += (f" (the cached {', '.join(designed)} clips are designed UI sounds, which natural-only does not "
                     "use)")  # fmt: skip
        out.append(f"no recording is cached for sfx kind(s) {', '.join(missing)}: {then}. Find some with CLI assets "
                   f"search sounds \"{missing[0]}\", plan them and get the user's yes.")  # fmt: skip
    return out
