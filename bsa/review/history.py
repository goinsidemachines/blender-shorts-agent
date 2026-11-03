"""Version history for before and after: what each version of Story, Storyboard, Final and Post showed, kept only for
the versions the page actually served to a person (the server's /state and the static page), the last KEEP per stage.

A stage's history lives in review/history/<stage>/: index.json lists the kept versions in the order first shown,
and v<N>/ holds what version N showed (Story: lines.json; Storyboard: shots.json and a copy of each still, plus
look.json and look/ with the look preview's frames when it had one; Final:
final.json, with the MP4's digest in the index entry; Post: post.json and a copy of the chosen thumbnail). A copy
is made in v<N>.<pid>.tmp and renamed into place under the index's lock, so a half-copied folder is never listed.
The approved final's outgoing MP4 is moved (not copied) into review/history/final/<sha256[:16]>.mp4 by the edit, and
the last MP4_KEEP of those are kept."""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import shutil
import time
from pathlib import Path

from bsa import jobstate, messages
from bsa.review import store, versions, worddiff

STAGES = ("story", "storyboard", "final", "post")
THUMBNAIL = "thumbnail.png"  # Post: the chosen vertical thumbnail as that version showed it
KEEP = 3
MP4_KEEP = 3
INDEX = "index.json"
LOCK_S = 15.0  # a copy of a storyboard's stills takes well under this; a poll that can't wait says so as a problem
_VDIR = re.compile(r"v[0-9]+")


def root(sdir: Path, stage: str) -> Path:
    return Path(sdir) / "review" / "history" / stage


def _rel(sdir: Path, path: Path) -> str:
    return path.relative_to(Path(sdir)).as_posix()


def _index(r: Path) -> list[dict]:
    """The kept entries (StoreDamaged when index.json is damaged); an entry without a version or fingerprint is
    left out."""
    data = store.read_json(r / INDEX, [], list)
    return [e for e in data if isinstance(e.get("version"), int) and isinstance(e.get("fingerprint"), str)]


def _fingerprint(sdir: Path, stage: str, content: dict) -> str:
    """The stage's fingerprint as versions.json holds it: `acked` is added to the content after the version."""
    from bsa.review import snapshot  # lazily: the edit (inside Blender) imports this module for keep_outgoing_final

    return snapshot.stage_fingerprint(sdir, stage, {k: v for k, v in content.items() if k != "acked"})


def _is_version(sdir: Path, stage: str, version: int, fp: str) -> bool:
    """versions.json still gives `fp` this version (the short may have changed since the snapshot was built)."""
    seen = store.read_json(versions.versions_path(sdir), {}, dict).get(stage)
    return isinstance(seen, list) and 0 < version <= len(seen) and seen[version - 1] == fp


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _plain_name(name) -> bool:
    return isinstance(name, str) and name not in ("", ".", "..") and Path(name).name == name and "\\" not in name


class _Moved(Exception):
    """A file the version points at changed while it was being copied: try again on the next poll."""


def _write(path: Path, data) -> None:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def _copy_checked(sdir: Path, src: str, dest: Path) -> None:
    """A copy of a file of the short, never a hard link (Blender and `thumbs.choose` rewrite files in place, which
    would change a hard link's "before" too); _Moved when it changed while being copied."""
    shutil.copy2(Path(sdir) / src, dest)
    if _sha256(dest) != versions.file_digest(sdir, src):
        raise _Moved(src)


def _copy_look(sdir: Path, look, tmp: Path) -> None:
    """The storyboard's look preview (approved with it): each frame into look/ and look.json with the style, the set
    and which frame is which shot's. Nothing without a look."""
    if not isinstance(look, dict):
        return
    frames = []
    for f in look.get("frames") or []:
        ref = f.get("file") if isinstance(f, dict) else None
        src = ref.get("file") if isinstance(ref, dict) else None
        name = None
        if isinstance(src, str) and src.startswith("stills/look/") and _plain_name(src[len("stills/look/"):]):
            name = src[len("stills/look/"):]
            (tmp / "look").mkdir(exist_ok=True)
            _copy_checked(sdir, src, tmp / "look" / name)
        if isinstance(f, dict):
            frames.append({"shot": f.get("shot"), "still": name})
    _write(tmp / "look.json", {"style": look.get("style"), "set": look.get("set"), "frames": frames})


