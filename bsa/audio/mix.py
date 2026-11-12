"""Voice + effects mix.

The chain, in order:

1. Each sfx event is shaped for its kind (bsa.audio.recorded.shape: transients ring out; sustained sounds fade in
   over 0.6 s and decay for 1 s past their dur_s, or crossfade (0.6 s, equal power) into a sustained event that
   starts within 0.25 s after them, so beds join across cuts without a dip) and levelled by perceived loudness: its loudest 400 ms K-weighted momentary loudness (ITU-R BS.1770, numpy,
   bsa.audio.dsp), measured on the event's own dur_s, becomes `level_db`. So a sharp knock and a steady rumble with the same `level_db` seem equally loud.
2. The effects bus (events and `extra_bed`, which is added as given) goes through a 75 Hz high-pass and one short
   synthetic room reverb (`reverb`, wet about -16 dB), so the effects share a space. The voice and the room tone do not.
3. The bus is set to BED_DBFS RMS where it sounds (silence between sparse effects does not count, so a few blips are not
   boosted over the voice) and ducked by DUCK_DB under speech: the gain starts to fall 60 ms before a line starts
   (attack 70 ms, release 0.4 s).
4. Voice normalised to VOICE_DBFS RMS while speaking (pauses do not count).
5. Room tone (a cached `room` recording, `room_db` LUFS) is looped under the whole mix, the closing card included:
   seamless 1 s crossfades, a seeded start, a 0.5 s fade in and a 1 s fade out over the very end. It is the floor, so
   it is never ducked. No cached room clip, or `room_db` None: no floor.
6. A lookahead peak limiter (5 ms lookahead, 100 ms release) with a PEAK_DBFS sample-peak ceiling.

`mix` reports the levels as heard in the result, measured the same way: the voice while speaking, the bed where it
sounds clear of speech.

Convention for sfx `level_db`: LUFS-ish. Before the bus is normalised, each event is scaled so its loudest momentary
loudness is `level_db` LUFS (close to dBFS RMS for steady sounds). So level_db sets the balance between events (and
against `extra_bed`); the summed bus is then normalised as a whole to BED_DBFS.
"""

from __future__ import annotations

import wave
import zlib
from pathlib import Path

import numpy as np

from bsa.audio import dsp, recorded
from bsa.audio.sfx import KINDS, ROOM_KIND, SR, TRANSIENT_KINDS
from bsa.short import Short, line_id_problem
from bsa.timeline import Timeline

VOICE_DBFS = -15
BED_DBFS = -23
DUCK_DB = -10
PEAK_DBFS = -1
DUCK_LOOKAHEAD_S = 0.06
DUCK_ATTACK_S = 0.07
DUCK_RELEASE_S = 0.4
ROOM_DB = -50.0  # default sound.room_db: room tone, LUFS-ish
ROOM_FADE_S = 0.5  # room tone fades in over this at the start ...
ROOM_FADE_OUT_S = 1.0  # ... and out over this at the very end of the mix (under the closing card)
ROOM_XFADE_S = 1.0
EVENT_CREST_DB = 26.0  # safety: an event's peak never ends more than this above its level_db
BUS_FADE_S = 1.0  # the bus ends on an equal-power fade-out that finishes exactly where the story ends
DUCK_S = 0.25  # "clear of speech" in the levels: this far from any speech
_SPEECH_THRESHOLD = 0.01
_BED_ACTIVE_DB = -40  # the bed "sounds" where its 50 ms envelope is within this of its loudest
_FLOOR_DB = dsp.FLOOR_DB
_db = dsp.db
_to_db = dsp.to_db


def _moving_average(x: np.ndarray, width: int) -> np.ndarray:
    """Centred box average over `width` samples, zero-padded at the ends (cumulative-sum form, O(n))."""
    c = np.concatenate([[0.0], np.cumsum(np.concatenate([x, np.zeros(width)]))])
    lo = width // 2
    i = np.arange(len(x))
    start = np.maximum(i - lo, 0)
    end = np.minimum(i - lo + width, len(x))
    return (c[end] - c[start]) / width


