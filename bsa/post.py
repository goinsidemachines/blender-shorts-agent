"""The post block of short.json: its fields, platform limits, the user's own post format, and the checks on them."""

from __future__ import annotations

import re
from urllib.parse import urlparse

FIELDS = ("title", "description", "hashtags", "tags", "pinned_comment", "link", "related")
LABELS = {
    "title": "Title",
    "description": "Description",
    "hashtags": "Hashtags",
    "tags": "Tags",
    "pinned_comment": "Pinned comment",
    "link": "Link",
    "related": "Related video",
}
TITLE_MAX = 100
TITLE_FEED = 60
DESCRIPTION_MAX = 5000
PLATFORMS = (("YouTube", 5000), ("Instagram", 2200), ("TikTok", 2200))
HASHTAGS = (3, 6)
TAGS_MAX = 500
PINNED_MAX = 10000
THUMB_WORDS = 6
FORMAT_KEYS = ("title_suffix", "title_style", "link_line", "pinned_comment", "always_hashtags")
TITLE_STYLES = ("question", "statement")

_HASHTAG = re.compile(r"^#[^\s#]+$")
_TEXT_FORMAT_KEYS = ("title_suffix", "link_line", "pinned_comment")
FRAME_W, FRAME_H = 1080, 1920


def _post(short) -> dict:
    p = getattr(short, "post", None)
    return p if isinstance(p, dict) else {}


def fields(short) -> dict:
    """The post block with every field present ("" / [] / None for what is not set)."""
    out = {
        "title": "",
        "description": "",
        "hashtags": [],
        "tags": [],
        "pinned_comment": "",
        "link": "",
        "related": None,
        "thumbnail": None,
        "posted": [],
    }
    for k, v in _post(short).items():
        if v is not None or k in ("related", "thumbnail"):
            out[k] = v
    return out


def tags_length(tags: list[str]) -> int:
    """YouTube's count: each tag's length, +2 for a tag with a space (its quotes), +1 per comma between tags."""
    tags = [str(t) for t in tags]
    return sum(len(t) + (2 if " " in t else 0) for t in tags) + max(len(tags) - 1, 0)


def fits(description: str) -> list[str]:
    """The names of the platforms whose description limit the text fits."""
    return [name for name, limit in PLATFORMS if len(description) <= limit]


def _state(count: int, mx: int, warn_over: int | None) -> str:
    if count > mx:
        return "over"
    return "warn" if warn_over is not None and count > warn_over else "ok"


def counts(short) -> dict[str, dict]:
    p = fields(short)
    title = p["title"] if isinstance(p["title"], str) else ""
    desc = p["description"] if isinstance(p["description"], str) else ""
    pinned = p["pinned_comment"] if isinstance(p["pinned_comment"], str) else ""
    hashtags = p["hashtags"] if isinstance(p["hashtags"], list) else []
    tags = [t for t in p["tags"] if isinstance(t, str)] if isinstance(p["tags"], list) else []
    n_hash = len(hashtags)
    hash_state = "over" if n_hash > HASHTAGS[1] else "warn" if n_hash and n_hash < HASHTAGS[0] else "ok"
    tl = tags_length(tags)
    return {
        "title": {"count": len(title), "max": TITLE_MAX, "warn_over": TITLE_FEED,
                  "state": _state(len(title), TITLE_MAX, TITLE_FEED)},
        "description": {"count": len(desc), "max": DESCRIPTION_MAX, "warn_over": PLATFORMS[1][1],
                        "state": _state(len(desc), DESCRIPTION_MAX, PLATFORMS[1][1])},
        "hashtags": {"count": n_hash, "max": HASHTAGS[1], "warn_over": None, "state": hash_state},
        "tags": {"count": tl, "max": TAGS_MAX, "warn_over": None, "state": _state(tl, TAGS_MAX, None)},
        "pinned_comment": {"count": len(pinned), "max": PINNED_MAX, "warn_over": None,
                           "state": _state(len(pinned), PINNED_MAX, None)},
    }  # fmt: skip


def format_value(value) -> dict:
    """A post_format as stored; ValueError for an unknown key or a value of the wrong kind."""
    if not isinstance(value, dict):
        raise ValueError(f"post_format must be an object with some of: {', '.join(FORMAT_KEYS)}")  # noqa: TRY004
    out = {}
    for key, v in value.items():
        if key not in FORMAT_KEYS:
            raise ValueError(f"{key!r} is not a post_format key: use {', '.join(FORMAT_KEYS)}")
        if key in _TEXT_FORMAT_KEYS:
            if not isinstance(v, str):
                raise ValueError(f"post_format.{key} must be text, got {v!r}")
        elif key == "title_style":
            if v not in TITLE_STYLES:
                raise ValueError(f"post_format.title_style must be one of {', '.join(TITLE_STYLES)}, got {v!r}")
        elif not isinstance(v, list) or not all(isinstance(h, str) and _HASHTAG.match(h) for h in v):
            raise ValueError(f"post_format.always_hashtags must be a list of #words, got {v!r}")
        out[key] = v
    return out


