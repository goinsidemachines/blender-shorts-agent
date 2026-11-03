"""Notes left on the review page (or in chat) about a stage, kept in <short>/review/notes.json.

A note starts "open", becomes "sent" when the user sends it to Claude, and ends "done" or "declined" with a
one-line reply. Every change goes through store.update_json."""

from __future__ import annotations

import time
from pathlib import Path

from bsa.review import stages, store

# thumbnail: the candidate's n; look: "style" or "set" (a change to the look preview on the Storyboard stage)
KINDS = ("line", "shot", "still", "frame", "finding", "field", "thumbnail", "look", "overall")
MAX_TEXT = 4000
MAX_REPLY = 300
ANSWERS = ("done", "declined")


def notes_path(sdir: Path) -> Path:
    return Path(sdir) / "review" / "notes.json"


def load(sdir: Path) -> list[dict]:
    return store.read_json(notes_path(sdir), [], list)


def _update(sdir: Path, fn) -> list[dict]:
    path = notes_path(sdir)
    path.parent.mkdir(parents=True, exist_ok=True)
    return store.update_json(path, [], fn, list)


def _find(items: list[dict], note_id: str) -> dict:
    for n in items:
        if n["id"] == note_id:
            return n
    raise ValueError(f"no note {note_id!r}")


def _now_iso(now: float | None) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() if now is None else now))


def add(sdir: Path, stage: str, target: dict, text: str, by: str, now: float | None = None) -> dict:
    if stage not in stages.PAGE_STAGES:
        raise ValueError(f"unknown stage {stage!r}; expected one of {stages.PAGE_STAGES}")
    kind = target.get("kind") if isinstance(target, dict) else None
    if kind not in KINDS:
        raise ValueError(f"unknown note kind {kind!r}; expected one of {KINDS}")
    if by not in ("page", "chat"):
        raise ValueError(f"unknown author {by!r}; expected 'page' or 'chat'")
    if not isinstance(text, str) or not text.strip():
        raise ValueError("a note needs some text")
    if len(text) > MAX_TEXT:
        raise ValueError(f"a note can be at most {MAX_TEXT} characters; this one is {len(text)}")
    keep = {"kind": kind, "id": target.get("id"), "version": target.get("version")}
    if kind == "frame":  # a moment of the video also names the shot on screen (None on the closing card)
        shot = target.get("shot")
        if shot is not None and not isinstance(shot, str):
            raise ValueError(f"a frame note's shot must be a shot id or null, not {shot!r}")
        keep["shot"] = shot
    made: dict = {}

    def fn(items):
        top = max((int(n["id"][1:]) for n in items), default=0)
        made.update(
            {
                "id": f"n{top + 1}",
                "stage": stage,
                "target": keep,
                "text": text,
                "created": _now_iso(now),
                "by": by,
                "status": "open",
                "reply": None,
            }
        )
        return [*items, made]

    _update(sdir, fn)
    return made


def _send_where(sdir: Path, pick) -> list[dict]:
    changed: list[dict] = []

    def fn(items):
        for n in items:
            if n["status"] == "open" and pick(n):
                n["status"] = "sent"
                changed.append(n)
        return items

    _update(sdir, fn)
    return changed


def send(sdir: Path, ids: list[str]) -> list[dict]:
    """Open notes with these ids become sent; returns the notes that changed."""
    return _send_where(sdir, lambda n: n["id"] in ids)


def send_stage(sdir: Path, stage: str) -> list[dict]:
    """Every open note of this stage becomes sent; returns the notes that changed."""
    return _send_where(sdir, lambda n: n["stage"] == stage)


def withdraw(sdir: Path, note_id: str) -> None:
    def fn(items):
        n = _find(items, note_id)
        if n["status"] != "open":
            raise ValueError(f"note {note_id} is {n['status']}; only open notes can be withdrawn")
        return [x for x in items if x is not n]

    _update(sdir, fn)


def answer(sdir: Path, note_id: str, status: str, reply: str) -> dict:
    if status not in ANSWERS:
        raise ValueError(f"status must be one of {ANSWERS}, not {status!r}")
    reply = reply.strip() if isinstance(reply, str) else ""
    if not reply or "\n" in reply or "\r" in reply:
        raise ValueError("the reply must be one non-empty line")
    if len(reply) > MAX_REPLY:
        raise ValueError(f"the reply can be at most {MAX_REPLY} characters; this one is {len(reply)}")
    done: dict = {}

    def fn(items):
        n = _find(items, note_id)
        n["status"], n["reply"] = status, reply
        done.update(n)
        return items

    _update(sdir, fn)
    return done