def _envelope(x: np.ndarray) -> np.ndarray:
    return _moving_average(np.abs(x), int(0.05 * SR))


def _active(bed_mono: np.ndarray) -> np.ndarray:
    """Where the bed sounds: its 50 ms envelope within _BED_ACTIVE_DB of its loudest."""
    env = _envelope(bed_mono)
    top = float(env.max()) if len(env) else 0.0
    return env > top * _db(_BED_ACTIVE_DB) if top > 0 else np.zeros(len(bed_mono), dtype=bool)


def _bed_gain(bed: np.ndarray, active: np.ndarray) -> float:
    """Gain putting the bed at BED_DBFS RMS where it sounds, as heard, i.e. after the limiter (a few fixed-point
    steps; the limiter leaves most beds alone, so one step is usually enough)."""
    mono = bed.mean(axis=1)[active]
    if not mono.any():
        return 1.0
    g = _db(BED_DBFS) / np.sqrt(np.mean(mono**2))
    for _ in range(3):
        limited = dsp.limit(bed * g, PEAK_DBFS)
        heard = np.sqrt(np.mean(limited.mean(axis=1)[active] ** 2))
        if abs(heard / _db(BED_DBFS) - 1) < 0.005:
            break
        g *= _db(BED_DBFS) / heard
    return float(g)


def _idx(frame: int, fps: int) -> int:
    """Sample index where 1-based frame `frame` starts."""
    return round((frame - 1) / fps * SR)


def _read_take(path: Path) -> np.ndarray:
    with wave.open(str(path), "rb") as w:
        if (w.getframerate(), w.getnchannels(), w.getsampwidth()) != (SR, 1, 2):
            raise ValueError(
                f"{path.name}: take must be {SR} Hz mono 16-bit "
                f"(got {w.getframerate()} Hz, {w.getnchannels()} channel(s), {8 * w.getsampwidth()}-bit)"
            )
        return np.frombuffer(w.readframes(w.getnframes()), np.int16).astype(np.float64) / 32768


def _voice(tl: Timeline, takes_dir: Path | None, n: int) -> np.ndarray:
    vo = np.zeros(n)
    if takes_dir is None:
        return vo
    for span in tl.lines:
        problem = line_id_problem(span.id)
        if problem:
            raise ValueError(problem)  # a take is read from <takes_dir>/<id>.wav: never from outside it
        path = Path(takes_dir) / f"{span.id}.wav"
        if not path.exists():
            continue
        x = _read_take(path)
        st = _idx(span.f0, tl.fps)
        x = x[: max(0, n - st)]
        vo[st : st + len(x)] += x
    return vo