def _copy(sdir: Path, stage: str, content: dict, tmp: Path) -> dict:
    """What the version showed, into tmp; the extra index fields."""
    tmp.mkdir(parents=True)
    if stage == "post":
        _write(tmp / "post.json", [{"key": f.get("key"), "text": f.get("text")} for f in content.get("fields") or []])
        th = content.get("thumbnail")
        ref = th.get("file") if isinstance(th, dict) else None
        if isinstance(ref, dict) and isinstance(ref.get("file"), str):
            _copy_checked(sdir, ref["file"], tmp / THUMBNAIL)
        return {}
    if stage == "story":
        lines = [{"id": ln.get("id"), "text": ln.get("text")}
                 for beat in content.get("beats") or [] for ln in beat.get("lines") or []]  # fmt: skip
        _write(tmp / "lines.json", lines)
        return {}
    if stage == "storyboard":
        shots = []
        for shot in content.get("shots") or []:
            ref = shot.get("still")
            src = ref.get("file") if isinstance(ref, dict) else None
            name = None
            if isinstance(src, str) and src.startswith("stills/") and _plain_name(src[len("stills/"):]):
                name = src[len("stills/"):]
                (tmp / "stills").mkdir(exist_ok=True)
                _copy_checked(sdir, src, tmp / "stills" / name)
            shots.append({"id": shot.get("id"), "title": shot.get("title"), "still": name})
        _write(tmp / "shots.json", shots)
        _copy_look(sdir, content.get("look"), tmp)
        return {}
    _write(tmp / "final.json", {"size": content.get("size_mb"), "findings": content.get("findings") or []})
    video = content.get("video")
    if isinstance(video, dict) and isinstance(video.get("file"), str):
        return {"mp4_digest": versions.file_digest(sdir, video["file"])}
    return {}


def _remove(r: Path, path: Path) -> None:
    """Delete a version folder or a leftover copy, only ever inside the stage's history root."""
    if path.parent == r and path.is_dir() and not path.is_symlink():
        shutil.rmtree(path, ignore_errors=True)  # a file being served right now: swept on a later record


def _record(sdir: Path, stage: str, version: int, fp: str, content: dict, now: float) -> None:
    r = root(sdir, stage)
    if any(e["fingerprint"] == fp for e in _index(r)):  # the usual poll: already kept, no lock taken
        return
    if not _is_version(sdir, stage, version, fp):
        return
    r.mkdir(parents=True, exist_ok=True)
    with store.locked(r / INDEX, timeout_s=LOCK_S):
        entries = _index(r)
        if any(e["fingerprint"] == fp for e in entries):  # another poll kept it while this one waited
            return
        for left in r.glob("*.tmp"):  # under the lock, any copy in progress is a crash's leftover
            _remove(r, left)
        tmp = r / f"v{version}.{os.getpid()}.tmp"
        try:
            extra = _copy(sdir, stage, content, tmp)
            if _fingerprint(sdir, stage, content) != fp:  # a file changed after the snapshot: not this version
                raise _Moved(stage)
            files = sorted(_rel(sdir, (r / f"v{version}") / p.relative_to(tmp)) for p in tmp.rglob("*") if p.is_file())
            dest = r / f"v{version}"
            if dest.exists():  # this version number under another fingerprint (versions.json was moved aside)
                os.replace(dest, r / f"v{version}.{os.getpid()}.old.tmp")
            os.replace(tmp, dest)
        except _Moved:
            return
        finally:
            _remove(r, tmp)
        entry = {"version": version, "fingerprint": fp, "at": now, "files": files, **extra}
        kept = ([e for e in entries if e["version"] != version] + [entry])[-KEEP:]
        jobstate.write_atomic(r / INDEX, json.dumps(kept, indent=2, ensure_ascii=False).encode("utf-8"))
        names = {f"v{e['version']}" for e in kept}
        for p in r.iterdir():  # the pruned versions, a replaced folder and anything a crash left
            if (_VDIR.fullmatch(p.name) and p.name not in names) or p.name.endswith(".tmp"):
                _remove(r, p)


