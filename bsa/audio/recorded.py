"""Recorded sound effects: the library of downloaded clips by sfx kind, and how each `sfx` event gets its sound.

Each event of a kind with cached recordings plays one of them (variants rotate so consecutive events of a kind
differ), trimmed of leading silence, shaped by kind (`shape`: a transient gets a 3 ms fade-in and rings out past its
dur_s; a sustained sound fades in over 0.6 s and decays for 1 s past it, or crossfades into the next sustained
event when one starts within 0.25 s), and given a small random pitch and gain change so repeats don't
sound copied. The mix then levels it by perceived loudness (bsa.audio.dsp), not by peak.

The `room` kind (room tone) is the odd one out: no event plays it. The mix loops one under the whole story as the quiet
floor (`room_clip`, `sound.room_db`). It is a recording only, never synthesised, and allowed under natural-only. A kind with no recording falls back to synthesis
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
from bsa.audio.sfx import ROOM_KIND, TRANSIENT_KINDS

SR = decode.SR
POLICIES = ("any", "natural-only")
SYNTHETIC_KINDS = ("blip", "whoosh", "hum")  # designed tones with no real-world recording behind them
PITCH_JITTER_ST = 0.6  # +- semitones
GAIN_JITTER_DB = 1.5  # +- dB
TRANSIENT_FADE_IN_S = 0.003  # no click at the start of a struck sound
TAIL_S = 0.6  # a transient may ring this long past its dur_s (decaying, with a smooth fade)
END_FADE_S = 0.01  # a clip that ends before its dur_s, or a tail, ends on a short fade
SUSTAINED_FADE_IN_S = 0.6  # an isolated held sound fades in (equal power), at most a third of its dur_s ...
SUSTAINED_FADE_OUT_S = 1.0  # ... and decays over this long PAST its dur_s (the recording runs on)
JOIN_GAP_S = 0.25  # a sustained event starting this soon after another ends (or in its last XFADE_S) joins it
XFADE_S = 0.6  # a join is an equal-power crossfade this long, centred on the boundary (half inside each event)
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
    "source", "license", "reason", "clip_at_s"}. An event that names a cached `clip` plays exactly it (whatever its
    kinds) and does not advance its kind's rotation. Deterministic for a given short and library."""
    used: dict[str, int] = {}
    out = []
    for fx in short.sfx:
        r = {"kind": fx.kind, "shot": fx.shot, "at_s": fx.at_s, "use": "synth", "clip": None, "source": None,
             "license": None, "reason": None, "clip_at_s": float(getattr(fx, "clip_at_s", 0.0) or 0.0)}  # fmt: skip
        named = str(getattr(fx, "clip", "") or "")
        if named and library is not None:
            hit = library.find(named)
            if hit is not None and policy == "natural-only" and is_designed(hit):
                r.update(use="skipped", reason=f"the named clip {named!r} is from a designed UI pack (tonal "
                                               "interface sounds), which sound_policy natural-only does not use")  # fmt: skip
                out.append(r)
                continue
            if hit is not None:
                r.update(use="recording", clip=hit["path"], source=hit.get("source"), license=hit.get("license"))
                out.append(r)
                continue
        missing_note = f"clip {named!r} is not cached; " if named else ""
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
        if missing_note:
            if r["use"] == "recording":
                r["reason"] = f"{missing_note}used a cached {fx.kind} clip instead"
            else:
                r["reason"] = missing_note + (r["reason"] or "")
        out.append(r)
    return out


