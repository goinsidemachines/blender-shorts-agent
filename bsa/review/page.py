"""The one live review page of a short, assembled into one HTML file from the parts in templates/page/.

The CSS and the scripts are inlined, so the page needs nothing from the network. In "server" mode it polls the
page server for its snapshot; in "static" mode (a saved file, used when the server can't start) it carries the
snapshot and its buttons copy the user's answer for the chat."""

from __future__ import annotations

import html
import json
import re
from pathlib import Path

from bsa import feedback

TEMPLATES = Path(__file__).parent / "templates"
PARTS = TEMPLATES / "page"
MODES = ("server", "static")
_SLOT = re.compile(r"@@([A-Z]+)@@")


def _part(name: str) -> str:
    path = PARTS / name
    return path.read_text(encoding="utf-8")


def _script_safe(text: str) -> str:
    """JSON or JS that can sit inside <script>: "</" never ends the element early."""
    return text.replace("</", "<\\/")


def render(mode: str, snapshot: dict | None = None) -> str:
    """The page as one HTML string. `mode` is "server" (live, polling the page server) or "static" (a saved file)."""
    if mode not in MODES:
        raise ValueError(f"unknown page mode {mode!r}; expected one of {MODES}")
    embedded = ""
    if mode == "static":  # a saved file can't fetch its state, so it carries it
        data = json.dumps(snapshot or {}, ensure_ascii=False, default=str)
        embedded = f"<script>window.BSA_SNAPSHOT = {_script_safe(data)};</script>"
    title = ((snapshot or {}).get("short") or {}).get("title") or "Review"
    slots = {
        "TITLE": html.escape(str(title), quote=True),
        "MODE": mode,
        "REPO": html.escape(feedback.REPO, quote=True),
        "SNAPSHOT": embedded,
        "CSS": _part("page.css").replace("</", "<\\/"),
        "QR": _script_safe(_part("qr.js")),
        "REVIEW": _script_safe(_part("review.js")),
        "STAGES": _script_safe(_part("stages.js")),
        "PAGE": _script_safe(_part("page.js")),
    }
    return _SLOT.sub(lambda m: slots[m[1]], _part("page.html"))  # one pass: inserted text is never re-scanned
