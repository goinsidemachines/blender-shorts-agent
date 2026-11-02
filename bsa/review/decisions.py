"""The user's answers on the review page or in chat, kept in <short>/review/decisions.json.

An approval or a request for changes is tied to the version of the stage the user looked at. A stale answer is
refused, and so is a second approval of the same version. The check and the append happen in one store.update_json
call, so two answers arriving at once record only one."""

from __future__ import annotations

import calendar
import time
from collections.abc import Iterable
from pathlib import Path

from bsa.review import stages, store

DECISIONS = ("approve", "changes")
AUTHORS = ("page", "chat", "settings")
ENGINES = ("cycles", "eevee")
_WHERE = {"page": "on the page", "chat": "in the chat", "settings": "from your settings"}


class DecisionRefused(ValueError):
    """The answer can't be recorded; the message says why in one plain line."""


class StaleVersion(DecisionRefused):
    pass


class AlreadyAnswered(DecisionRefused):
    pass


def decisions_path(sdir: Path) -> Path:
    return Path(sdir) / "review" / "decisions.json"


def load(sdir: Path) -> list[dict]:
    return store.read_json(decisions_path(sdir), [], list)


def _update(sdir: Path, fn) -> list[dict]:
    path = decisions_path(sdir)
    path.parent.mkdir(parents=True, exist_ok=True)
    return store.update_json(path, [], fn, list)


def _iso(now: float | None) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() if now is None else now))


def _clock(at: str) -> str:
    """HH:MM, in the user's local time, of a stored UTC timestamp."""
    try:
        return time.strftime("%H:%M", time.localtime(calendar.timegm(time.strptime(at, "%Y-%m-%dT%H:%M:%SZ"))))
    except (ValueError, TypeError):
        return "an earlier time"


def record(
    sdir: Path,
    stage: str,
    version: int,
    decision: str,
    by: str,
    *,
    current: int,
    choice: dict | None = None,
    now: float | None = None,
) -> dict:
    allowed = (*stages.APPROVAL_STAGES, *stages.ACK_STAGES)
    if stage not in allowed:
        raise DecisionRefused(f"{stage!r} has nothing to approve; expected one of {allowed}")
    if decision not in DECISIONS:
        raise DecisionRefused(f"unknown decision {decision!r}; expected one of {DECISIONS}")
    if by not in AUTHORS:
        raise DecisionRefused(f"unknown author {by!r}; expected one of {AUTHORS}")
    if by == "settings" and stage != "engine":
        raise DecisionRefused("only the engine can be answered from settings")
    if stage == "engine" and decision == "approve":
        engine = choice.get("engine") if isinstance(choice, dict) else None
        if engine not in ENGINES:
            raise DecisionRefused(f"approving the engine needs a choice: one of {ENGINES}")
        choice = {"engine": engine}
    elif choice is not None and not isinstance(choice, dict):
        raise DecisionRefused("the choice must be an object")
    label = stages.LABELS[stage]
    made: dict = {}

    def fn(items):
        if version != current:
            raise StaleVersion(
                f"You answered version {version} of the {label}, but it has changed since (now version {current}). "
                "Look at it again, then answer."
            )
        if decision == "approve":
            for d in items:
                if d["stage"] == stage and d["version"] == version and d["decision"] == "approve" and not d["superseded"]:
                    raise AlreadyAnswered(
                        f"The {label} (version {version}) was already approved {_WHERE[d['by']]} at {_clock(d['at'])}."
                    )
        top = max((int(d["id"][1:]) for d in items), default=0)
        made.update(
            {
                "id": f"d{top + 1}",
                "stage": stage,
                "version": version,
                "decision": decision,
                "at": _iso(now),
                "by": by,
                "choice": choice,
                "applied": False,
                "superseded": False,
            }
        )
        return [*items, made]

    _update(sdir, fn)
    return made


def latest(sdir: Path, stage: str) -> dict | None:
    """The newest decision of the stage that is not superseded."""
    live = [d for d in load(sdir) if d["stage"] == stage and not d["superseded"]]
    return live[-1] if live else None


def approved(sdir: Path, stage: str, version: int) -> dict | None:
    """The live approval of this version of the stage, if any."""
    for d in reversed(load(sdir)):
        if d["stage"] == stage and d["version"] == version and d["decision"] == "approve" and not d["superseded"]:
            return d
    return None


def unapplied(sdir: Path) -> list[dict]:
    """Decisions Claude has not acted on yet (not applied, not superseded), oldest first."""
    return [d for d in load(sdir) if not d["applied"] and not d["superseded"]]


def mark_applied(sdir: Path, decision_id: str) -> dict:
    done: dict = {}

    def fn(items):
        for d in items:
            if d["id"] == decision_id:
                d["applied"] = True
                done.update(d)
                return items
        raise ValueError(f"no decision {decision_id!r}")

    _update(sdir, fn)
    return done


def supersede(sdir: Path, stages_: Iterable[str]) -> int:
    """Retire every live decision of these stages (their content changed under them); returns how many."""
    wanted = set(stages_)
    count = [0]

    def fn(items):
        for d in items:
            if d["stage"] in wanted and not d["superseded"]:
                d["superseded"] = True
                count[0] += 1
        return items

    _update(sdir, fn)
    return count[0]