def _title_lines(t) -> list[str] | None:
    lines = t.split("\n") if isinstance(t, str) else t
    if not isinstance(lines, list) or not all(isinstance(x, str) for x in lines):
        return None
    return lines


def thumbnail_texts(p: dict) -> list[tuple[str, str]]:
    th = p.get("thumbnail") if isinstance(p, dict) else None
    if not isinstance(th, dict):
        return []
    out = []
    lines = _title_lines(th.get("title"))
    if lines:
        out.append(("post thumbnail title", " ".join(x.strip() for x in lines if x.strip())))
    for key in ("kicker", "fact"):
        if isinstance(th.get(key), str) and th[key].strip():
            out.append((f"post thumbnail {key}", th[key]))
    return out


def rule_places(short) -> list[tuple[str, str]]:
    p = fields(short)
    out = []
    for where, key in (("post title", "title"), ("post description", "description"),
                       ("post pinned comment", "pinned_comment")):  # fmt: skip
        if isinstance(p[key], str) and p[key]:
            out.append((where, p[key]))
    if isinstance(p["hashtags"], list):
        out.append(("post hashtags", " ".join(h for h in p["hashtags"] if isinstance(h, str))))
    if isinstance(p["tags"], list):
        out.append(("post tags", " ".join(t for t in p["tags"] if isinstance(t, str))))
    out = [o for o in out if o[1]]
    return out + thumbnail_texts(p)


def _host(url) -> str:
    try:
        return (urlparse(str(url)).hostname or "").lower()
    except ValueError:
        return ""


def _site_host(website) -> str:
    text = str(website or "").strip().lower()
    if not text:
        return ""
    return _host(text if "//" in text else "https://" + text).removeprefix("www.")


def _is_int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _is_num(v) -> bool:
    return isinstance(v, int | float) and not isinstance(v, bool)


def _crop_ok(crop) -> bool:
    if not isinstance(crop, list | tuple) or len(crop) != 4 or not all(_is_num(v) for v in crop):
        return False
    x0, y0, x1, y1 = crop
    return 0 <= x0 < x1 <= FRAME_W and 0 <= y0 < y1 <= FRAME_H


def _thumbnail_errors(short, th) -> list[str]:
    from bsa.review import facts

    if not isinstance(th, dict):
        return ["post thumbnail must be an object with a title, optional kicker and fact, and heroes"]
    errors = []
    lines = _title_lines(th.get("title"))
    if lines is None or not 1 <= len(lines) <= 2 or not all(x.strip() for x in lines):
        errors.append("post thumbnail title must be one or two non-empty lines")
    for key in ("kicker", "fact"):
        if th.get(key) is not None and not isinstance(th[key], str):
            errors.append(f"post thumbnail {key} must be text or null")
    heroes = th.get("heroes", [])
    if not isinstance(heroes, list):
        errors.append("post thumbnail heroes must be a list")
        heroes = []
    elif len(heroes) > 3:
        errors.append(f"post thumbnail has {len(heroes)} heroes; at most 3")
    shots = {sh.id for sh in short.shots}
    for i, h in enumerate(heroes, 1):
        if not isinstance(h, dict):
            errors.append(f"post thumbnail hero {i} must be an object with shot, frame and crop")
            continue
        if h.get("shot") not in shots:
            errors.append(f"post thumbnail hero {i} names shot {h.get('shot')!r}, which is not in the short")
        if not _is_int(h.get("frame")) or h["frame"] < 1:
            errors.append(f"post thumbnail hero {i} frame must be a whole number from 1")
        crop = h.get("crop")
        if crop is not None and not _crop_ok(crop):
            errors.append(f"post thumbnail hero {i} crop must be [x0, y0, x1, y1] inside {FRAME_W}x{FRAME_H}")
    chosen = th.get("chosen")
    if chosen is not None and not _is_int(chosen):
        errors.append("post thumbnail chosen must be a whole number or null")
    stated = {n for f in short.facts for n in facts.numbers(f.claim)}
    for where, text in thumbnail_texts({"thumbnail": th}):
        for n in facts.numbers(text):
            if n not in stated:
                errors.append(f"{where} shows {n}, which no fact states: cite a fact with that number or take it out")
    return errors


