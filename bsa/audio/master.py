"""The loudness master: the final mix brought to a target integrated loudness, true-peak ceiling and (optionally) a
loudness-range limit. Only whole-mix processing: the voice/bed balance the mix chose is never touched. numpy only.

Steps (`master`): measure; find the gain that puts the limited mix on the target loudness (gain, lookahead limiter
(bsa.audio.dsp.limit) at the ceiling less a small sample-peak margin, measure, then a secant step on the gain that
allows for how much of the change the limiter let through, up to `ROUNDS` times, aiming for `AIM_LU`); if that leaves
the true peak above the ceiling, the loudness short of the target or the loudness range above `max_lra_lu`, run one
gentle compressor (3:1, at most about 3 dB of gain reduction) and search again, keeping the better result.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from bsa.audio import dsp

# Bump whenever the master's behaviour changes (its steps, aims or limits), so a mix mastered by older code is re-made
# before a final edit uses it. 1 is 0.7.0's master (and any mix with no stamp); 2 is 0.7.1's.
MASTER_VERSION = 2
STAMP_NAME = "mix.json"  # next to audio/mix.wav

TOLERANCE_LU = 0.5  # the check on a finished file (verdict)
AIM_LU = 0.15  # what the master itself aims for, so the check has room (a codec moves the level a little)
PEAK_MARGIN_DB = 0.3  # the limiter's sample-peak ceiling sits this far under the true-peak ceiling
ROUNDS = 8  # gain / limit / measure rounds per attempt
MAX_REDUCTION_DB = 3.0


def stamp_path(sdir: Path) -> Path:
    return Path(sdir) / "audio" / STAMP_NAME


def make_stamp(values: dict) -> dict:
    """What `CLI mix` records next to the mix: the resolved master values and the master's code version."""
    return {"master_version": MASTER_VERSION, "master": json.loads(json.dumps(values))}


def write_stamp(sdir: Path, values: dict) -> Path:
    path = stamp_path(sdir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(make_stamp(values), indent=2), encoding="utf-8")
    return path


def stale_reason(sdir: Path, values: dict) -> str | None:
    """Why audio/mix.wav was not mastered as the current master settings and code would (None when it was, or when
    there is no mix). A missing or unreadable stamp counts as stale when the master is on; with the master off only a
    stamp that says the mix was mastered does."""
    sdir = Path(sdir)
    if not (sdir / "audio" / "mix.wav").is_file():
        return None
    try:
        stamp = json.loads(stamp_path(sdir).read_text(encoding="utf-8"))
        if not isinstance(stamp, dict) or not isinstance(stamp.get("master"), dict):
            raise TypeError("not a stamp")
    except (OSError, ValueError, TypeError):
        stamp = None
    if not values["enabled"]:
        if stamp is not None and stamp["master"].get("enabled"):
            return "the master was on when audio/mix.wav was made and is off now"
        return None
    if stamp is None:
        return f"there is no readable audio/{STAMP_NAME} (the mix was made before the loudness master was stamped)"
    if stamp.get("master_version") != MASTER_VERSION:
        return f"the master changed since audio/{STAMP_NAME} (version {stamp.get('master_version')}, now {MASTER_VERSION})"
    if stamp["master"] != json.loads(json.dumps(values)):
        return f"the master settings changed since audio/{STAMP_NAME}"
    return None


def targets(values: dict) -> dict:
    return {"integrated_lufs": values["integrated_lufs"], "true_peak_dbtp": values["true_peak_dbtp"],
            "max_lra_lu": values.get("max_lra_lu")}  # fmt: skip


def master(x: np.ndarray, values: dict) -> tuple[np.ndarray, dict]:
    """(the mastered stereo mix, a report). `values` are the resolved master settings (bsa.presets.master_resolved).
    The report has "applied", "gain_db" (the net level change), "compressed", "rounds", "before" and "after" (each
    {integrated_lufs, true_peak_dbtp, lra_lu}) and "targets". A silent mix (nothing above the -70 LUFS gate) is
    returned as it is."""
    target, ceiling, max_lra = values["integrated_lufs"], values["true_peak_dbtp"], values.get("max_lra_lu")
    before = dsp.measure(x)
    report = {"applied": False, "gain_db": 0.0, "compressed": False, "rounds": 0, "before": before, "after": before,
              "targets": targets(values)}  # fmt: skip
    if before["integrated_lufs"] <= dsp.FLOOR_DB:
        report["note"] = "the mix is silent: nothing to master"
        return x, report
    gain0 = dsp.db(target - before["integrated_lufs"])
    best, rounds = _search(x, gain0, target, ceiling, max_lra)
    report["rounds"] = rounds
    if not _good(best, target, ceiling, max_lra):
        squeezed = dsp.compress(x, MAX_REDUCTION_DB)  # squeeze once, from the untouched mix, then search again
        alt, more = _search(squeezed, dsp.db(target - dsp.gated_lufs(squeezed)), target, ceiling, max_lra)
        report["rounds"] += more
        if _score(alt, target, ceiling, max_lra) < _score(best, target, ceiling, max_lra):
            best, report["compressed"] = alt, True
    cur, now = best
    report.update(applied=True, after=now, gain_db=round(now["integrated_lufs"] - before["integrated_lufs"], 2))
    return cur, report


