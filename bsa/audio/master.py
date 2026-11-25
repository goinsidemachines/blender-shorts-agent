"""The loudness master: the final mix brought to a target integrated loudness, true-peak ceiling and (optionally) a
loudness-range limit. Only whole-mix processing: the voice/bed balance the mix chose is never touched. numpy only.

Steps (`master`): measure; scale the whole mix to the target loudness; if that leaves the true peak above the ceiling or
the loudness range above `max_lra_lu`, run one gentle compressor (3:1, at most about 3 dB of gain reduction) and scale
again; finish with the lookahead limiter (bsa.audio.dsp.limit) at the ceiling less a small sample-peak margin. The
gain, limit and measure are repeated (at most three rounds) until the loudness is within `TOLERANCE_LU` of the target
and the true peak is at or under the ceiling.
"""

from __future__ import annotations

import numpy as np

from bsa.audio import dsp

TOLERANCE_LU = 0.5
PEAK_MARGIN_DB = 0.3  # the limiter's sample-peak ceiling sits this far under the true-peak ceiling
ROUNDS = 3
MAX_REDUCTION_DB = 3.0


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
    base = x
    gain = dsp.db(target - before["integrated_lufs"])
    sample_ceiling = ceiling - PEAK_MARGIN_DB
    cur, now = x, before
    for rounds in range(1, ROUNDS + 1):
        cur = dsp.limit(base * gain, sample_ceiling)
        now = dsp.measure(cur)
        too_wide = max_lra is not None and now["lra_lu"] > max_lra
        if (too_wide or now["true_peak_dbtp"] > ceiling) and not report["compressed"] and rounds < ROUNDS:
            base = dsp.compress(x, MAX_REDUCTION_DB)  # squeeze once, from the untouched mix, then scale again
            report["compressed"] = True
            gain = dsp.db(target - dsp.gated_lufs(base))
            continue
        report["rounds"] = rounds
        off = target - now["integrated_lufs"]
        over = now["true_peak_dbtp"] - ceiling
        if abs(off) <= TOLERANCE_LU and over <= 0:
            break
        if over > 0:
            sample_ceiling -= over + 0.05  # the limiter works on samples: bring it down by what the true peak shows
        gain *= dsp.db(off)
    else:
        cur = dsp.limit(base * gain, sample_ceiling)
        now = dsp.measure(cur)
    report.update(applied=True, after=now, gain_db=round(now["integrated_lufs"] - before["integrated_lufs"], 2))
    return cur, report


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
