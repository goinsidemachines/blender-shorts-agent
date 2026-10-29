"""Poly Haven: search, a download plan the user can approve, cached verified download, credits.

Only `download` fetches asset files, and it must be called only after the user has said yes to the list that
`format_plan` produced. `search` and `plan` read small JSON from the public API; nothing is written to disk.
"""

from __future__ import annotations

import hashlib
import json
import os
import urllib.request
from collections.abc import Callable
from pathlib import Path, PurePosixPath

from bsa import __version__
from bsa.short import Asset, Short

API = "https://api.polyhaven.com"
USER_AGENT = f"blender-shorts-agent/{__version__}"
TIMEOUT_S = 30
KINDS = ("textures", "hdris", "models")
# Poly Haven file-group key -> role, for the texture maps we use.
TEXTURE_MAPS = {"Diffuse": "diffuse", "Rough": "rough", "nor_gl": "nor_gl", "Displacement": "disp"}
IMAGE_FORMATS = ("jpg", "png")  # preference order for texture maps

Http = Callable[[str], object]  # url -> file-like object with .read(n) and .close()


def _urlopen(url: str):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    return urllib.request.urlopen(req, timeout=TIMEOUT_S)


def _get_json(url: str, http: Http):
    resp = http(url)
    try:
        return json.loads(resp.read().decode("utf-8"))
    finally:
        resp.close()


def search(kind: str, query: str, limit: int = 8, http: Http = _urlopen) -> list[dict]:
    """Assets of `kind` whose name, tags or categories match every word of `query`, best matches first."""
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {KINDS}, got {kind!r}")
    words = query.lower().split()
    found = []
    for asset_id, info in _get_json(f"{API}/assets?t={kind}", http).items():
        name = str(info.get("name", ""))
        fields = {
            "name": name.lower() + " " + asset_id.lower(),
            "tags": " ".join(info.get("tags", [])).lower(),
            "categories": " ".join(info.get("categories", [])).lower(),
        }
        haystack = " ".join(fields.values())
        if not all(w in haystack for w in words):
            continue
        score = sum(2 if w in fields["name"] else 1 for w in words)
        found.append(
            (-score, name, {"id": asset_id, "name": name, "kind": kind, "tags": info.get("tags", []),
                            "categories": info.get("categories", [])})
        )
    found.sort(key=lambda t: (t[0], t[1]))
    return [item for _, _, item in found[:limit]]


def search_with_rules(kind: str, query: str, rules: list[dict], limit: int = 8,
                      http: Http = _urlopen) -> tuple[list[dict], list[dict]]:  # fmt: skip
    """search(), leaving out assets whose name, tags or categories break a hard rule (bsa.rules.asset_conflicts):
    (results, hidden), each hidden one {"id", "rule", "words"}."""
    from bsa.rules import asset_conflicts

    found, hidden = [], []
    for item in search(kind, query, limit=10_000, http=http):
        hits = asset_conflicts(rules, item["name"], [*item["tags"], *item["categories"]])
        if hits:
            hidden.append({"id": item["id"], "rule": hits[0]["rule"]["id"], "words": hits[0]["words"]})
        elif len(found) < limit:
            found.append(item)
    return found, hidden


def check_rules(asset_id: str, rules: list[dict], http: Http = _urlopen) -> None:
    """ValueError when the asset's name, tags or categories (from its /info) break a hard rule. Reads JSON only,
    and only when a rule has asset words (people, animals, faces, logos)."""
    from bsa.rules import KINDS, asset_conflicts, describe

    if not any((k := KINDS.get(str(r.get("kind") or ""))) and k.asset_words for r in rules):
        return
    info = _get_json(f"{API}/info/{asset_id}", http)
    hits = asset_conflicts(rules, f"{info.get('name', '')} {asset_id}",
                           [*info.get("tags", []), *info.get("categories", [])])  # fmt: skip
    if hits:
        h = hits[0]
        raise ValueError(f"{asset_id} is tagged {', '.join(repr(w) for w in h['words'])}, which hard rule "
                         f"{describe(h['rule'])} forbids: pick another asset")  # fmt: skip