def _format_warnings(p: dict, s: dict) -> list[str]:
    fmt = (s or {}).get("post_format")
    if not isinstance(fmt, dict) or not fmt:
        return []
    warnings = []
    title = str(p["title"] or "")
    suffix = fmt.get("title_suffix") or ""
    if suffix and not title.endswith(suffix):
        warnings.append(f"post title does not end with your title suffix {suffix!r}")
    body = title[: len(title) - len(suffix)] if suffix and title.endswith(suffix) else title
    if fmt.get("title_style") == "question" and title and not body.rstrip().endswith("?"):
        warnings.append("post title is not a question, which your post format asks for")
    have = {h.lower() for h in p["hashtags"] if isinstance(h, str)}
    missing = [h for h in fmt.get("always_hashtags") or [] if h.lower() not in have]
    if missing:
        warnings.append(f"post hashtags are missing {', '.join(missing)}, which your post format always includes")
    return warnings


def problems(short, s: dict) -> tuple[list[str], list[str]]:
    """(errors, warnings) for the post block of `short`, with the user's settings `s`."""
    p = fields(short)
    errors: list[str] = []
    warnings: list[str] = []
    for key in ("title", "description", "pinned_comment", "link"):
        if not isinstance(p[key], str):
            errors.append(f"post {key.replace('_', ' ')} must be text")
            p[key] = ""
    for key in ("hashtags", "tags"):
        if not isinstance(p[key], list) or not all(isinstance(x, str) for x in p[key]):
            errors.append(f"post {key} must be a list of text")
            p[key] = []
    title, desc, pinned = p["title"], p["description"], p["pinned_comment"]
    if len(title) > TITLE_MAX:
        errors.append(f"post title is {len(title)} characters; YouTube allows {TITLE_MAX}")
    elif len(title) > TITLE_FEED:
        warnings.append(
            f"post title is {len(title)} characters and may be cut off in the feed (about {TITLE_FEED} show)"
        )
    if len(desc) > DESCRIPTION_MAX:
        errors.append(f"post description is {len(desc):,} characters; YouTube allows {DESCRIPTION_MAX:,}")
    elif len(desc) > PLATFORMS[1][1]:
        warnings.append(f"post description is {len(desc):,} characters, too long for Instagram and TikTok (2,200)")
    for h in p["hashtags"]:
        if not _HASHTAG.match(h):
            errors.append(f"post hashtag {h!r} must be one word starting with #")
    n = len(p["hashtags"])
    if n and not HASHTAGS[0] <= n <= HASHTAGS[1]:
        warnings.append(f"post has {n} hashtag{'s' if n != 1 else ''}; {HASHTAGS[0]} to {HASHTAGS[1]} work best")
    tl = tags_length(p["tags"])
    if tl > TAGS_MAX:
        errors.append(f"post tags take {tl} of YouTube's {TAGS_MAX} characters")
    if len(pinned) > PINNED_MAX:
        errors.append(f"post pinned comment is {len(pinned):,} characters; YouTube allows {PINNED_MAX:,}")
    link = p["link"].strip()
    if link:
        if not link.startswith("https://"):
            errors.append(f"post link {link!r} must start with https://")
        else:
            site = _site_host((s or {}).get("website"))
            host = _host(link).removeprefix("www.")
            if not site or not (host == site or host.endswith("." + site)):
                where = f"your website ({site})" if site else "your website (none is set)"
                warnings.append(f"post link {link} is not on {where}")
        if pinned and link not in pinned:
            warnings.append("post pinned comment does not contain the link")
    rel = p["related"]
    if rel is not None:
        if not isinstance(rel, dict):
            errors.append('post related must be {"title", "url"} or null')
        elif rel.get("url") and not str(rel["url"]).startswith("https://"):
            errors.append(f"post related url {rel['url']!r} must start with https://")
    th = p["thumbnail"]
    if th is not None:
        errors += _thumbnail_errors(short, th)
        lines = _title_lines(th.get("title")) if isinstance(th, dict) else None
        if lines and sum(len(x.split()) for x in lines) > THUMB_WORDS:
            warnings.append(f"post thumbnail title is over {THUMB_WORDS} words; short titles read in the feed")
    posted = p["posted"]
    if not isinstance(posted, list) or not all(
        isinstance(x, dict) and all(isinstance(x.get(k), str) and x[k] for k in ("platform", "url", "at"))
        for x in posted
    ):
        errors.append('post posted must be a list of {"platform", "url", "at"}')
    warnings += _format_warnings(p, s)
    return errors, warnings
