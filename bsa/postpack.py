"""out/post-pack.md: every post field and every output file of a short in one plain file to copy from.

Written by `CLI post SHORT` and whenever the page is shown (`shown`), only when its bytes change, so a poll never
rewrites it. It names no brand but the user's own: everything in it comes from the short's post block and files.
"""

from __future__ import annotations

import math
import os
import threading
from pathlib import Path

from bsa import captions, engine_runs, post

PACK = "out/post-pack.md"
OUT_FILES = {"1080x1920": "out/thumbnail-1080x1920.png", "1280x720": "out/thumbnail-1280x720.png"}
NOT_YET = "(not written yet)"
STALE_NOTE = " (made for an older timing: run the final edit again)"
_CAPTION_KEYS = ("srt", "vtt", "transcript")
CHECK_IDS = ("upload", "length", "thumbnail", "captions", "pin", "link")


def clock(seconds: float) -> str:
    total = math.floor(seconds + 0.5)
    return f"{total // 60}:{total % 60:02d}"


def megabytes(n: int) -> str:
    tenths = math.floor(n / 100_000 + 0.5)  # round first, then choose the format
    return f"{math.floor(tenths / 10 + 0.5)} MB" if tenths >= 100 else f"{tenths / 10:.1f} MB"


def _size(n: int) -> str:
    return f"{math.floor(n / 1000 + 0.5)} KB" if n < 1_000_000 else megabytes(n)


def video_info(sdir: Path, short) -> dict:
    rel = f"out/{short.slug}.mp4"
    path = Path(sdir) / rel
    exists = path.is_file()
    nbytes = path.stat().st_size if exists else None
    seconds = None
    rec = engine_runs.edit_info(Path(sdir), "final")
    if rec:
        try:
            seconds = float(rec["frames"]) / float(rec["fps"])
        except (KeyError, TypeError, ValueError, ZeroDivisionError):
            seconds = None
    return {"rel": rel, "path": str(path), "exists": exists, "bytes": nbytes, "seconds": seconds,
            "length": clock(seconds) if seconds is not None else None,
            "size": megabytes(nbytes) if nbytes is not None else None}  # fmt: skip


def files(sdir: Path, short, video: dict | None = None) -> list[dict]:
    sdir = Path(sdir)
    video = video or video_info(sdir, short)
    cap = captions.paths(sdir, short)
    stale = captions.state(sdir, short)["stale"]
    spec = [
        ("video", "Video", video["rel"]),
        ("thumbnail_vertical", "Thumbnail, Shorts and Reels", OUT_FILES["1080x1920"]),
        ("thumbnail_landscape", "Thumbnail, YouTube", OUT_FILES["1280x720"]),
        ("srt", "Captions (SRT)", f"out/{cap['srt'].name}"),
        ("vtt", "Captions (VTT)", f"out/{cap['vtt'].name}"),
        ("transcript", "Transcript", f"out/{cap['transcript'].name}"),
        ("pack", "Post pack", PACK),
    ]
    rows = []
    for key, label, rel in spec:
        path = sdir / rel
        exists = path.is_file()
        rows.append({"key": key, "label": label, "rel": rel, "path": str(path), "exists": exists,
                     "size": _size(path.stat().st_size) if exists else None,
                     "stale": bool(key in _CAPTION_KEYS and exists and stale)})  # fmt: skip
    return rows


def checklist_items(sdir: Path, short) -> list[dict]:
    """The before-you-post checklist, [{"id", "text"}] in CHECK_IDS order, built from what the plugin knows: the
    video's length and size, the chosen thumbnail, the caption file and the pinned comment. Only the items that
    apply are listed."""
    from bsa import thumbs

    sdir = Path(sdir)
    video = video_info(sdir, short)
    p = post.fields(short)
    about = ", ".join(x for x in (video["length"], video["size"]) if x)
    sized = f" ({about})" if about else ""
    items = [
        ("upload", (f"Upload {video['rel']}{sized} from this computer. A copy sent through a phone app can be cut "
                    "short.")),
        ("length", f"After uploading, check the platform shows the full {video['length'] or 'video'}."),
    ]  # fmt: skip
    th = p["thumbnail"] if isinstance(p["thumbnail"], dict) else {}
    if thumbs._chosen_n(th.get("chosen")) is not None:
        items.append(("thumbnail", (f"Thumbnail: {OUT_FILES['1080x1920']} (Shorts and Reels: pick it as the cover "
                                    f"frame where the app allows), {OUT_FILES['1280x720']} (YouTube custom "
                                    "thumbnail).")))  # fmt: skip
    srt = captions.paths(sdir, short)["srt"]
    if srt.is_file():
        items.append(("captions", (f"Captions: out/{srt.name}. Upload it in YouTube Studio → Subtitles, so the "
                                   "numbers come out right.")))  # fmt: skip
    if isinstance(p["pinned_comment"], str) and p["pinned_comment"].strip():
        items.append(("pin", "Pin the first comment"))
    items.append(("link", "After posting, tell Claude the link"))
    return [{"id": i, "text": t} for i, t in items]


def _text(value) -> str:
    return value.strip() if isinstance(value, str) and value.strip() else NOT_YET


def _related(value) -> str:
    if not isinstance(value, dict):
        return NOT_YET
    parts = [str(value[k]).strip() for k in ("title", "url") if value.get(k)]
    return " ".join(p for p in parts if p) or NOT_YET


def _joined(value, sep: str) -> str:
    items = [x for x in value if isinstance(x, str) and x] if isinstance(value, list) else []
    return sep.join(items) or NOT_YET


def markdown(sdir: Path, short) -> str:
    p = post.fields(short)
    sections = [
        ("Title", _text(p["title"])),
        ("Description", _text(p["description"])),
        ("Hashtags", _joined(p["hashtags"], " ")),
        ("Tags", _joined(p["tags"], ", ")),
        ("Pinned comment", _text(p["pinned_comment"])),
        ("Link", _text(p["link"])),
        ("Related video", _related(p["related"])),
    ]
    out = [f"# Post pack: {short.title}", ""]
    for name, value in sections:
        out += [f"## {name}", value, ""]
    out += ["## Files"]
    video = video_info(sdir, short)
    for row in files(sdir, short, video):
        if not row["exists"] or row["key"] == "pack":
            continue
        line = f"- {row['label']}: {row['rel']}"
        if row["key"] == "video" and video["length"] and video["size"]:
            line += f" ({video['length']}, {video['size']})"
        elif row["stale"]:
            line += STALE_NOTE
        out.append(line)
    return "\n".join(out) + "\n"


def write(sdir: Path, short) -> tuple[Path, bool]:
    path = Path(sdir) / PACK
    data = markdown(sdir, short).encode("utf-8")
    try:
        if path.read_bytes() == data:
            return path, False
    except OSError:
        pass
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)
    return path, True


def shown(sdir: Path) -> None:
    """Keep the pack current while the page is shown. Never raises: a short that does not load writes nothing."""
    try:
        from bsa.short import load_short

        write(sdir, load_short(Path(sdir) / "source" / "short.json"))
    except Exception:  # noqa: BLE001, S110 - a side file must never break or slow the page
        pass