def _merged(old: list[str], new: list[str]) -> list[str]:
    """The ids of both sides in the new order, an id only in the old one placed after its old predecessor."""
    order = list(new)
    for k, oid in enumerate(old):
        if oid in order:
            continue
        pos = 0
        for prev in reversed(old[:k]):
            if prev in order:
                pos = order.index(prev) + 1
                break
        order.insert(pos, oid)
    return order


def _read(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _story_compare(sdir: Path, folder: Path, entry: dict, content: dict) -> dict:
    old = {d["id"]: str(d.get("text") or "") for d in _read(folder / "lines.json")}
    new = {ln["id"]: str(ln.get("text") or "")
           for beat in content.get("beats") or [] for ln in beat.get("lines") or []}  # fmt: skip
    return {"lines": [{"id": i, "diff": worddiff.diff(old.get(i, ""), new.get(i, ""))}
                      for i in _merged(list(old), list(new))]}  # fmt: skip


def _storyboard_compare(sdir: Path, folder: Path, entry: dict, content: dict) -> dict:
    old = {s["id"]: s for s in _read(folder / "shots.json")}
    new = {s["id"]: s for s in content.get("shots") or []}
    shots = []
    for i in _merged(list(old), list(new)):
        o, n = old.get(i), new.get(i)
        before = None
        if o and _plain_name(o.get("still")) and (folder / "stills" / o["still"]).is_file():
            before = {"file": _rel(sdir, folder / "stills" / o["still"])}
        ref = n.get("still") if n else None
        after = {"file": ref["file"]} if isinstance(ref, dict) and isinstance(ref.get("file"), str) else None
        shots.append({"id": i, "title": (n or o).get("title"), "before": before, "after": after})
    out = {"shots": shots}
    look = _look_compare(sdir, folder, content.get("look"))
    if look:
        out["look"] = look
    return out


def _look_side(look: dict | None) -> dict | None:
    return {"style": look.get("style"), "set": look.get("set")} if look is not None else None


def _look_compare(sdir: Path, folder: Path, now) -> dict | None:
    """The look preview then and now: each side's style and set (None for a side without a look) and its frames
    paired by shot. None when neither side has a look."""
    then = _read(folder / "look.json") if (folder / "look.json").is_file() else None
    then = then if isinstance(then, dict) else None
    now = now if isinstance(now, dict) else None
    if then is None and now is None:
        return None
    old = {}
    for f in (then or {}).get("frames") or []:
        if isinstance(f, dict) and isinstance(f.get("shot"), str):
            name = f.get("still")
            ok = _plain_name(name) and (folder / "look" / name).is_file()
            old[f["shot"]] = {"file": _rel(sdir, folder / "look" / name)} if ok else None
    new = {}
    for f in (now or {}).get("frames") or []:
        if isinstance(f, dict) and isinstance(f.get("shot"), str):
            ref = f.get("file")
            new[f["shot"]] = {"file": ref["file"]} if isinstance(ref, dict) and isinstance(ref.get("file"), str) else None
    return {"before": _look_side(then), "after": _look_side(now),
            "frames": [{"shot": i, "before": old.get(i), "after": new.get(i)} for i in _merged(list(old), list(new))]}


def _final_compare(sdir: Path, folder: Path, entry: dict, content: dict) -> dict:
    final = _read(folder / "final.json")
    digest = entry.get("mp4_digest")
    video = None
    if isinstance(digest, str) and re.fullmatch(r"[0-9a-f]{64}", digest):
        mp4 = folder.parent / f"{digest[:16]}.mp4"
        if mp4.is_file():
            video = {"file": _rel(sdir, mp4)}
    return {"video": video, "findings": final.get("findings") or []}


def _post_compare(sdir: Path, folder: Path, entry: dict, content: dict) -> dict:
    old = {d.get("key"): str(d.get("text") or "") for d in _read(folder / "post.json") if isinstance(d, dict)}
    new = {f.get("key"): str(f.get("text") or "") for f in content.get("fields") or []}
    keys = _merged([k for k in old if isinstance(k, str)], [k for k in new if isinstance(k, str)])
    th = content.get("thumbnail")
    ref = th.get("file") if isinstance(th, dict) else None
    after = {"file": ref["file"]} if isinstance(ref, dict) and isinstance(ref.get("file"), str) else None
    before = {"file": _rel(sdir, folder / THUMBNAIL)} if (folder / THUMBNAIL).is_file() else None
    return {"fields": [{"key": k, "diff": worddiff.diff(old.get(k, ""), new.get(k, ""))} for k in keys],
            "thumbnail": {"before": before, "after": after}}  # fmt: skip


_COMPARE = {"story": _story_compare, "storyboard": _storyboard_compare, "final": _final_compare,
            "post": _post_compare}  # fmt: skip


def listing(sdir: Path, stage: str, current_version: int, content: dict) -> list[dict]:
    """The kept versions newest first, never the current one, each with what to compare it with. A version whose
    folder is gone or unreadable is not offered."""
    sdir = Path(sdir)
    r = root(sdir, stage)
    out = []
    for e in reversed(_index(r)):
        if e["version"] == current_version:
            continue
        folder = r / f"v{e['version']}"
        if not folder.is_dir():
            continue
        try:
            compare = _COMPARE[stage](sdir, folder, e, content)
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            continue
        files = [f for f in e.get("files") or [] if isinstance(f, str)]
        out.append({"version": e["version"], "at": e.get("at"), "files": files, "compare": compare})
    return out


def shown(sdir: Path, snap: dict, now: float | None = None) -> dict:
    """The snapshot is being served to a person: keep what each shown (not locked) Story, Storyboard, Final and Post
    version shows, prune to KEEP, and list the earlier ones on the stage row (`history`). Never raises: a failure
    is one line in `problems` and that stage's history is []."""
    sdir = Path(sdir)
    now = time.time() if now is None else now
    problems = snap.setdefault("problems", [])
    for row in snap.get("stages") or []:
        stage = row.get("id")
        if stage not in STAGES:
            continue
        row["history"] = []
        version, content = row.get("version"), row.get("content")
        if row.get("status") == "locked" or not isinstance(version, int) or not isinstance(content, dict):
            continue  # a locked stage is never shown; a final being written has no version
        try:
            _record(sdir, stage, version, _fingerprint(sdir, stage, content), content, now)
            row["history"] = listing(sdir, stage, version, content)
        except Exception as e:  # noqa: BLE001 - history must never break the page
            text = str(e) if isinstance(e, store.StoreDamaged) else (
                f"the {stage} history can't be kept: {type(e).__name__}: {e}")  # fmt: skip
            line = messages.one_line(text.replace(str(sdir) + os.sep, ""))
            if line not in problems:
                problems.append(line)
    return snap


def keep_outgoing_final(sdir: Path, mp4: Path) -> Path | None:
    """Move the approved final's MP4 into review/history/final/<sha256[:16]>.mp4 (the name its shown version's
    `mp4_digest` points at) and delete all but the MP4_KEEP newest kept MP4s, never the one just kept. None when
    there is no file."""
    mp4 = Path(mp4)
    if not mp4.is_file():
        return None
    r = root(sdir, "final")
    r.mkdir(parents=True, exist_ok=True)
    dest = r / f"{_sha256(mp4)[:16]}.mp4"
    with store.locked(r / INDEX, timeout_s=LOCK_S):
        os.replace(mp4, dest)  # PermissionError on Windows while the page streams it: the edit waits and retries
        os.utime(dest)  # the newest: the one just kept is never pruned
        others = [p for p in r.glob("*.mp4") if p != dest and p.is_file()]
        others.sort(key=lambda p: p.stat().st_mtime_ns, reverse=True)
        for old in others[MP4_KEEP - 1 :]:
            with contextlib.suppress(OSError):
                old.unlink()
    return dest