def _file(entry: dict, role: str, asset_id: str, rel: str) -> dict:
    return {"url": entry["url"], "bytes": int(entry["size"]), "role": role, "md5": entry.get("md5", ""),
            "path": f"{asset_id}/{rel}"}


def _plan_one(asset_id: str, resolution: str, http: Http, name: str | None) -> Asset:
    data = _get_json(f"{API}/files/{asset_id}", http)
    name = name or asset_id.replace("_", " ").title()
    files: list[dict] = []
    if "blend" in data:
        kind = "models"
        entry = _at_resolution(data["blend"], resolution, asset_id)["blend"]
        files.append(_file(entry, "blend", asset_id, PurePosixPath(entry["url"]).name))
        for rel, inc in entry.get("include", {}).items():
            files.append(_file(inc, "texture", asset_id, rel))
    elif "hdri" in data:
        kind = "hdris"
        entry = _at_resolution(data["hdri"], resolution, asset_id)["hdr"]
        files.append(_file(entry, "hdri", asset_id, PurePosixPath(entry["url"]).name))
    elif "Diffuse" in data:
        kind = "textures"
        for key, role in TEXTURE_MAPS.items():
            if key not in data:
                continue
            by_fmt = _at_resolution(data[key], resolution, asset_id)
            fmt = next((f for f in IMAGE_FORMATS if f in by_fmt), None)
            if fmt is None:
                continue
            files.append(_file(by_fmt[fmt], role, asset_id, PurePosixPath(by_fmt[fmt]["url"]).name))
    else:
        raise ValueError(f"{asset_id}: not a texture, HDRI or model Poly Haven understands")
    return Asset(id=asset_id, name=name, kind=kind, source="Poly Haven", license="CC0", files=files,
                 bytes=sum(f["bytes"] for f in files))


def _at_resolution(by_res: dict, resolution: str, asset_id: str) -> dict:
    if resolution not in by_res:
        raise ValueError(f"{asset_id}: no {resolution} version (available: {', '.join(sorted(by_res))})")
    return by_res[resolution]


def plan(
    ids: list[str], resolution: str = "2k", http: Http = _urlopen, names: dict[str, str] | None = None
) -> list[Asset]:
    """What would be downloaded for `ids`, as Assets (CC0, Poly Haven). Reads API JSON only.

    `names` maps id -> display name (from search()); ids not in it fall back to the title-cased id.
    """
    names = names or {}
    return [_plan_one(i, resolution, http, names.get(i)) for i in ids]


def format_plan(plans: list[Asset]) -> str:
    """Markdown table for chat: name, source, licence, size in MB (or "already in the cache" when every file is),
    plus the total to download, then any file's `note` (a designed UI sound pack, say)."""
    rows = ["| Asset | Source | Licence | Size |", "|---|---|---|---|"]
    for a in plans:
        size = "already in the cache" if a.files and all(f.get("cached") for f in a.files) else (
            f"{a.bytes / 1_000_000:.1f} MB")  # fmt: skip
        rows.append(f"| {a.name} | {a.source} | {a.license} | {size} |")
    total = sum(download_bytes(a) for a in plans) / 1_000_000
    notes = [f"- {a.name}: {n}" for a in plans for n in dict.fromkeys(f["note"] for f in a.files if f.get("note"))]
    return "\n".join(rows) + f"\n\n**Total: {total:.1f} MB**" + ("\n\n" + "\n".join(notes) if notes else "")


def download_bytes(a: Asset) -> int:
    """Bytes still to download for an asset (files marked `cached` are already there)."""
    if not a.files:
        return a.bytes
    if all(f.get("cached") for f in a.files):
        return 0
    return a.bytes - sum(int(f.get("bytes") or 0) for f in a.files if f.get("cached"))


