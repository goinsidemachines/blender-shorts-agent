"""Moving the workspace (`CLI workspace move NEW`): what refuses a move, which stored paths it repoints, and what it
checks after a copy. Pure: the CLI does the renaming, copying and saving.

Paths are compared with os.path.normcase(os.path.normpath(...)), so on Windows `D:\\Shorts`, `d:\\shorts` and
`D:\\Shorts\\` are one folder. Only absolute path text is ever treated as a path: a caption or a voice line is
never repointed.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from bsa import gpulock

FINGERPRINTS = "blend/short.fingerprints.json"  # rebuilt on the next run: its old paths are not leftovers
PLUGIN_STATE = ("state.json", "state.json.bak")


def _norm(text: str) -> str:
    return os.path.normcase(os.path.normpath(text))


def _is_path(text: str) -> bool:
    return isinstance(text, str) and bool(text.strip()) and os.path.isabs(text)


def _prefix(root: Path) -> str:
    r = _norm(str(root))
    return r if r.endswith(os.sep) else r + os.sep  # a drive root ("d:\\") already ends with one


def under(path_text: str, root: Path) -> bool:
    """`path_text` is `root` itself or a path inside it (never a sibling that only shares the prefix)."""
    if not _is_path(path_text):
        return False
    p = _norm(path_text)
    return p == _norm(str(root)) or p.startswith(_prefix(root))


def repoint(path_text: str, old: Path, new: Path) -> str | None:
    """The same path under `new`, or None when it is not under `old`. The part below the root keeps its own case."""
    if not under(path_text, old):
        return None
    full = os.path.normpath(path_text)  # normcase never changes the length, so the root's length cuts it
    rest = full[len(_norm(str(old))) :].lstrip("\\/")
    return str(Path(new) / rest) if rest else str(Path(new))


def same_folder(a: Path, b: Path) -> bool:
    return _norm(str(a)) == _norm(str(b))


def missing_drive(path: Path) -> str | None:
    """The drive (or network share) of `path` when it is not there at all (unplugged, unmapped), else None."""
    drive = os.path.splitdrive(os.path.abspath(str(path)))[0]
    if not drive:
        return None
    return None if os.path.exists(drive + os.sep) else drive


def same_drive(a: Path, b: Path) -> bool:
    """Both on one drive (case-insensitive drive letters or UNC shares), so a rename can move it."""
    da = os.path.splitdrive(os.path.abspath(str(a)))[0]
    db = os.path.splitdrive(os.path.abspath(str(b)))[0]
    return da.lower() == db.lower()


def _forms(path: Path) -> list[Path]:
    """The path as written and as it really is (a junction or a link resolved), so a folder reached through a
    junction is still seen as inside or around the workspace."""
    try:
        real = Path(os.path.realpath(path))
    except (OSError, ValueError):
        return [path]
    return [path] if same_folder(real, path) else [path, real]


def busy(*, server: dict | None, running: bool, gpu: dict | None) -> str | None:
    """Why the workspace is in use right now (its page server, a job, the GPU lock), or None."""
    if server:
        return (f"the review page server for this workspace is running (pid {server.get('pid')}): close its tabs and "
                "let it stop, then move")  # fmt: skip
    if running:
        return "a render or another job is running in this workspace: wait for it to finish"
    if gpu:
        return f"the GPU is in use by {gpulock.describe_holder(gpu)} in this workspace: wait for it to finish"
    return None


def refusal(old: Path, new: Path, *, server: dict | None, running: bool, gpu: dict | None) -> str | None:
    """Why the workspace OLD can't move to NEW right now, or None when it can."""
    old, new = Path(old), Path(new)
    olds, news = _forms(old), _forms(new)
    if any(same_folder(o, n) for o in olds for n in news):
        return f"{new} is the same folder as the workspace: nothing to move"
    if any(under(str(n), o) for o in olds for n in news):
        return f"{new} is inside the workspace {old}: pick a folder outside it"
    if any(under(str(o), n) for o in olds for n in news):
        return f"the workspace {old} is inside the new folder {new}: pick a folder that is not one of its parents"
    why = busy(server=server, running=running, gpu=gpu)
    if why:
        return why
    if new.exists():
        if not new.is_dir():
            return f"{new} is a file: pick a new or empty folder"
        if any(new.iterdir()):
            return f"{new} already exists and is not empty: pick a new or empty folder"
    return None


def rewrite(value, old: Path, new: Path, at: str = "") -> tuple[object, list[str]]:
    """`value` with every string at any depth that is a path under OLD repointed to NEW (a copy: the input is not
    changed), and the dotted keys changed ("closing.image", "files[1]")."""
    if isinstance(value, str):
        moved = repoint(value, old, new)
        return (moved, [at]) if moved is not None else (value, [])
    keys: list[str] = []
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            out[k], changed = rewrite(v, old, new, f"{at}.{k}" if at else str(k))
            keys += changed
        return out, keys
    if isinstance(value, list):
        items = []
        for i, v in enumerate(value):
            item, changed = rewrite(v, old, new, f"{at}[{i}]")
            items.append(item)
            keys += changed
        return items, keys
    return value, []


def _files(root: Path):
    for dirpath, _dirs, names in os.walk(root):
        for name in names:
            path = Path(dirpath) / name
            yield path, path.relative_to(root).as_posix()


def size_mismatches(old: Path, new: Path) -> list[str]:
    """Relative paths (sorted, with /) of what of OLD is missing in NEW or differs there: a file of another size, a
    folder (empty ones too, as "rel/") that is not a folder there, a broken link with nothing at its place."""
    old, new = Path(old), Path(new)
    bad = []
    for dirpath, dirs, _names in os.walk(old):
        for name in dirs:
            rel = (Path(dirpath) / name).relative_to(old).as_posix()
            if not (new / rel).is_dir():
                bad.append(rel + "/")
    for path, rel in _files(old):
        try:
            size = path.stat().st_size
        except OSError:  # a broken link: there is nothing to size, only its place to keep
            if not os.path.lexists(new / rel):
                bad.append(rel)
            continue
        try:
            if (new / rel).stat().st_size != size:
                bad.append(rel)
        except OSError:
            bad.append(rel)
    return sorted(bad)


def plugin_files(short_dir: Path) -> list[Path]:
    """The JSON files the plugin itself writes in a short's folder and may store absolute paths in: state.json and
    its backup, and every `*.json` under review/ (render status, engine runs, checks, notes, the camera test
    record, the page's history). A move rewrites these; the user's own files (source/short.json ...) are only
    reported. Sorted."""
    sdir = Path(short_dir)
    found = [sdir / name for name in PLUGIN_STATE if (sdir / name).is_file()]
    review = sdir / "review"
    if review.is_dir():
        found += [p for p in review.rglob("*.json") if p.is_file()]
    return sorted(found)


def leftovers(root: Path, old: Path) -> list[tuple[str, str]]:
    """(file relative to root, dotted key) of every string that still names a path under OLD in the `*.json` files
    under root, sorted; the fingerprint cache is skipped (rebuilt on the next run), as is a file that isn't JSON."""
    found = []
    for path, rel in _files(Path(root)):
        if not rel.endswith(".json") or rel == FINGERPRINTS or rel.endswith("/" + FINGERPRINTS):
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        _, keys = rewrite(data, old, Path(root))
        found += [(rel, k) for k in keys]
    return sorted(found)
