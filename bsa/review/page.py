"""The one live review page of a short. A placeholder until the real page lands (Task 9)."""

from __future__ import annotations

import html
import json


def render(mode: str, snapshot: dict | None = None) -> str:
    """The page as one HTML string. `mode` is "server" (live, polling the page server) or "static" (a saved file)."""
    embedded = ""
    if mode == "static":  # a saved file can't fetch its state, so it carries it; "</" never closes the script early
        data = json.dumps(snapshot or {}, ensure_ascii=False, default=str).replace("</", "<\\/")
        embedded = f"<script>window.BSA_SNAPSHOT = {data};</script>"
    title = html.escape(((snapshot or {}).get("short") or {}).get("title") or "Review", quote=True)
    return (
        "<!doctype html>\n"
        '<html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>{title}</title></head>"
        f'<body data-mode="{html.escape(mode, quote=True)}"><main><h1>{title}</h1>'
        f"<p>The review page is on its way.</p></main>{embedded}</body></html>\n"
    )