def _score(result, target, ceiling, max_lra) -> tuple:
    """Lower is better: a true peak over the ceiling first, then a loudness range over the limit, then the distance
    from the target loudness."""
    _, m = result
    over = max(m["true_peak_dbtp"] - ceiling, 0.0)
    wide = max(m["lra_lu"] - max_lra, 0.0) if max_lra is not None else 0.0
    return (round(over, 1), round(wide, 1), round(abs(m["integrated_lufs"] - target) / AIM_LU))


def _good(result, target, ceiling, max_lra) -> bool:
    over, wide, _ = _score(result, target, ceiling, max_lra)
    m = result[1]
    return over == 0.0 and wide == 0.0 and m["true_peak_dbtp"] <= ceiling and abs(m["integrated_lufs"] - target) <= AIM_LU


def _search(base: np.ndarray, gain: float, target: float, ceiling: float, max_lra) -> tuple[tuple, int]:
    """Find the gain that puts `base`, limited, on the target loudness under the ceiling. Each round applies `gain`,
    limits at `sample_ceiling` and measures; the next gain comes from the measured miss, corrected by how much of the
    last gain change the limiter let through (a secant step), so a peaky mix that the limiter holds down still gets
    its make-up gain. Returns ((mix, measurement), rounds) of the best round."""
    sample_ceiling = ceiling - PEAK_MARGIN_DB
    best = None
    prev = None  # (gain dB, loudness) of the last round at this sample ceiling
    rounds = 0
    for rounds in range(1, ROUNDS + 1):
        cur = dsp.limit(base * gain, sample_ceiling)
        now = dsp.measure(cur)
        cand = (cur, now)
        if best is None or _score(cand, target, ceiling, max_lra) < _score(best, target, ceiling, max_lra):
            best = cand
        off = target - now["integrated_lufs"]
        over = now["true_peak_dbtp"] - ceiling
        if over > 0:
            sample_ceiling -= over + 0.05  # the limiter works on samples: bring it down by what the true peak shows
            prev = None
            continue
        if abs(off) <= AIM_LU:
            break
        slope = 1.0
        if prev is not None and abs(gain_db := 20 * np.log10(gain) - prev[0]) > 1e-6:
            slope = float(np.clip((now["integrated_lufs"] - prev[1]) / gain_db, 0.15, 1.0))
        prev = (20 * np.log10(gain), now["integrated_lufs"])
        gain *= dsp.db(off / slope)
    return best, rounds


def verdict(measured: dict, values: dict) -> tuple[bool, list[str]]:
    """(ok, what is off) for a measured file against the targets: loudness more than 0.5 LU away, a true peak more
    than 0.2 dB over the ceiling, a loudness range over `max_lra_lu`."""
    problems = []
    off = measured["integrated_lufs"] - values["integrated_lufs"]
    if abs(off) > TOLERANCE_LU:
        problems.append(f"loudness is {measured['integrated_lufs']:.1f} LUFS, {abs(off):.1f} LU "
                        f"{'over' if off > 0 else 'under'} the {values['integrated_lufs']:g} LUFS target")  # fmt: skip
    over = measured["true_peak_dbtp"] - values["true_peak_dbtp"]
    if over > 0.2:
        problems.append(f"true peak is {measured['true_peak_dbtp']:.1f} dBTP, {over:.1f} dB over the "
                        f"{values['true_peak_dbtp']:g} dBTP ceiling")  # fmt: skip
    lra = values.get("max_lra_lu")
    if lra is not None and measured["lra_lu"] > lra:
        problems.append(f"loudness range is {measured['lra_lu']:.1f} LU, over the {lra:g} LU limit")
    return not problems, problems