def shape(x: np.ndarray, dur_s: float, kind: str | None = None, tail_s: float = TAIL_S, lead_s: float = 0.0,
          join_in: bool = False, join_out: bool = False) -> np.ndarray:  # fmt: skip
    """The envelope of an effect of `kind`.

    Transient kinds (impact, thud, click, blip): a ~3 ms fade-in, then the sound is left alone. Past `dur_s` its own
    tail keeps ringing, for up to `tail_s`, under a smooth fade to nothing (the recording is not chopped at dur_s).
    Transients are never joined.
    Sustained kinds (everything else): the result is `lead_s + dur_s + extension` long, where `x` supplies the extra
    samples (a shorter `x` just gives a shorter result). An isolated start fades in equal-power over 0.6 s (at most a
    third of dur_s); an isolated end decays equal-power over SUSTAINED_FADE_OUT_S (1.0 s) PAST dur_s, so the sound
    runs on and dies away instead of stopping (with no samples past dur_s it fades inside instead, at most a third).
    A joined side (`join_in`: the event has a partner before it, `join_out`: one after it) is an equal-power
    crossfade of XFADE_S centred on the boundary: the event starts `lead_s` (XFADE_S / 2) early, or runs XFADE_S / 2
    past its end, and the partner's complementary curve keeps the summed power constant (no dip at the cut)."""
    n = round(dur_s * SR)
    if kind in TRANSIENT_KINDS:
        x = np.array(x[: n + round(tail_s * SR)], dtype=np.float64)
        k = min(round(TRANSIENT_FADE_IN_S * SR), len(x) // 4)
        if k:
            x[:k] *= np.sin(np.linspace(0, np.pi / 2, k)) ** 2
        if len(x) > n:  # ring out past dur_s, fading smoothly
            x[n:] *= 0.5 * (1 + np.cos(np.linspace(0, np.pi, len(x) - n)))
        else:
            e = min(round(END_FADE_S * SR), len(x) // 4)
            if e:
                x[-e:] *= 0.5 * (1 + np.cos(np.linspace(0, np.pi, e)))
        return x
    lead = round(lead_s * SR) if join_in else 0
    post = round((XFADE_S / 2 if join_out else SUSTAINED_FADE_OUT_S) * SR)
    x = np.array(x[: lead + n + post], dtype=np.float64)
    ext = max(0, len(x) - lead - n)
    if join_in:
        fi = min(lead + round(XFADE_S / 2 * SR), len(x))
    else:
        fi = min(round(SUSTAINED_FADE_IN_S * SR), n // 3, len(x))
    if join_out:
        fo = min(round(XFADE_S * SR), len(x) // 2)
    elif ext:
        fo = ext
    else:
        fo = min(round(SUSTAINED_FADE_OUT_S * SR), len(x) // 3)
    if fi:
        x[:fi] *= np.sin(np.linspace(0, np.pi / 2, fi))
    if fo:
        x[-fo:] *= np.cos(np.linspace(0, np.pi / 2, fo))
    return x


def prepare(x: np.ndarray, dur_s: float, seed: int = 0, kind: str | None = None, lead_s: float = 0.0,
            join_in: bool = False, join_out: bool = False) -> tuple[np.ndarray, float]:  # fmt: skip
    """(the clip ready to place, gain jitter in dB): leading silence trimmed, pitch shifted by a little, shaped for
    its kind (`shape`: no kind = sustained; a joined sustained clip is longer than dur_s, starting `lead_s` early),
    peak-normalised as a safety step (the mix levels by loudness)."""
    rng = np.random.default_rng(seed)
    x = np.asarray(x, dtype=np.float64)
    if len(x) == 0 or not np.abs(x).max():
        return np.zeros(0), 0.0
    loud = np.flatnonzero(np.abs(x) > np.abs(x).max() * 10 ** (TRIM_DB / 20))
    x = x[max(0, loud[0] - round(PREROLL_S * SR)) :]
    ratio = 2 ** (rng.uniform(-1, 1) * PITCH_JITTER_ST / 12)
    x = decode.resample(x, SR, SR / ratio)  # played `ratio` times faster: higher and shorter
    x = shape(x, dur_s, kind, lead_s=lead_s, join_in=join_in, join_out=join_out)
    peak = np.abs(x).max() if len(x) else 0.0
    return (x / peak if peak else x), float(rng.uniform(-GAIN_JITTER_DB, GAIN_JITTER_DB))


def room_clip(library: Library | None, policy: str = "any") -> dict | None:
    """The cached `room` recording the mix loops under the story (the first by path; designed UI clips are left out
    under natural-only), or None."""
    clips = library.clips_for(ROOM_KIND) if library is not None else []
    if policy == "natural-only":
        clips = [c for c in clips if not is_designed(c)]
    return clips[0] if clips else None


def room_status(library: Library | None, policy: str = "any", room_db: float | None = None) -> dict:
    """What the mix did about room tone, for its JSON result."""
    if room_db is None:
        return {"enabled": False, "used": False, "note": "sound.room_db is off: no room tone floor"}
    clip = room_clip(library, policy)
    if clip is None:
        return {"enabled": True, "used": False, "level_db": room_db, "note": _ROOM_MISSING}
    return {"enabled": True, "used": True, "level_db": room_db, "clip": clip["path"], "source": clip.get("source"),
            "license": clip.get("license")}  # fmt: skip


_ROOM_MISSING = ("no room tone recording is cached, so the mix has no quiet floor: effects and pauses drop to digital "
                 "silence between sounds. Find one with CLI assets search sounds \"room tone\", plan it and get the "
                 "user's yes.")


def _kinds(res: list[dict], use: str) -> list[str]:
    return sorted({r["kind"] for r in res if r["use"] == use})


def report(res: list[dict], room: dict | None = None) -> dict:
    """What the mix used, for its JSON result: counts, the recordings with their licences, and plain notes. `room`
    (room_status) adds the room tone: its clip is listed with the others and a missing one is noted."""
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
    if room is not None:
        if room.get("used"):
            recordings[room["clip"]] = {"clip": room["clip"], "source": room["source"], "license": room["license"]}
        elif room.get("enabled"):
            notes.append(room["note"])
    out = {"recorded": sum(r["use"] == "recording" for r in res), "synthesised": sum(r["use"] == "synth" for r in res),
           "skipped": sum(r["use"] == "skipped" for r in res), "clips": list(recordings.values()), "notes": notes,
           "events": res}  # fmt: skip
    if room is not None:
        out["room"] = room
    return out


def policy_warnings(short, library: Library | None, policy: str = "any", room: bool = False) -> list[str]:
    """validate warnings: synthetic kinds under natural-only, kinds with no cached recording and, when `room` (the
    sound.room_db setting is on), no cached room tone."""
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
    absent = sorted({str(fx.clip) for fx in short.sfx
                     if getattr(fx, "clip", "") and (library is None or library.find(fx.clip) is None)})  # fmt: skip
    if absent:
        out.append(f"sfx clip(s) {', '.join(repr(a) for a in absent)} are named but not cached, so those events use "
                   f"a clip of their kind (or synthesis) instead. Find them with CLI assets search sounds "
                   f"\"<what it is>\", plan them and get the user's yes, then download.")  # fmt: skip
    if room and room_clip(library, policy) is None:
        out.append(_ROOM_MISSING[0].upper() + _ROOM_MISSING[1:])
    return out