def _bed(tl: Timeline, short: Short, n: int, extra_bed: np.ndarray | None, library=None,
         policy: str = "any") -> np.ndarray:  # fmt: skip
    """The dry effects bus, stereo (n, 2): every event shaped for its kind and levelled by loudness, plus `extra_bed`."""
    shots = {s.id: s for s in tl.shots}
    bed = np.zeros(n)
    for fx in short.sfx:
        if fx.kind == ROOM_KIND:
            raise ValueError("sfx kind 'room' is not an event: room tone is the floor under the whole story. Set it "
                             "with the sound.room_db setting (CLI settings set sound.room_db -50).")
        if fx.kind not in KINDS:
            raise ValueError(f"unknown sfx kind {fx.kind!r}; valid kinds: {', '.join(sorted(KINDS))}")
    uses = recorded.resolve(short, library, policy)
    for fx in short.sfx:
        if fx.shot not in shots:
            raise ValueError(f"sfx {fx.kind!r} refers to unknown shot {fx.shot!r}")
    starts = [_idx(shots[fx.shot].f0, tl.fps) + round(fx.at_s * SR) for fx in short.sfx]
    joined = _joins(short, uses, starts)
    for i, (fx, use) in enumerate(zip(short.sfx, uses, strict=True)):
        gain_db = 0.0
        if use["use"] == "skipped":
            continue
        st = starts[i]
        if st < 0:
            raise ValueError(f"sfx {fx.kind!r} in shot {fx.shot!r} starts before the video (at_s={fx.at_s})")
        join_in = i in joined.values()  # a partner before this event ...
        join_out = i in joined  # ... or after it
        lead = min(recorded.XFADE_S / 2, st / SR) if join_in else 0.0
        lead_n = round(lead * SR)
        if use["use"] == "recording":
            raw = library.samples(library.find(use["clip"]))[round(use.get("clip_at_s", 0.0) * SR) :]
            x, gain_db = recorded.prepare(raw, fx.dur_s, seed=i, kind=fx.kind, lead_s=lead, join_in=lead_n > 0,
                                          join_out=join_out)  # fmt: skip
        elif fx.kind in TRANSIENT_KINDS:
            x = recorded.shape(KINDS[fx.kind](fx.dur_s, seed=i), fx.dur_s, fx.kind, tail_s=0.0)
        else:  # synthesis can run as long as the join or the decay needs
            post = recorded.XFADE_S / 2 if join_out else recorded.SUSTAINED_FADE_OUT_S
            x = recorded.shape(KINDS[fx.kind](lead + fx.dur_s + post, seed=i), fx.dur_s, fx.kind, lead_s=lead,
                               join_in=lead_n > 0, join_out=join_out)  # fmt: skip
        if not len(x) or not x.any():
            continue
        # level on the event's own body (dur_s), then give the extended parts the same gain
        body = x[lead_n : lead_n + round(fx.dur_s * SR)]
        g = dsp.level_gain(body if body.any() else x, fx.level_db + gain_db, EVENT_CREST_DB)
        x = (x * g)[: max(0, n - (st - lead_n))]
        bed[st - lead_n : st - lead_n + len(x)] += x
    stereo = np.stack([bed, bed], 1)
    if extra_bed is not None:
        eb = np.asarray(extra_bed, dtype=np.float64)
        if eb.ndim == 1:
            eb = np.stack([eb, eb], 1)
        m = min(n, len(eb))
        stereo[:m] += eb[:m]
    return stereo


def _joins(short: Short, uses: list[dict], starts: list[int]) -> dict[int, int]:
    """{earlier event index: later event index} for sustained events that join: the next sustained event to start
    (one partner per side) begins within JOIN_GAP_S after this one ends, or in its last XFADE_S. Transients, skipped
    events and an event that already has a partner before it are left out."""
    sustained = [i for i, (fx, use) in enumerate(zip(short.sfx, uses, strict=True))
                 if use["use"] != "skipped" and fx.kind not in TRANSIENT_KINDS]  # fmt: skip
    joined: dict[int, int] = {}
    for i in sorted(sustained, key=lambda i: (starts[i], i)):
        end = starts[i] + round(short.sfx[i].dur_s * SR)
        lo, hi = end - round(recorded.XFADE_S * SR), end + round(recorded.JOIN_GAP_S * SR)
        later = [j for j in sustained if j != i and starts[j] > starts[i] and lo <= starts[j] <= hi
                 and j not in joined.values()]  # fmt: skip
        if later:
            joined[i] = min(later, key=lambda j: (starts[j], j))
    return joined


