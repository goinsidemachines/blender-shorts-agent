"""Stage versions: a stage's version goes up only when what the user sees of it changes.

The fingerprint is a hash of the stage's visible content, including the bytes of every file it points at. The
versions of a short live in <short>/review/versions.json as {stage: [fingerprint, ...]}; a version is the 1-based
position of a fingerprint in its stage's list."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from bsa.review import store

_digests: dict[tuple[str, int, int], str] = {}


def versions_path(sdir: Path) -> Path:
    return Path(sdir) / "review" / "versions.json"


def _file_digest(sdir: Path, rel: str) -> str:
    """The sha256 of a file inside the short, or "missing" (also for a path that leaves the short)."""
    root = Path(sdir).resolve()
    path = (root / rel).resolve()
    if root != path and root not in path.parents:
        return "missing"
    try:
        st = path.stat()
    except OSError:
        return "missing"
    key = (str(path), st.st_size, st.st_mtime_ns)
    hit = _digests.get(key)
    if hit is None:
        try:
            hit = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError:
            return "missing"
        _digests[key] = hit
    return hit


def _files(node, found: list[str]) -> None:
    if isinstance(node, dict):
        if isinstance(node.get("file"), str):
            found.append(node["file"])
        for v in node.values():
            _files(v, found)
    elif isinstance(node, list):
        for v in node:
            _files(v, found)


def fingerprint(content: dict, sdir: Path) -> str:
    """sha256 of the content as canonical JSON, plus the digest of every nested {"file": path}'s bytes."""
    paths: list[str] = []
    _files(content, paths)
    body = {"content": content, "files": [[p, _file_digest(sdir, p)] for p in paths]}
    raw = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def version_of(sdir: Path, stage: str, fp: str) -> int:
    """The 1-based version of this fingerprint in the stage; a new fingerprint is appended as the next version."""
    path = versions_path(sdir)
    path.parent.mkdir(parents=True, exist_ok=True)
    found: list[int] = []

    def fn(data):
        seen = data.setdefault(stage, [])
        if not isinstance(seen, list):
            raise store.StoreDamaged(
                f"{path} is damaged (stage {stage!r} is not a list): move it aside to start it over"
            )
        if fp not in seen:
            seen.append(fp)
        found.append(seen.index(fp) + 1)
        return data

    store.update_json(path, {}, fn, dict)
    return found[0]
