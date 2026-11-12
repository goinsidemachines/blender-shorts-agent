"""`render.deliver_dir`: a folder, named relative to the short's folder, that gets a copy of the final video and the
chosen thumbnails. The plugin keeps a list of what it put there (`.bsa-delivered.json` in that folder): it overwrites
only those files, never another file the user put there, and never deletes anything.
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path, PurePosixPath, PureWindowsPath

from bsa import postpack, workspace

MANIFEST = ".bsa-delivered.json"


def problems(value) -> str | None:
    """Why `value` can't be render.deliver_dir, or None."""
    if not isinstance(value, str) or not value.strip():
        return "render.deliver_dir must be a folder name inside the short's folder, e.g. final"
    text = value.strip()
    if PurePosixPath(text).is_absolute() or PureWindowsPath(text).is_absolute() or text.startswith(("/", "\\")):
        return f"render.deliver_dir {text!r} must be relative to the short's folder, not a full path"
    parts = [p for p in text.replace("\\", "/").split("/") if p not in ("", ".")]
    if not parts or ".." in parts:
        return f"render.deliver_dir {text!r} must be a folder inside the short's folder"
    if parts[0] in workspace.SHORT_DIRS or parts[0] in ("cards",):
        return f"render.deliver_dir {text!r} is a folder the plugin uses itself: pick another name, e.g. final"
    return None


def folder(sdir: Path, s: dict) -> Path | None:
    value = ((s or {}).get("render") or {}).get("deliver_dir")
    if not value or problems(value):
        return None
    return Path(sdir).joinpath(*[p for p in str(value).replace("\\", "/").split("/") if p not in ("", ".")])


def _sources(sdir: Path, short) -> list[Path]:
    rels = [f"out/{short.slug}.mp4", *postpack.OUT_FILES.values()]
    return [Path(sdir) / r for r in rels if (Path(sdir) / r).is_file()]


def _read(dest: Path) -> dict:
    try:
        data = json.loads((dest / MANIFEST).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _same(a: Path, b: Path) -> bool:
    try:
        return a.stat().st_size == b.stat().st_size and a.read_bytes() == b.read_bytes()
    except OSError:
        return False


def deliver(sdir: Path, short, s: dict) -> dict | None:
    """Copy the final and the thumbnails into the deliver folder. None when render.deliver_dir is not set. Else
    {"dir", "files": [paths], "skipped": [{"file", "why"}], "warnings": [...]}; a file that is already the same is
    listed and left. Never raises for a copy that fails: that is a warning."""
    dest = folder(sdir, s)
    if dest is None:
        return None
    sources = _sources(sdir, short)
    res = {"dir": str(dest), "files": [], "skipped": [], "warnings": []}
    mine = set(_read(dest).get("files") or [])
    try:
        dest.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        res["warnings"].append(f"deliver folder {dest} couldn't be made: {exc.strerror or exc}")
        return res
    if not any(p.name.startswith(f"{short.slug}.") for p in sources):
        res["warnings"].append(f"nothing to deliver yet: out/{short.slug}.mp4 is missing")
    for src in sources:
        target = dest / src.name
        if target.exists() and src.name not in mine and not _same(src, target):
            res["skipped"].append({"file": str(target), "why": "not put there by the plugin: left as it is"})
            res["warnings"].append(f"{target} is not one the plugin put there, so it was not replaced: rename or "
                                   "move it, then run CLI deliver again")  # fmt: skip
            continue
        try:
            tmp = target.with_name(f"{target.name}.{os.getpid()}.tmp")
            shutil.copy2(src, tmp)
            os.replace(tmp, target)
        except OSError as exc:
            res["warnings"].append(f"{src.name} was not copied to {dest}: {exc.strerror or exc}")
            continue
        mine.add(src.name)
        res["files"].append(str(target))
    try:
        (dest / MANIFEST).write_text(json.dumps({"files": sorted(mine)}, indent=2), encoding="utf-8")
    except OSError as exc:
        res["warnings"].append(f"{MANIFEST} not written in {dest}: {exc.strerror or exc}")
    return res
