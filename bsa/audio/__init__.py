"""Sound effects (recorded clips, synthesis as the fallback) and the voice/effects mix. No music, ever."""

from __future__ import annotations

from pathlib import Path


def _cache_dir(short_dir=None, cache_dir=None) -> Path | None:
    if cache_dir is not None:
        return Path(cache_dir)
    if short_dir is not None:
        return Path(short_dir).parent / ".assets-cache"
    from bsa import settings

    ws = settings.load_settings().get("workspace")
    return Path(ws) / ".assets-cache" if ws else None


def _event(e) -> tuple[float, float]:
    if isinstance(e, dict):
        return float(e["t"]), float(e.get("energy", 1.0))
    t, energy = e
    return float(t), float(energy)


def place_clips(events, clips, *, length_s: float, sr: int = 48000, offset_s: float = 0.0, merge_s: float = 0.015,
                ref_energy: float | None = None, floor: float = 0.03, exponent: float = 0.5):  # fmt: skip
    """A mono bed (float64, `length_s` long at `sr`) with one clip per event, for source/sound.py: collisions,
    footfalls, clicks from a simulation.

    `events`: (t_s, energy_J) pairs or dicts with "t" and "energy" (bsa.fields.physics.contact impacts as they are);
    each starts at t + `offset_s` (map simulation time to the video's). `clips`: one clip (1-D samples at `sr`) or a
    list, used in turn so repeats vary. Events closer than `merge_s` are one sound (their energies added, at the
    first's time): a contact that passes down a row in a few milliseconds is heard as one click. Gain is
    (energy / ref_energy) ** exponent (0.5: amplitude follows the impact speed); `ref_energy` defaults to the
    loudest event; set it to one number for every shot so a soft hit sounds soft everywhere. Events under `floor`
    gain are left out. Mix the bed in through sound.py's build_bed (`sound.md`)."""
    import numpy as np

    pool = [np.asarray(clips, float)] if np.ndim(clips) == 1 and len(clips) and np.isscalar(clips[0]) else [
        np.asarray(c, float) for c in clips]  # fmt: skip
    bed = np.zeros(round(length_s * sr))
    merged: list[list[float]] = []
    for t, energy in sorted((_event(e) for e in events), key=lambda te: te[0]):
        if merged and t - merged[-1][0] < merge_s:
            merged[-1][1] += energy
        else:
            merged.append([t, energy])
    if not merged or not pool:
        return bed
    ref = ref_energy if ref_energy else max(e for _, e in merged)
    placed = 0
    for t, energy in merged:
        gain = (max(energy, 0.0) / ref) ** exponent if ref > 0 else 0.0
        if gain < floor:
            continue
        c = pool[placed % len(pool)] * gain
        placed += 1
        at = round((t + offset_s) * sr)
        if at >= len(bed) or at + len(c) <= 0:
            continue
        lo = max(at, 0)
        end = min(at + len(c), len(bed))
        bed[lo:end] += c[lo - at : end - at]
    return bed


def clip(name, *, variant: int | None = None, short_dir=None, cache_dir=None, mono: bool = True):
    """A recorded sound for source/sound.py, as float64 samples at 48 kHz: mono (n,) or, with mono=False,
    (n, channels). `name` is a file path (WAV, or OGG/FLAC/MP3 decoded through Blender), a cached clip (its file name
    without extension, its cache path, or "<asset>/<file stem>"), or an sfx kind ("thud") with `variant` choosing
    among that kind's clips (default the first). Cached clips are the ones `CLI assets download` fetched into the
    workspace's .assets-cache (from `short_dir`'s workspace, else the workspace setting)."""
    from bsa.audio import decode, recorded

    path = Path(str(name))
    if path.is_file():
        data = decode.load(path)
    else:
        cache = _cache_dir(short_dir, cache_dir)
        lib = recorded.Library(cache) if cache is not None else None
        found = lib.find(str(name)) if lib else None
        if found is None and lib is not None:
            clips = lib.clips_for(str(name))
            found = clips[(variant or 0) % len(clips)] if clips else None
        if found is None:
            raise FileNotFoundError(f"no cached sound {str(name)!r}: find recordings with CLI assets search sounds "
                                    f"\"{name}\", plan them and download after the user's yes")  # fmt: skip
        data = decode.load(lib.file(found))
    return data.mean(axis=1) if mono else data
