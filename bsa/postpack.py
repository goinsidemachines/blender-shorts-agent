"""out/post-pack.md: every post field and every output file of a short in one plain file to copy from.

Written by `CLI post SHORT` and whenever the page is shown (`shown`), only when its bytes change, so a poll never
rewrites it. It names no brand but the user's own: everything in it comes from the short's post block and files.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import threading
from pathlib import Path

from bsa import captions, engine_runs, post, translations

PACK = "out/post-pack.md"
GENERATED = "out/post-pack.generated.md"
INSTAGRAM = "out/instagram-post.md"
INSTAGRAM_GENERATED = "out/instagram-post.generated.md"
HASHES = "out/.post-hashes.json"  # {file: sha256 of what the plugin last wrote there}: a file that differs was edited
# The one set of section headings of a post pack, in order. Nothing else writes or reads a pack by other names.
HEADINGS = ("Title", "Description", "Hashtags", "Tags", "Pinned comment", "Link", "Related video", "Files")
INSTAGRAM_HEADINGS = ("Caption", "Tags box")
INSTAGRAM_LIMIT = dict(post.PLATFORMS)["Instagram"]
INSTAGRAM_AUDIO = "Audio: original audio (voice and sound effects only), no music. Don't add a track."
OUT_FILES = {"1080x1920": "out/thumbnail-1080x1920.png", "1280x720": "out/thumbnail-1280x720.png"}
NOT_YET = "(not written yet)"
STALE_NOTE = " (made for an older timing: run the final edit again)"
_CAPTION_KEYS = ("srt", "vtt", "transcript")
CHECK_IDS = ("upload", "length", "thumbnail", "captions", "languages", "pin", "link")
TIMING_TEXT = "made for an older timing: run the final edit again"
OLD_ENGLISH = translations.OLD_ENGLISH
LANGUAGE_REMOVED = "Not current (language removed)"


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


def _settings(settings: dict | None) -> dict:
    """The settings the closing voice line comes from: those given, else the user's (as `CLI captions` reads them)."""
    if settings is not None:
        return settings
    try:
        from bsa import settings as settings_mod

        return settings_mod.load_settings()
    except Exception:  # noqa: BLE001 - no settings: the closing line is then not checked
        return {}


def files(sdir: Path, short, video: dict | None = None, cap_state: dict | None = None,
          settings: dict | None = None) -> list[dict]:  # fmt: skip
    """The output files, one row each. A row's `path` is an absolute local path, also in the offline page's data.
    `video` (video_info), `cap_state` (captions.state) and `settings` are read here when not given."""
    sdir = Path(sdir)
    video = video or video_info(sdir, short)
    cap = captions.paths(sdir, short)
    stale = (cap_state or captions.state(sdir, short))["stale"]
    spec = [
        ("video", "Video", video["rel"]),
        ("thumbnail_vertical", "Thumbnail, Shorts and Reels", OUT_FILES["1080x1920"]),
        ("thumbnail_landscape", "Thumbnail, YouTube", OUT_FILES["1280x720"]),
        ("srt", "Captions (SRT)", f"out/{cap['srt'].name}"),
        ("vtt", "Captions (VTT)", f"out/{cap['vtt'].name}"),
        ("transcript", "Transcript", f"out/{cap['transcript'].name}"),
    ]
    skipped = translations.skipped(short, _settings(settings))
    for lang in _languages(short):
        lp = captions.paths(sdir, short, lang)
        spec += [(f"srt-{lang}", f"Captions, {lang} (SRT)", f"out/{lp['srt'].name}", lang),
                 (f"vtt-{lang}", f"Captions, {lang} (VTT)", f"out/{lp['vtt'].name}", lang)]  # fmt: skip
    for lang, old in captions.left_over(sdir, short).items():  # a removed language's files: shown, not current
        for ext in ("srt", "vtt"):
            if ext in old:
                spec.append((f"{ext}-{lang}", f"Captions, {lang} ({ext.upper()})", f"out/{Path(old[ext]).name}", lang))
                skipped[lang] = LANGUAGE_REMOVED
    spec.append(("pack", "Post pack", PACK))
    rows = []
    for key, label, rel, *lang in spec:
        path = sdir / rel
        exists = path.is_file()
        why = skipped.get(lang[0]) if lang else None  # not updated for the current English
        timing = bool(exists and (key in _CAPTION_KEYS or lang) and stale)
        row = {"key": key, "label": label, "rel": rel, "path": str(path), "exists": exists,
               "size": _size(path.stat().st_size) if exists else None,
               "stale": bool(timing or (exists and why))}  # fmt: skip
        if why and exists:  # both reasons: both said; only the timing: no `why` (the default text)
            row["why"] = f"{why}; also {TIMING_TEXT}" if timing else why
        rows.append(row)
    return rows


