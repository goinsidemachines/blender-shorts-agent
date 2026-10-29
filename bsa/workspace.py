"""Workspace and per-short folder layout."""

from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path

SHORT_DIRS = ["source", "stills", "audio/vo", "audio/sfx", "blend", "frames", "review", "assets", "out"]
PLUGIN_NAME = "blender-shorts-agent"
_NUM = re.compile(r"^(\d{2,})-")


def ensure_workspace(root: Path) -> Path:
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    gitignore = root / ".gitignore"
    if not gitignore.exists():
        gitignore.write_text("*\n", encoding="utf-8")
    return root


def _is_plugin_clone(cwd: Path) -> bool:
    try:
        data = json.loads((cwd / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return isinstance(data, dict) and data.get("name") == PLUGIN_NAME


def default_workspace(cwd: Path) -> Path:
    cwd = Path(cwd)
    if _is_plugin_clone(cwd):
        return cwd / "shorts"
    videos = Path.home() / "Videos"
    return videos / "Shorts" if videos.is_dir() else Path.home() / "Shorts"


def slugify(title: str) -> str:
    text = unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode("ascii").lower()
    text = text.replace("'", "")
    return re.sub(r"[^a-z0-9]+", "-", text).strip("-") or "short"


def _numbered(root: Path):
    root = Path(root)
    if not root.is_dir():
        return
    for p in sorted(root.iterdir()):
        m = _NUM.match(p.name)
        if p.is_dir() and m:
            yield int(m.group(1)), p


def _claim_number(root: Path, n: int) -> bool:
    """Reserve number `n` atomically (mkdir is exclusive), so two sessions making shorts at once get different
    numbers even when both saw the same highest folder."""
    claims = root / ".numbers"
    claims.mkdir(exist_ok=True)
    try:
        (claims / f"{n:02d}").mkdir()
    except FileExistsError:
        return False
    return True


def _take_number(root: Path, n: int) -> None:
    """Claim the number the user chose, or raise ValueError saying why it can't be had."""
    if n < 1:
        raise ValueError(f"the short's number must be 1 or more, got {n}")
    used = [p.name for m, p in _numbered(root) if m == n]
    if used:
        raise ValueError(f"number {n:02d} is already used by {', '.join(used)}: choose another number")
    if not _claim_number(root, n):
        raise ValueError(f"number {n:02d} is reserved ({root / '.numbers' / f'{n:02d}'} exists): another session "
                         "is making that short, or a short with it was deleted (then remove that folder to free the "
                         "number)")  # fmt: skip


def new_short(root: Path, title: str, number: int | None = None) -> Path:
    """A new short's folder: the next free number, or `number` when given (refused when a short has it or another
    session has claimed it)."""
    root = ensure_workspace(root)
    if number is not None:
        _take_number(root, int(number))
        nxt = int(number)
    else:
        nxt = max((n for n, _ in _numbered(root)), default=0) + 1
        while not _claim_number(root, nxt) or any(n == nxt for n, _ in _numbered(root)):
            nxt += 1
    short = root / f"{nxt:02d}-{slugify(title)}"
    for d in SHORT_DIRS:
        (short / d).mkdir(parents=True, exist_ok=True)
    return short


def find_short(root: Path, key: str) -> Path:
    """The short whose folder name, number or slug is `key`. A folder name wins; a number or slug that fits more
    than one short is refused with the matches (two shorts can share a slug: "Pendulum" made twice)."""
    key = key.strip().lower()
    shorts = list(_numbered(root))
    exact = [p for _, p in shorts if key == p.name.lower()]
    if exact:
        return exact[0]
    found = [p for n, p in shorts if key == p.name.split("-", 1)[1].lower() or (key.isdigit() and int(key) == n)]
    if len(found) > 1:
        names = ", ".join(p.name for p in found)
        raise ValueError(f"{key!r} matches {len(found)} shorts ({names}): use the short's number or full folder name")
    if found:
        return found[0]
    raise FileNotFoundError(f"no short matching {key!r} in {root}")


def resolve_short(key: str, workspace: Path | None) -> Path:
    """A short's folder from a path to it, or from its number, slug or folder name inside the workspace."""
    path = Path(key)
    if path.is_dir() and (path / "source").is_dir():
        return path.resolve()
    if not workspace:
        raise FileNotFoundError(f"no short {key!r}: no workspace is set yet (settings set workspace PATH)")
    return find_short(Path(workspace), key)
