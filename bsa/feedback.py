"""Plugin feedback: problems in the plugin itself, noted in <short>/review/plugin-feedback.json. Stdlib only.

Nothing here sends anything. `report` builds a redacted Markdown issue body and a prefilled GitHub "new issue" link;
the user reviews it and clicks Submit on GitHub themselves, only if they choose to.
"""

from __future__ import annotations

import json
import os
import platform
import re
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlencode

SEVERITIES = ("blocking", "wrong-output", "rough-edge")
FIELDS = ("happened", "expected", "command", "error", "workaround", "stage")
LABELS = {"happened": "What happened", "expected": "Expected", "command": "Command", "error": "Error line",
          "workaround": "Workaround", "stage": "Stage"}  # fmt: skip
REPO = "https://github.com/goinsidemachines/blender-shorts-agent"
TEMPLATE = "plugin_feedback.yml"
LABEL = "plugin-feedback"
URL_LIMIT = 7000  # GitHub and browsers cut longer links; the page holds the full text to paste
PLUGIN_JSON = Path(__file__).resolve().parent.parent / ".claude-plugin" / "plugin.json"
SCRUB_MIN = 4  # setting values shorter than this are left alone (they would blank out ordinary words)

_SECRETS = [
    (re.compile(r"\bsk-[A-Za-z0-9_\-]{8,}"), "<redacted>"),
    (re.compile(r"\b(?:ghp|gho|ghs|ghu|github_pat|xox[abpr])_?[A-Za-z0-9_\-]{10,}"), "<redacted>"),
    (re.compile(r"(?i)\bBearer\s+\S+"), "Bearer <redacted>"),
    (re.compile(r"(?i)\b([A-Za-z_\-]*(?:api[_\-]?key|key|token|secret|password|passwd|pwd))(\s*[=:]\s*)"
                r"(?!<redacted>)[\"']?[^\s\"',;&)\]}]+[\"']?"), r"\1\2<redacted>"),
]
_LONG = re.compile(r"[A-Za-z0-9+/=_\-.]{32,}")


def feedback_path(short_dir: Path) -> Path:
    return Path(short_dir) / "review" / "plugin-feedback.json"