def _languages(short) -> list[str]:
    """The language codes of the short's translations that can name a file."""
    data = getattr(short, "captions_translations", None)
    return [k for k in data if isinstance(k, str) and translations.LANG.match(k)] if isinstance(data, dict) else []


def _from_an_earlier_make(sdir: Path) -> bool:
    """True when review/thumbs.json has no choice (the chosen candidate was made again since) while short.json
    still records one: the files in out/ are from the earlier make."""
    from bsa import thumbs
    from bsa.review.store import StoreDamaged

    try:
        return thumbs._chosen_n(thumbs.load(sdir).get("chosen")) is None
    except StoreDamaged:
        return False


def checklist_items(sdir: Path, short, video: dict | None = None, cap_state: dict | None = None,
                    settings: dict | None = None) -> list[dict]:  # fmt: skip
    """The before-you-post checklist, [{"id", "text"}] in CHECK_IDS order, built from what the plugin knows: the
    video's length and size, the chosen thumbnail, the caption file (only when cut for the current timing) and the
    pinned comment. Only the items that apply are listed. `video` and `cap_state` as for `files`."""
    from bsa import thumbs

    sdir = Path(sdir)
    video = video or video_info(sdir, short)
    p = post.fields(short)
    about = ", ".join(x for x in (video["length"], video["size"]) if x)
    sized = f" ({about})" if about else ""
    items = [
        ("upload", (f"Upload {video['rel']}{sized} from this computer. A copy sent through a phone app can be cut "
                    "short.")),
        ("length", f"After uploading, check the platform shows the full {video['length'] or 'video'}."),
    ]  # fmt: skip
    th = p["thumbnail"] if isinstance(p["thumbnail"], dict) else {}
    if thumbs._chosen_n(th.get("chosen")) is not None and (sdir / OUT_FILES["1080x1920"]).is_file():
        earlier = " (from an earlier make)" if _from_an_earlier_make(sdir) else ""
        items.append(("thumbnail", (f"Thumbnail: {OUT_FILES['1080x1920']} (Shorts and Reels: pick it as the cover "
                                    f"frame where the app allows), {OUT_FILES['1280x720']} (YouTube custom "
                                    f"thumbnail){earlier}.")))  # fmt: skip
    srt = captions.paths(sdir, short)["srt"]
    if srt.is_file() and not (cap_state or captions.state(sdir, short))["stale"]:
        items.append(("captions", (f"Captions: out/{srt.name}. Upload it in YouTube Studio → Subtitles, so the "
                                   "numbers come out right.")))  # fmt: skip
    skipped = translations.skipped(short, _settings(settings))
    ready = [
        f"out/{captions.paths(sdir, short, lang)['srt'].name}"
        for lang in _languages(short)
        if captions.paths(sdir, short, lang)["srt"].is_file() and lang not in skipped
    ]
    if ready and not (cap_state or captions.state(sdir, short))["stale"]:
        items.append(("languages", f"Upload each language in YouTube Studio → Subtitles: {', '.join(ready)}"))
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


def markdown(sdir: Path, short, settings: dict | None = None) -> str:
    p = post.fields(short)
    values = [_text(p["title"]), _text(p["description"]), _joined(p["hashtags"], " "), _joined(p["tags"], ", "),
              _text(p["pinned_comment"]), _text(p["link"]), _related(p["related"])]  # fmt: skip
    out = [f"# Post pack: {short.title}", ""]
    for name, value in zip(HEADINGS, values, strict=False):  # "Files" has no value: its lines follow
        out += [f"## {name}", value, ""]
    out += [f"## {HEADINGS[-1]}"]
    video = video_info(sdir, short)
    for row in files(sdir, short, video, settings=settings):
        if not row["exists"] or row["key"] == "pack":
            continue
        line = f"- {row['label']}: {row['rel']}"
        if row["key"] == "video" and video["length"] and video["size"]:
            line += f" ({video['length']}, {video['size']})"
        elif row.get("why"):
            line += f" ({row['why']})"
        elif row["stale"]:
            line += STALE_NOTE
        out.append(line)
    return "\n".join(out) + "\n"