def _target(cache_dir: Path, rel: str) -> Path:
    """Where `rel` (remote data, untrusted) lands inside cache_dir; ValueError if it would escape."""
    parts = rel.split("/")
    if any(p in ("", ".", "..") or "\\" in p or ":" in p for p in parts):
        raise ValueError(f"unsafe asset path {rel!r}")
    root = Path(cache_dir).resolve()
    target = root.joinpath(*parts).resolve()
    try:
        target.relative_to(root)
    except ValueError:
        raise ValueError(f"unsafe asset path {rel!r}") from None
    return target


def _fetch(f: dict, dest: Path, http: Http) -> None:
    if not str(f["url"]).lower().startswith("https://"):
        raise ValueError(f"refusing to download {f['url']!r}: only https:// URLs are allowed")
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    md5, sha1 = hashlib.md5(), hashlib.sha1()
    size = 0
    exact = not f.get("max_bytes")  # max_bytes: the exact size was not known at plan time, only a cap
    limit = (f["bytes"] if exact else int(f["max_bytes"])) + 1  # one byte over is enough to know the body is too big
    resp = http(f["url"])
    try:
        with open(part, "wb") as out:
            while size < limit:
                chunk = resp.read(min(1 << 20, limit - size))
                if not chunk:
                    break
                out.write(chunk)
                md5.update(chunk)
                sha1.update(chunk)
                size += len(chunk)
    except BaseException:
        part.unlink(missing_ok=True)
        raise
    finally:
        resp.close()
    problem = None
    if exact and size != f["bytes"]:
        problem = f"size {size}{'+' if size > f['bytes'] else ''} bytes, expected {f['bytes']}"
    elif not exact and (size >= limit or size == 0):
        problem = f"size {size}{'+' if size >= limit else ''} bytes, expected at most {f['max_bytes']}"
    elif f.get("md5") and md5.hexdigest() != f["md5"]:
        problem = f"md5 {md5.hexdigest()}, expected {f['md5']}"
    elif f.get("sha1") and sha1.hexdigest() != f["sha1"]:
        problem = f"sha1 {sha1.hexdigest()}, expected {f['sha1']}"
    if problem:
        part.unlink(missing_ok=True)
        raise RuntimeError(f"Download of {f['url']} failed verification ({problem}); the partial file was removed.")
    os.replace(part, dest)


def cache_dir(short_dir: Path) -> Path:
    """The shared download cache: `.assets-cache` in the workspace that holds the short (its parent folder)."""
    return Path(short_dir).parent / ".assets-cache"


def asset_files(short_dir: Path, asset) -> dict[str, Path]:
    """{role: local path} of a downloaded asset (an Asset, or its dict from short.json `assets`): each file is at
    `<workspace>/.assets-cache/<file path>`. This is how build.py finds the files `assets download` fetched."""
    files = asset.files if isinstance(asset, Asset) else asset.get("files", [])
    return {f["role"]: _target(cache_dir(short_dir), f["path"]) for f in files}


def download(plans: list[Asset], cache_dir: Path, http: Http = _urlopen) -> dict[str, list[Path]]:
    """Fetch every file of `plans` into `cache_dir` (skipping cached files of the right size). Approval first."""
    result: dict[str, list[Path]] = {}
    for a in plans:
        paths = []
        for f in a.files:
            dest = _target(cache_dir, f["path"])
            cached = dest.is_file() and (dest.stat().st_size == f["bytes"] if not f.get("max_bytes") else
                                         0 < dest.stat().st_size <= int(f["max_bytes"]))  # fmt: skip
            if not cached:
                _fetch(f, dest, http)
            paths.append(dest)
        result[a.id] = paths
    return result


def record(short: Short, plans: list[Asset]) -> None:
    """Add the assets to the short's credits list (once each)."""
    have = {a.id for a in short.assets}
    for a in plans:
        if a.id not in have:
            short.assets.append(a)
            have.add(a.id)