def _load(short_dir: Path) -> dict:
    try:
        data = json.loads(feedback_path(short_dir).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    if not isinstance(data.get("entries"), list):
        data["entries"] = []
    return data


def _save(short_dir: Path, data: dict) -> None:
    path = feedback_path(short_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def plugin_version() -> str | None:
    try:
        return str(json.loads(PLUGIN_JSON.read_text(encoding="utf-8")).get("version") or "") or None
    except (OSError, ValueError, AttributeError):
        return None


def environment(blender: str | None = None, mode: str | None = None) -> dict:
    """What the authors need to reproduce it, and nothing about the user: plugin, Blender, OS, script or MCP mode."""
    return {"plugin": plugin_version(), "blender": blender or None, "os": f"{platform.system()} {platform.release()}",
            "mode": mode or None}  # fmt: skip


# --------------------------------------------------------------------------- entries


def entries(short_dir: Path) -> list[dict]:
    return [e for e in _load(short_dir)["entries"] if isinstance(e, dict)]


def pending(short_dir: Path) -> list[dict]:
    """Entries not shown in a report and not declined."""
    return [e for e in entries(short_dir) if e.get("status", "new") == "new"]


def counts(items: list[dict]) -> dict:
    out: dict[str, int] = {}
    for e in items:
        out[e.get("severity", "rough-edge")] = out.get(e.get("severity", "rough-edge"), 0) + 1
    return out


def should_ask(short_dir: Path, setting: str | None) -> bool:
    """Ask the user about sending the notes: the setting is `ask`, something is pending and this short has not been
    asked yet (once per short)."""
    return (setting or "ask") == "ask" and not _load(short_dir).get("asked") and bool(pending(short_dir))


def add(short_dir: Path, severity: str, summary: str, *, happened=None, expected=None, command=None, error=None,
        workaround=None, stage=None, blender=None, mode=None, short_paths: dict | None = None) -> dict:  # fmt: skip
    """Note one problem in the plugin itself. Text is stored redacted (short_paths: workspace/home for redact)."""
    severity = str(severity or "").strip().lower()
    if severity not in SEVERITIES:
        raise ValueError(f"{severity!r} is not a severity: use one of {', '.join(SEVERITIES)}")
    summary = str(summary or "").strip()
    if not summary:
        raise ValueError("a feedback note needs a one-line summary")
    paths = {"short_dir": short_dir, **(short_paths or {})}
    data = _load(short_dir)
    ids = [int(e["id"][1:]) for e in data["entries"] if isinstance(e, dict) and re.fullmatch(r"f\d+", str(e.get("id")))]
    entry = {"id": f"f{max(ids, default=0) + 1}", "time": _now(), "severity": severity,
             "summary": redact(summary, **paths)}  # fmt: skip
    values = {"happened": happened, "expected": expected, "command": command, "error": error,
              "workaround": workaround, "stage": stage}  # fmt: skip
    for key in FIELDS:
        text = str(values[key]).strip() if values[key] is not None else ""
        entry[key] = redact(text, **paths) if text else None
    entry["context"] = environment(blender, mode)
    entry["status"] = "new"
    data["entries"].append(entry)
    _save(short_dir, data)
    return entry


def _mark(short_dir: Path, status: str) -> int:
    data = _load(short_dir)
    n = 0
    for e in data["entries"]:
        if isinstance(e, dict) and e.get("status", "new") == "new":
            e["status"] = status
            e[f"{status}_at"] = _now()
            n += 1
    data["asked"] = data.get("asked") or _now()
    _save(short_dir, data)
    return n


def decline(short_dir: Path) -> int:
    """The user said no: mark every pending entry declined (they stay on disk; this short is not asked again)."""
    return _mark(short_dir, "declined")


# --------------------------------------------------------------------------- redaction


def _path_forms(path) -> list[str]:
    text = str(path).rstrip("\\/")
    if not text:
        return []
    return list(dict.fromkeys([text, text.replace("\\", "/"), text.replace("/", "\\")]))


def _secret(match: re.Match) -> str:
    word = match.group(0)
    if "<" in word or ">" in word:
        return word
    has_digit = any(c.isdigit() for c in word)
    has_alpha = any(c.isalpha() for c in word)
    return "<redacted>" if has_digit and has_alpha else word


def redact(text, *, short_dir=None, workspace=None, home=None, scrub=()) -> str:
    """`text` with the short's folder, the workspace and the home folder replaced by <short>, <workspace> and <home>
    (either slash, any case), the user name in other paths by <user>, anything that looks like a key or token by
    <redacted>, and every `scrub` string (the user's setting values) by <setting>."""
    if text is None:
        return ""
    out = str(text)
    home = Path.home() if home is None else home
    swaps = []
    for path, mark in ((short_dir, "<short>"), (workspace, "<workspace>"), (home, "<home>")):
        if path:
            swaps += [(form, mark) for form in _path_forms(path)]
    for form, mark in sorted(swaps, key=lambda s: -len(s[0])):
        out = re.sub(re.escape(form), lambda _m, m=mark: m, out, flags=re.IGNORECASE)
    if short_dir:
        name = Path(short_dir).name
        if len(name) >= SCRUB_MIN:
            out = re.sub(re.escape(name), "<short>", out, flags=re.IGNORECASE)
    user = Path(str(home)).name
    if len(user) >= 2:
        out = re.sub(rf"(?i)(?<=[\\/]){re.escape(user)}(?=[\\/]|\b)", "<user>", out)
    for word in sorted({str(s).strip() for s in scrub if s and len(str(s).strip()) >= SCRUB_MIN}, key=len,
                       reverse=True):  # fmt: skip
        out = re.sub(re.escape(word), "<setting>", out, flags=re.IGNORECASE)
    for pattern, repl in _SECRETS:
        out = pattern.sub(repl, out)
    return _LONG.sub(_secret, out)


def settings_scrub(s: dict) -> list[str]:
    """Every user value in the settings that must never reach a report: handle, website, end card, rules, voice
    command and label, voice, brand, closing card, keys and the workspace."""
    out: list[str] = []

    def walk(v):
        if isinstance(v, dict):
            for x in v.values():
                walk(x)
        elif isinstance(v, list):
            for x in v:
                walk(x)
        elif isinstance(v, str) and v.strip():
            out.extend([v.strip(), v.strip().lstrip("@")])

    for key in ("handle", "website", "end_card", "rules", "narrator", "brand", "closing", "freesound", "workspace",
                "blender_exe"):  # fmt: skip
        walk((s or {}).get(key))
    for value in list(out):  # a command's own parts (a script path, a model name) too
        for part in re.split(r"\s+", value):
            if len(part) >= SCRUB_MIN and not part.startswith("-") and not part.startswith("{"):
                out.append(part.strip("\"'"))
    words = {"ask", "none", "brand", "fresh", "cycles", "eevee", "custom", "python", "python3", "true", "false",
             "kokoro", "chatterbox", "elevenlabs"}  # fmt: skip
    return [w for w in dict.fromkeys(out) if w.lower() not in words]


# --------------------------------------------------------------------------- report


def _cell(text) -> str:
    return str(text).replace("\r", "").replace("|", "\\|").replace("\n", "<br>")


def _env_line(env: dict) -> str:
    parts = [f"plugin {env.get('plugin') or '?'}", f"Blender {env.get('blender') or '?'}", env.get("os") or "?",
             f"{env.get('mode') or '?'} mode"]  # fmt: skip
    return " · ".join(parts)


def _entry_md(e: dict) -> str:
    rows = [f"| Severity | {_cell(e.get('severity'))} |"]
    for key in FIELDS:
        if e.get(key):
            value = f"`{_cell(e[key])}`" if key in ("command", "error") and "`" not in e[key] else _cell(e[key])
            rows.append(f"| {LABELS[key]} | {value} |")
    return f"### {e['id']}. {e.get('summary')}\n\n| | |\n|---|---|\n" + "\n".join(rows)


def _title(items: list[dict]) -> str:
    if len(items) == 1:
        text = items[0].get("summary") or "a problem"
        return "Plugin feedback: " + (text if len(text) <= 80 else text[:79] + "…")
    return f"Plugin feedback: {len(items)} problems from a short"


def report(short_dir: Path, *, workspace=None, home=None, scrub=(), mark: bool = False) -> dict:
    """The pending entries (all entries when none are pending) as a redacted issue: {"title", "body" (Markdown, the
    full text), "url" (a GitHub new-issue link with the title and text prefilled, under URL_LIMIT), "shortened" (the
    link holds a shortened text: the full one is on the page to paste), "entries"}. `mark`: mark them reported."""
    items = pending(short_dir) or entries(short_dir)
    paths = {"short_dir": short_dir, "workspace": workspace, "home": home, "scrub": scrub}
    clean = []
    for e in items:
        c = dict(e)
        for key in ("summary", *FIELDS):
            if c.get(key):
                c[key] = redact(c[key], **paths)
        clean.append(c)
    envs = list(dict.fromkeys(_env_line(e.get("context") or {}) for e in clean)) or [_env_line(environment())]
    title = redact(_title(clean), **paths)
    blocks = "\n\n".join(_entry_md(e) for e in clean)
    env_md = "\n".join(f"- {line}" for line in envs)
    body = (f"Noted by Claude while making a short with blender-shorts-agent.\n\n## Problems\n\n{blocks}\n\n"
            f"## Environment\n\n{env_md}\n")  # fmt: skip
    url, shortened = _url(title, blocks, "; ".join(envs), clean)
    if mark and items:
        _mark(short_dir, "reported")
    return {"title": title, "body": body, "url": url, "shortened": shortened, "entries": len(clean),
            "by_severity": counts(clean)}  # fmt: skip


def _link(title: str, entries_text: str, env: str) -> str:
    query = urlencode({"template": TEMPLATE, "labels": LABEL, "title": title, "entries": entries_text,
                       "environment": env})  # fmt: skip
    return f"{REPO}/issues/new?{query}"


def _url(title: str, blocks: str, env: str, items: list[dict]) -> tuple[str, bool]:
    url = _link(title, blocks, env)
    if len(url) <= URL_LIMIT:
        return url, False
    note = "The full text was too long for a link: paste it here from the plugin-feedback page.\n\n"
    lines = [f"- {e['id']} ({e.get('severity')}): {e.get('summary')}" for e in items]
    while True:
        text = note + "\n".join(lines)
        url = _link(title, text, env)
        if len(url) <= URL_LIMIT or not lines:
            break
        lines = lines[:-2] + ["- …"] if len(lines) > 1 and lines[-1] != "- …" else lines[:-1]
    if len(url) > URL_LIMIT:
        url = _link(title[:80], note.strip(), env[:200])
    return url, True


def render_page(rep: dict, out: Path) -> Path:
    """review/plugin-feedback.html: the redacted report with a Copy button and an "Open on GitHub" button."""
    from string import Template

    from bsa.review import pages

    esc = pages._esc
    shortened = ("<p class=\"note\">The full text is too long for a link, so GitHub opens with a shortened version. "
                 "Copy the full text below and paste it into the issue.</p>") if rep.get("shortened") else ""  # fmt: skip
    sev = ", ".join(f"{n} {s}" for s, n in (rep.get("by_severity") or {}).items())
    text = Template((Path(pages.__file__).parent / "templates" / "feedback.html").read_text(encoding="utf-8"))
    html = text.substitute(css=pages._css(), title=esc(rep["title"]), count=esc(rep.get("entries", 0)),
                           severity=esc(sev), url=esc(rep["url"]), body=esc(rep["body"]),
                           shortened=shortened)  # fmt: skip
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    return out