def heading_problems(text: str, allowed: tuple[str, ...] = HEADINGS) -> list[str]:
    """What is wrong with a post pack's `## ` headings: one that is not in the fixed set (a dashboard reads them by
    name), or a fixed one that is missing."""
    found = [ln[3:].strip() for ln in text.splitlines() if ln.startswith("## ")]
    out = [f"heading {h!r} is not one of {', '.join(allowed)}" for h in found if h not in allowed]
    out += [f"heading {h!r} is missing" for h in allowed if h not in found]
    return out


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _hashes(sdir: Path) -> dict:
    try:
        data = json.loads((Path(sdir) / HASHES).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _put(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def _remember(sdir: Path, rel: str, data: bytes) -> None:
    path = Path(sdir) / HASHES
    known = _hashes(sdir)
    known[rel] = _sha(data)
    _put(path, json.dumps(known, indent=2, sort_keys=True).encode("utf-8"))


def write_guarded(sdir: Path, rel: str, generated_rel: str, data: bytes) -> dict:
    """Write `data` to `rel` unless the file there is not what the plugin last wrote (a hash is kept in HASHES): then
    the user edited it, so `data` goes to `generated_rel` and the file is left alone. A file with no record counts as
    edited, unless it already holds exactly `data`. Returns {"path", "changed", "edited", "generated"}: `path` is
    where `data` is now, `edited` is True when the user's own file was kept."""
    sdir = Path(sdir)
    path = sdir / rel
    try:
        on_disk = path.read_bytes()
    except OSError:
        on_disk = None
    if on_disk is not None and on_disk != data and _hashes(sdir).get(rel) != _sha(on_disk):
        target = sdir / generated_rel
        try:
            same = target.read_bytes() == data
        except OSError:
            same = False
        if not same:
            _put(target, data)
        return {"path": target, "changed": not same, "edited": True, "generated": str(target)}
    if on_disk == data:
        if _hashes(sdir).get(rel) != _sha(data):
            _remember(sdir, rel, data)
        return {"path": path, "changed": False, "edited": False, "generated": None}
    _put(path, data)
    _remember(sdir, rel, data)
    (sdir / generated_rel).unlink(missing_ok=True)  # the user's edit is gone: the side copy would only confuse
    return {"path": path, "changed": True, "edited": False, "generated": None}


def write_info(sdir: Path, short, settings: dict | None = None) -> dict:
    data = markdown(sdir, short, settings).encode("utf-8")
    wrong = heading_problems(data.decode("utf-8"))
    if wrong:  # the template and HEADINGS drifted apart: a bug here, never written
        raise ValueError("the post pack template breaks its own headings: " + "; ".join(wrong))
    return write_guarded(sdir, PACK, GENERATED, data)


def write(sdir: Path, short, settings: dict | None = None) -> tuple[Path, bool]:
    """Write out/post-pack.md, unless the user edited it (then out/post-pack.generated.md). (the path written, whether
    it changed)."""
    info = write_info(sdir, short, settings)
    return info["path"], info["changed"]


def wants_instagram(settings: dict | None) -> bool:
    return "instagram" in post.platforms(_settings(settings))


def _caption_body(description: str) -> str:
    """The description as an Instagram caption body: no source list, no 'made in' note, no link line (the caption
    says 'Link in bio'), no trailing hashtag line."""
    paras = [x.strip() for x in re.split(r"\n\s*\n", description.strip()) if x.strip()]
    keep = []
    for para in paras:
        first = para.splitlines()[0]
        if first.startswith("Sources"):
            break
        if first.startswith(("\u25b6", "Made in")) or all(w.startswith("#") for w in para.split()):
            continue
        keep.append(para)
    return "\n\n".join(keep)


def instagram_markdown(short, settings: dict | None = None) -> tuple[str, list[str]]:
    """(out/instagram-post.md, warnings): the caption (title, body, 'Link in bio', hashtags) within Instagram's
    2,200 characters, and the tags box. A body that would not fit is cut at a sentence and a warning says so."""
    p = post.fields(short)
    fmt = _settings(settings).get("post_format")
    suffix = (fmt.get("title_suffix") if isinstance(fmt, dict) else "") or ""
    title = _text(p["title"])
    title = title[: -len(suffix)].rstrip() if suffix and title.endswith(suffix) else title
    tags = (
        [h for h in p["hashtags"] if isinstance(h, str) and h.lower() != "#shorts"]
        if isinstance(p["hashtags"], list)
        else []
    )
    tail = ""
    if isinstance(p["link"], str) and p["link"].strip():
        tail = "Link in bio."
    hashes = " ".join(tags)
    body = _caption_body(p["description"] if isinstance(p["description"], str) else "")
    warnings = []

    def build(b: str) -> str:
        return "\n\n".join(x for x in (title, b, tail, hashes) if x)

    caption = build(body)
    if len(caption) > INSTAGRAM_LIMIT:
        room = INSTAGRAM_LIMIT - len(build("")) - 2
        cut = body[: max(room, 0)]
        stop = max(cut.rfind(". "), cut.rfind("? "), cut.rfind("! "), cut.rfind(".\n"))
        body = cut[: stop + 1] if stop > 0 else cut.rstrip()
        caption = build(body)
        warnings.append(f"the Instagram caption was cut to fit {INSTAGRAM_LIMIT:,} characters: shorten the "
                        "description, or write out/instagram-post.md by hand")  # fmt: skip
    out = [f"# Instagram post: {short.title}", "", INSTAGRAM_AUDIO, "", f"## {INSTAGRAM_HEADINGS[0]}", caption, "",
           f"## {INSTAGRAM_HEADINGS[1]}", _joined(p["tags"], ", "), ""]  # fmt: skip
    return "\n".join(out), warnings


def write_instagram(sdir: Path, short, settings: dict | None = None) -> dict | None:
    """Write out/instagram-post.md when Instagram is among the user's platforms (post_format.platforms); None when
    it is not. Same hand-edit protection as the pack; adds "warnings"."""
    if not wants_instagram(settings):
        return None
    text, warnings = instagram_markdown(short, settings)
    wrong = heading_problems(text, INSTAGRAM_HEADINGS)
    if wrong:
        raise ValueError("the Instagram template breaks its own headings: " + "; ".join(wrong))
    info = write_guarded(sdir, INSTAGRAM, INSTAGRAM_GENERATED, text.encode("utf-8"))
    return {**info, "warnings": warnings}


def write_all(sdir: Path, short, settings: dict | None = None) -> dict:
    """Both post files. {"pack": info, "instagram": info or None, "warnings": [...]}: a warning for each file the user
    edited (the new text went to its .generated.md), for headings of the user's own pack that are off, and for a
    cut Instagram caption."""
    pack = write_info(sdir, short, settings)
    ig = write_instagram(sdir, short, settings)
    warnings = []
    for info, rel in ((pack, PACK), (ig, INSTAGRAM)):
        if info and info["edited"]:
            warnings.append(f"{rel} was edited by hand, so it was left as it is: the plugin's text is in "
                            f"{Path(info['generated']).relative_to(Path(sdir)).as_posix()}")  # fmt: skip
    if pack["edited"]:
        try:
            mine = (Path(sdir) / PACK).read_text(encoding="utf-8")
        except OSError:
            mine = ""
        warnings += [f"{PACK}: {w}" for w in heading_problems(mine)]
    if ig:
        warnings += ig["warnings"]
    return {"pack": pack, "instagram": ig, "warnings": warnings}


def shown(sdir: Path) -> None:
    """Keep the pack current while the page is shown. Never raises: a short that does not load writes nothing."""
    try:
        from bsa.short import load_short

        write_all(sdir, load_short(Path(sdir) / "source" / "short.json"))
    except Exception:  # noqa: BLE001, S110 - a side file must never break or slow the page
        pass