def _room(library, policy: str, room_db: float, n: int) -> np.ndarray | None:
    """The room tone floor, stereo (n, 2), or None when there is no room clip to use. Each channel loops the clip from
    its own seeded start, so the floor has a little width. It runs the whole mix (the closing card included): faded in
    over ROOM_FADE_S and out over the last ROOM_FADE_OUT_S."""
    clip = recorded.room_clip(library, policy)
    if clip is None:
        return None
    x = library.samples(clip)
    x = x[np.argmax(np.abs(x) > 0) :] if x.any() else x
    if len(x) < SR // 4 or not x.any():
        return None
    x = x * _db(room_db - dsp.integrated_lufs(x))
    seed = zlib.crc32(clip["path"].encode())
    room = np.zeros((n, 2))
    fi = min(round(ROOM_FADE_S * SR), n // 2)
    fo = min(round(ROOM_FADE_OUT_S * SR), n // 2)
    for c in range(2):
        y = dsp.loop_to_length(x, n, seed=seed + c, xfade_s=ROOM_XFADE_S)
        y[:fi] *= dsp.fade_in_curve(fi)
        y[n - fo :] *= dsp.fade_out_curve(fo)
        room[:, c] = y
    return room


def mix(tl: Timeline, short: Short, takes_dir: Path | None, extra_bed: np.ndarray | None = None, library=None,
        policy: str = "any", closing: tuple[int, Path | None, int | None] | None = None,
        room_db: float | None = ROOM_DB, reverb: bool = True):  # fmt: skip
    """(stereo float64 mix in [-1, 1] of shape (n_samples, 2), levels). n_samples = round(n_frames / fps * SR).
    levels: voice_dbfs (the mix while speaking, None without a voice), bed_dbfs (the mix where the bed sounds clear of
    speech, else where it sounds; None without a bed) and peak_dbfs. `library` (bsa.audio.recorded.Library): sfx
    events play cached recordings of their kind, else synthesis; `policy` "natural-only" leaves out synthetic kinds
    and synthesis (recorded.resolve says what each event used). `room_db` (None = off) loops a cached `room` recording
    under the story at that loudness; `reverb` puts the effects bus (`extra_bed` included) through one shared room.
    `closing` (frames, take or None, the take's first frame) adds the closing card's frames after the story
    (bsa.closing): the closing line in the voice, normalised with the story's lines; no effects bed there (the bus
    ends with the story, after a 1 s fade) but the room tone runs on under the card and fades out over the last second."""
    n_story = round(tl.n_frames / tl.fps * SR)
    n = round((tl.n_frames + (closing[0] if closing else 0)) / tl.fps * SR)
    bed = _bed(tl, short, n, extra_bed, library, policy)
    bed[n_story:] = 0.0
    bed = dsp.effects_bus(bed, reverb=reverb)
    fade = min(round(BUS_FADE_S * SR), n_story)
    bed[n_story - fade : n_story] *= dsp.fade_out_curve(fade)[:, None]
    bed[n_story:] = 0.0  # the effects end with the story (after a 1 s fade): no reverb tail into the closing card
    vo = _voice(tl, takes_dir, n)
    if closing and closing[1] is not None and closing[2] is not None:
        x = _read_take(Path(closing[1]))
        st = _idx(closing[2], tl.fps)
        x = x[: max(0, n - st)]
        vo[st : st + len(x)] += x

    speech = _envelope(vo) > _SPEECH_THRESHOLD
    if speech.any():
        vo *= _db(VOICE_DBFS) / np.sqrt(np.mean(vo[speech] ** 2))
    active = _active(bed.mean(axis=1))
    bed *= _bed_gain(bed, active)
    duck = dsp.duck_gain(speech, DUCK_DB, DUCK_LOOKAHEAD_S, DUCK_ATTACK_S, DUCK_RELEASE_S)
    out = bed * duck[:, None] + vo[:, None]
    if room_db is not None:
        floor = _room(library, policy, room_db, n)
        if floor is not None:
            out += floor  # the floor: not ducked

    out = dsp.limit(out, PEAK_DBFS)
    return out, _levels(out, speech, active)


def render_mix(tl: Timeline, short: Short, takes_dir: Path | None, extra_bed: np.ndarray | None = None) -> np.ndarray:
    """The stereo mix alone (see `mix`)."""
    return mix(tl, short, takes_dir, extra_bed)[0]


def _levels(out: np.ndarray, speech: np.ndarray, active: np.ndarray) -> dict:
    mono = out.mean(axis=1)

    def level(where: np.ndarray):
        return _to_db(float(np.sqrt(np.mean(mono[where] ** 2)))) if where.any() else None

    clear = active & ~(_moving_average(speech.astype(float), int(DUCK_S * SR)) > 0)
    return {
        "voice_dbfs": level(speech),
        "bed_dbfs": level(clear) if clear.any() else level(active),
        "peak_dbfs": _to_db(float(np.abs(out).max()) if out.size else 0.0),
    }


def write_wav(path: Path, stereo: np.ndarray) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    pcm = (np.clip(stereo, -1.0, 1.0) * 32767).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm.tobytes())
