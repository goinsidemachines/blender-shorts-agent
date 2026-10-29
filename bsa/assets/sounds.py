"""Recorded sound effects (CC0 only): search, a download plan the user approves, verified download, the clip library.

Sources, all CC0 (public domain dedication: commercial use, no attribution required):

- **Curated packs** (`pack:<id>`): known CC0 packs hosted as plain files on OpenGameArt, including Kenney's, at
  stable URLs. Downloaded as a zip; the audio files are extracted into the cache and tagged with sfx kinds from
  their file names ("impactWood_heavy" -> impact, thud).
- **Wikimedia Commons** (`commons:<file name>`): keyless public API; each file's licence is read from its metadata
  and anything but CC0 is refused; downloads are checked against the file's SHA-1.
- **Freesound** (`freesound:<id>`): only with an API key (`settings set freesound.api_key KEY`, or
  $FREESOUND_API_KEY), sent in a header, never in a URL. Search is filtered to CC0, and each sound's licence is
  checked again at plan time. The HQ OGG preview is downloaded (full originals need OAuth).

Not offered: Sonniss GDC bundles (royalty-free, but not CC0), and anything else without a CC0 licence.

Only `download` fetches sound files, and only after the user's yes to the `format_plan` table. Search and plan read
small JSON (and ask a file's size with a HEAD request).
"""

from __future__ import annotations

import json
import re
import shutil
import socket
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from html import unescape
from pathlib import Path, PurePosixPath

from bsa.assets import polyhaven
from bsa.audio import decode, recorded
from bsa.audio.sfx import KINDS
from bsa.short import Asset

COMMONS_API = "https://commons.wikimedia.org/w/api.php"
FREESOUND_API = "https://freesound.org/apiv2"
AUDIO_EXT = (".wav", ".ogg", ".flac", ".mp3", ".oga")
CC0_URLS = ("creativecommons.org/publicdomain/zero/",)
MAX_MEMBER_BYTES = 60_000_000
MAX_ARCHIVE_BYTES = 400_000_000
UNKNOWN_SIZE_FACTOR = 3  # with no Content-Length, a pack may be this many times its catalogue size at most
LEGACY_DIRS = ("sfx",)  # cache folders earlier versions kept sound packs in (`.assets-cache/sfx/<folder>/*.zip`)
LEGACY_NAME_WORDS = 3  # with no size to match, an old folder is a pack's when it shares this many name words
DESIGNED_NOTE = ("designed UI sounds (tonal, partly synthesised): fine for interface clicks, not used under "
                 "sound_policy natural-only")
RETRY = ("its server could not be reached (no connection, DNS, or the site is down): run the same {what} again "
         "in a minute, or check the connection")

PACKS = {
    "oga-metal-wood": {
        "name": "100 CC0 metal and wood SFX", "author": "rubberduck", "license": "CC0", "approx_bytes": 2_000_000,
        "page": "https://opengameart.org/content/100-cc0-metal-and-wood-sfx",
        "url": "https://opengameart.org/sites/default/files/100-CC0-wood-metal-SFX.zip",
        "tags": "metal wood hit impact thud slam drop fall land door lock key click latch creak squeak tools hammer "
                "spring sheet scrape",
        "kinds": ["impact", "click", "creak"],
    },
    "kenney-ui-sfx": {
        "name": "51 UI sound effects (buttons, switches and clicks)", "author": "Kenney", "license": "CC0",
        "approx_bytes": 1_400_000, "page": "https://opengameart.org/content/51-ui-sound-effects-buttons-switches-and-clicks",
        "url": "https://opengameart.org/sites/default/files/UI_SFX_Set.zip",
        "tags": "click switch button toggle tick latch ui interface",
        "kinds": ["click"],
        "designed": True,  # interface sounds, tonal and partly synthesised: not used under natural-only
    },
    "kenney-rpg": {
        "name": "50 RPG sound effects", "author": "Kenney", "license": "CC0", "approx_bytes": 691_000,
        "page": "https://opengameart.org/content/50-rpg-sound-effects",
        "url": "https://opengameart.org/sites/default/files/RPGsounds_Kenney.zip",
        "tags": "door creak metal latch click cloth footstep chop knife slide drag wood coins book",
        "kinds": ["thud", "click", "slide", "creak"],
    },
}  # fmt: skip
# `tags` are words a search matches. `kinds` are the sfx kinds the pack's clips get when indexed (auto_kinds of its
# file names): search shows these, never kinds read off the tags, so it promises no kind the library will not have.

# File-name words -> sfx kind (a clip can have several). Order: the kinds' order in the result.
KIND_WORDS = {
    "impact": ("impact", "hit", "bang", "knock", "punch", "slam", "hammer", "strike", "smash"),
    "thud": ("thud", "thump", "heavy", "drop", "fall", "land", "landing", "body"),
    "click": ("click", "tick", "switch", "button", "latch", "lock", "unlock", "key", "toggle", "notch", "clack"),
    "scrape": ("scrape", "scratch", "grind", "rasp", "scraping"),
    "slide": ("slide", "sliding", "drag", "draw", "shove", "push"),
    "creak": ("creak", "creaking", "squeak", "door", "hinge", "groan"),
    "rumble": ("rumble", "quake", "earthquake"),
    "hiss": ("hiss", "steam", "spray", "air"),
    "wind": ("wind", "gust", "breeze"),
    "whoosh": ("whoosh", "swish", "swoosh", "woosh", "swing"),
}

def _urlopen(url: str, headers: dict | None = None):
    req = urllib.request.Request(url, headers={"User-Agent": polyhaven.USER_AGENT, **(headers or {})})
    return urllib.request.urlopen(req, timeout=polyhaven.TIMEOUT_S)


def content_length(url: str) -> int | None:
    """The file's size from a HEAD request, or None when the server does not say."""
    req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": polyhaven.USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=polyhaven.TIMEOUT_S) as resp:
            value = resp.headers.get("Content-Length")
            return int(value) if value and value.isdigit() else None
    except OSError:
        return None


def _get_json(url: str, http, headers: dict | None = None):
    resp = http(url, headers) if headers else http(url)
    try:
        return json.loads(resp.read().decode("utf-8"))
    finally:
        resp.close()


def _words(name: str) -> list[str]:
    stem = PurePosixPath(name).stem
    return [w.lower() for w in re.findall(r"[A-Z]?[a-z]+|[A-Z]+(?![a-z])", stem)]


def auto_kinds(name: str) -> list[str]:
    """The sfx kinds a clip's file name suggests (["impact", "thud"] for "impactWood_heavy_003.ogg")."""
    words = set(_words(name))
    return [kind for kind, keys in KIND_WORDS.items() if words & set(keys)]


def _safe(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", text).strip("._") or "sound"


def _strip_html(text: str) -> str:
    return " ".join(unescape(re.sub(r"<[^>]+>", " ", str(text or ""))).split())


def _is_cc0(licence: str) -> bool:
    text = str(licence or "").strip().lower()
    return text in ("cc0", "cc0 1.0", "cc-zero", "creative commons 0") or any(u in text for u in CC0_URLS)


# --------------------------------------------------------------------------- URLs


def commons_search_url(query: str, limit: int = 20) -> str:
    return COMMONS_API + "?" + urllib.parse.urlencode({
        "action": "query", "format": "json", "generator": "search", "gsrnamespace": 6,
        "gsrsearch": f"{query} filetype:audio", "gsrlimit": limit, "prop": "imageinfo",
        "iiprop": "url|size|sha1|mime|extmetadata", "iiextmetadatafilter": "LicenseShortName|Artist"})  # fmt: skip


def commons_file_url(title: str) -> str:
    return COMMONS_API + "?" + urllib.parse.urlencode({
        "action": "query", "format": "json", "titles": f"File:{title}", "prop": "imageinfo",
        "iiprop": "url|size|sha1|mime|extmetadata", "iiextmetadatafilter": "LicenseShortName|Artist"})  # fmt: skip


def freesound_search_url(query: str, limit: int = 15) -> str:
    return f"{FREESOUND_API}/search/text/?" + urllib.parse.urlencode({
        "query": query, "filter": 'license:"Creative Commons 0"', "page_size": limit,
        "fields": "id,name,license,username,duration,url,previews"})  # fmt: skip


def freesound_sound_url(sound_id: str) -> str:
    return f"{FREESOUND_API}/sounds/{int(sound_id)}/?fields=id,name,license,username,duration,url,previews"


def _auth(key: str) -> dict:
    return {"Authorization": f"Token {key}"}


# --------------------------------------------------------------------------- search


def _commons_items(data: dict) -> list[dict]:
    out = []
    for page in (data.get("query") or {}).get("pages", {}).values():
        info = (page.get("imageinfo") or [{}])[0]
        meta = info.get("extmetadata") or {}
        title = str(page.get("title", "")).removeprefix("File:")
        out.append({"title": title, "url": info.get("url", ""), "bytes": int(info.get("size") or 0),
                    "sha1": info.get("sha1", ""), "mime": info.get("mime", ""), "page": info.get("descriptionurl", ""),
                    "license": (meta.get("LicenseShortName") or {}).get("value", ""),
                    "author": _strip_html((meta.get("Artist") or {}).get("value", ""))})  # fmt: skip
    return out


HOSTS = {"Wikimedia Commons": "commons.wikimedia.org", "Freesound": "freesound.org", "OpenGameArt": "opengameart.org"}


def _unreachable(exc: Exception) -> bool:
    """A connection failure (DNS, refused, timeout, no route), as opposed to a server's answer."""
    if isinstance(exc, urllib.error.HTTPError):
        return False
    return isinstance(exc, (urllib.error.URLError, ConnectionError, TimeoutError, socket.gaierror, socket.timeout))


def plain_error(source: str, exc: Exception) -> str:
    """The failure in plain words: "can't reach commons.wikimedia.org (network or DNS)" for a connection failure,
    else the error's own text."""
    if _unreachable(exc):
        return f"can't reach {HOSTS.get(source, source)} (network or DNS)"
    return str(exc) or type(exc).__name__


def skipped(source: str, exc: Exception, what: str = "search", item: str | None = None) -> dict:
    """A source left out because it failed: {"source", "error", "retry"} (and the planned `id`). A connection failure
    reads "can't reach <host> (network or DNS)", never the raw urlopen error."""
    out = {"source": source, "error": plain_error(source, exc), "retry": RETRY.format(what=what)}
    return {"id": item, **out} if item else out


def source_name(item: str) -> str:
    """The source a sound id comes from: "OpenGameArt", "Wikimedia Commons" or "Freesound"."""
    return {"pack": "OpenGameArt", "commons": "Wikimedia Commons", "freesound": "Freesound"}.get(
        item.split(":", 1)[0], item)


def pack_kinds(pid: str, cache_dir: Path | None = None) -> tuple[list[str], str]:
    """(kinds, where from) for a pack: the kinds its clips have in the library when it is indexed there
    ("library"), else the catalogue's `kinds` ("catalogue")."""
    if cache_dir is not None:
        clips = [c for c in recorded.Library(cache_dir).clips if c.get("asset") == f"sfx-{pid}"]
        if clips:
            have = {k for c in clips for k in c.get("kinds", [])}
            return [k for k in KIND_WORDS if k in have], "library"
    return list(PACKS[pid]["kinds"]), "catalogue"


def search(query: str, limit: int = 12, http=_urlopen, freesound_key: str | None = None,
           cache_dir: Path | None = None) -> dict:
    """{"results": [...], "notes": [...], "skipped": [...]}: CC0 recordings matching `query` from the packs,
    Wikimedia Commons and (with a key) Freesound. Each result has an `id` to plan (pack:..., commons:...,
    freesound:...), its name, licence, the kinds its name suggests and where it comes from. A source that fails is
    left out and named in `skipped` ({"source", "error", "retry"}) and in a note, so the user hears which source was
    not searched and that a retry may find more. A pack's `kinds` are what its clips are tagged: from the library
    when it is cached in `cache_dir` (`kinds_from` "library"), else from the catalogue."""
    words = query.lower().split()
    results, notes, skips = [], [], []
    scored = []
    for pid, p in PACKS.items():
        hay = (p["name"] + " " + p["tags"]).lower()
        score = sum(w in hay for w in words)
        if score:
            kinds, kinds_from = pack_kinds(pid, cache_dir)
            scored.append((-score, pid, {"id": f"pack:{pid}", "name": p["name"], "provider": "pack", "license": "CC0",
                                         "source": f"OpenGameArt ({p['author']})", "bytes": p["approx_bytes"],
                                         "page": p["page"], "kinds": kinds, "kinds_from": kinds_from,
                                         **({"designed": True, "note": DESIGNED_NOTE} if p.get("designed") else {})}))  # fmt: skip
    results += [r for *_, r in sorted(scored)]
    try:
        for it in _commons_items(_get_json(commons_search_url(query), http)):
            if not it["mime"].startswith("audio/") and not it["mime"].startswith("application/ogg"):
                continue
            if _is_cc0(it["license"]):
                results.append({"id": f"commons:{it['title']}", "name": it["title"], "provider": "commons",
                                "license": "CC0", "source": f"Wikimedia Commons ({it['author'] or 'unknown'})",
                                "bytes": it["bytes"], "page": it["page"], "kinds": auto_kinds(it["title"])})  # fmt: skip
    except (OSError, ValueError) as exc:
        skips.append(skipped("Wikimedia Commons", exc))
    if freesound_key:
        try:
            data = _get_json(freesound_search_url(query), http, _auth(freesound_key))
            for s in data.get("results", []):
                if _is_cc0(s.get("license")) and (s.get("previews") or {}).get("preview-hq-ogg"):
                    results.append({"id": f"freesound:{s['id']}", "name": s.get("name", ""), "provider": "freesound",
                                    "license": "CC0", "source": f"Freesound ({s.get('username', 'unknown')})",
                                    "duration_s": s.get("duration"), "page": s.get("url", ""),
                                    "kinds": auto_kinds(s.get("name", ""))})  # fmt: skip
        except (OSError, ValueError) as exc:
            skips.append(skipped("Freesound", exc))
    else:
        notes.append("Freesound is not searched: it needs a free API key (settings set freesound.api_key KEY, from "
                     "freesound.org/apiv2/apply)")  # fmt: skip
    for s in skips:
        why = s["error"] if s["error"].startswith("can't reach") else f"its search failed ({s['error']})"
        notes.append(f"{s['source']} was skipped: {why}; searched the other sources only. {s['retry'][0].upper()}"
                     f"{s['retry'][1:]}.")  # fmt: skip
    return {"results": results[: max(limit, len(PACKS))], "notes": notes, "skipped": skips}


# --------------------------------------------------------------------------- plan


def _kinds(spec: str, name: str) -> list[str]:
    if not spec:
        return auto_kinds(name)
    kinds = [k.strip() for k in spec.split(",") if k.strip()]
    bad = [k for k in kinds if k not in KINDS]
    if bad:
        raise ValueError(f"unknown sfx kind {', '.join(bad)}; valid kinds: {', '.join(sorted(KINDS))}")
    return kinds


def _size(url: str, head, approx: int) -> dict:
    n = head(url)
    return {"bytes": n} if n else {"bytes": approx, "max_bytes": approx * UNKNOWN_SIZE_FACTOR}


def _words_of(text: str) -> set[str]:
    return {w for w in re.split(r"[^a-z0-9]+", text.lower()) if w}


def _is_audio_zip(path: Path) -> bool:
    try:
        with zipfile.ZipFile(path) as z:
            return any(PurePosixPath(n).suffix.lower() in AUDIO_EXT for n in z.namelist())
    except (OSError, zipfile.BadZipFile):
        return False


def _legacy_archive(cache_dir: Path, pid: str, f: dict) -> Path | None:
    """A pack's archive kept by an earlier version (`<cache>/sfx/<folder>/<any>.zip`): the zip of exactly the pack's
    size when the size is known; with no size, the one whose folder and file names share LEGACY_NAME_WORDS words
    with the pack (its id, archive name and author). Only zips holding audio count."""
    p = PACKS[pid]
    want = _words_of(f"{pid} {PurePosixPath(f['path']).stem} {p['author']}")
    exact = None if f.get("max_bytes") else f["bytes"]
    best = None
    for legacy in LEGACY_DIRS:
        root = Path(cache_dir) / legacy
        if not root.is_dir():
            continue
        for zp in sorted(root.glob("*/*.zip")):
            if exact is not None:
                if zp.stat().st_size == exact and _is_audio_zip(zp):
                    return zp
                continue
            score = len(want & _words_of(f"{zp.parent.name} {zp.stem}"))
            if score >= LEGACY_NAME_WORDS and (best is None or score > best[0]) and _is_audio_zip(zp):
                best = (score, zp)
    return best[1] if best else None


def _reuse_cached(cache_dir: Path, pid: str, f: dict) -> None:
    """Mark a pack's archive `cached` when it is already in the cache (this layout, or an earlier one's, copied in):
    then the plan asks for no download."""
    target = polyhaven._target(cache_dir, f["path"])
    size_ok = (lambda n: n == f["bytes"]) if not f.get("max_bytes") else (lambda n: 0 < n <= int(f["max_bytes"]))
    if target.is_file() and size_ok(target.stat().st_size):
        f["cached"] = True
        return
    old = _legacy_archive(cache_dir, pid, f)
    if old is None or not size_ok(old.stat().st_size):
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(old, target)  # the old folder is left as it was
    f["cached"] = True
    f["reused_from"] = str(old)


def _plan_pack(pid: str, head, cache_dir: Path | None = None) -> Asset:
    if pid not in PACKS:
        raise ValueError(f"no such pack {pid!r}; packs: {', '.join(PACKS)}")
    p = PACKS[pid]
    name = PurePosixPath(urllib.parse.urlparse(p["url"]).path).name
    f = {"url": p["url"], "role": "archive", "path": f"sounds/{pid}/{name}", "page": p["page"], "author": p["author"],
         **_size(p["url"], head, p["approx_bytes"])}  # fmt: skip
    if p.get("designed"):
        f.update(designed=True, note=DESIGNED_NOTE)
    if cache_dir is not None:
        _reuse_cached(Path(cache_dir), pid, f)
    return Asset(id=f"sfx-{pid}", name=p["name"], kind="sounds", source=f"OpenGameArt ({p['author']})",
                 license=p["license"], files=[f], bytes=f["bytes"])  # fmt: skip


def _plan_commons(title: str, kinds: str, http) -> Asset:
    items = _commons_items(_get_json(commons_file_url(title), http))
    it = next((i for i in items if i["url"]), None)
    if it is None:
        raise ValueError(f"Wikimedia Commons has no file {title!r}")
    if not _is_cc0(it["license"]):
        raise ValueError(f"{title} is licensed {it['license'] or 'unknown'}, not CC0: only CC0 sounds are offered")
    if PurePosixPath(title).suffix.lower() not in AUDIO_EXT:
        raise ValueError(f"{title} is not a sound file this plugin reads ({', '.join(AUDIO_EXT)})")
    f = {"url": it["url"], "bytes": it["bytes"], "sha1": it["sha1"], "role": "clip",
         "path": f"sounds/commons/{_safe(title)}", "kinds": _kinds(kinds, title), "page": it["page"],
         "author": it["author"]}  # fmt: skip
    return Asset(id=f"sfx-commons-{_safe(PurePosixPath(title).stem)}", name=PurePosixPath(title).stem.replace("_", " "),
                 kind="sounds", source=f"Wikimedia Commons ({it['author'] or 'unknown'})", license="CC0", files=[f],
                 bytes=f["bytes"])  # fmt: skip


def _plan_freesound(sound_id: str, kinds: str, http, head, key: str | None) -> Asset:
    if not key:
        raise ValueError("Freesound needs an API key: settings set freesound.api_key KEY (free, from "
                         "freesound.org/apiv2/apply)")  # fmt: skip
    s = _get_json(freesound_sound_url(sound_id), http, _auth(key))
    if not _is_cc0(s.get("license")):
        raise ValueError(f"Freesound sound {sound_id} is licensed {s.get('license')}, not CC0: only CC0 is offered")
    url = (s.get("previews") or {}).get("preview-hq-ogg")
    if not url:
        raise ValueError(f"Freesound sound {sound_id} has no OGG preview")
    name = str(s.get("name") or sound_id)
    f = {"url": url, "role": "clip", "path": f"sounds/freesound/{int(sound_id)}-{_safe(PurePosixPath(name).stem)}.ogg",
         "kinds": _kinds(kinds, name), "page": s.get("url", ""), "author": s.get("username", ""),
         **_size(url, head, 400_000)}  # fmt: skip
    return Asset(id=f"sfx-freesound-{int(sound_id)}", name=name, kind="sounds",
                 source=f"Freesound ({s.get('username', 'unknown')})", license="CC0", files=[f], bytes=f["bytes"])


def plan(items: list[str], http=_urlopen, head=content_length, freesound_key: str | None = None,
         cache_dir: Path | None = None) -> list[Asset]:  # fmt: skip
    """Assets for items `pack:ID`, `commons:FILE NAME[=kind,kind]` or `freesound:ID[=kind,...]` (kinds default to
    what the file name suggests). Refuses anything not CC0. Reads JSON and file sizes only. With `cache_dir`, a pack
    already there (or in an earlier version's `sfx/` folder, copied in) is marked `cached`: nothing to download."""
    out = []
    for item in items:
        provider, _, rest = item.partition(":")
        ref, _, kinds = rest.partition("=")
        if kinds:
            _kinds(kinds, ref)  # a typo in a kind fails before any request
        if provider == "pack":
            out.append(_plan_pack(ref, head, cache_dir))
        elif provider == "commons":
            out.append(_plan_commons(ref.removeprefix("File:"), kinds, http))
        elif provider == "freesound":
            out.append(_plan_freesound(ref, kinds, http, head, freesound_key))
        else:
            raise ValueError(f"unknown sound id {item!r}: use pack:ID, commons:FILE or freesound:ID")
    return out


def is_sound_id(item: str) -> bool:
    return item.split(":", 1)[0] in ("pack", "commons", "freesound") and ":" in item


# --------------------------------------------------------------------------- download


def _extract(archive: Path, pid: str, cache_dir: Path) -> list[str]:
    """Unpack the audio files of a pack into `sounds/<pid>/` in the cache; returns their cache paths. Names are
    remote data: anything that would escape the folder is skipped, and sizes are capped."""
    out, total = [], 0
    with zipfile.ZipFile(archive) as z:
        for info in z.infolist():
            parts = info.filename.replace("\\", "/").split("/")
            if info.is_dir() or parts[0] == "__MACOSX" or PurePosixPath(parts[-1]).suffix.lower() not in AUDIO_EXT:
                continue
            if any(p in ("", ".", "..") or ":" in p for p in parts) or parts[-1].startswith("._"):
                continue
            if info.file_size > MAX_MEMBER_BYTES or total + info.file_size > MAX_ARCHIVE_BYTES:
                continue
            rel = "/".join(["sounds", pid, *parts])
            dest = polyhaven._target(cache_dir, rel)
            if not (dest.is_file() and dest.stat().st_size == info.file_size):
                dest.parent.mkdir(parents=True, exist_ok=True)
                with z.open(info) as src, open(dest, "wb") as dst:
                    shutil.copyfileobj(src, dst)
            total += info.file_size
            out.append(rel)
    return out


def _flatness(path: Path) -> float | None:
    """A WAV clip's spectral flatness (other formats would need Blender to decode: left out)."""
    if path.suffix.lower() != ".wav":
        return None
    try:
        x, sr = decode.read_wav(path)
    except (OSError, ValueError):
        return None
    return recorded.flatness(x, sr)


def _index(lib, asset_id: str, source: str, licence: str, f: dict, clips: list[tuple[str, list[str]]]) -> None:
    designed = bool(f.get("designed") or PACKS.get(asset_id.removeprefix("sfx-"), {}).get("designed"))
    lib.add([{"path": rel, "kinds": kinds, "asset": asset_id, "name": PurePosixPath(rel).stem, "source": source,
              "license": licence, "author": f.get("author", ""), "page": f.get("page", ""), "designed": designed,
              "flatness": _flatness(polyhaven._target(lib.cache_dir, rel))}
             for rel, kinds in clips])  # fmt: skip


def adopt_legacy(cache_dir: Path) -> list[str]:
    """Index packs an earlier version left in `<cache>/sfx/<folder>/` (files/, wav48/, pack.zip), once and with no
    network: each known pack not in the library yet whose old archive is found by name is copied into
    `sounds/<pack>/`, unpacked and added to the library. The old folder is left as it was. Returns the asset ids
    added."""
    cache_dir = Path(cache_dir)
    if not any((cache_dir / d).is_dir() for d in LEGACY_DIRS):
        return []
    lib = recorded.Library(cache_dir)
    have = {c.get("asset") for c in lib.clips}
    added = []
    for pid, p in PACKS.items():
        if f"sfx-{pid}" in have:
            continue
        name = PurePosixPath(urllib.parse.urlparse(p["url"]).path).name
        f = {"path": f"sounds/{pid}/{name}", "bytes": p["approx_bytes"],
             "max_bytes": p["approx_bytes"] * UNKNOWN_SIZE_FACTOR, "author": p["author"], "page": p["page"]}
        _reuse_cached(cache_dir, pid, f)
        if not f.get("cached"):
            continue
        rels = _extract(polyhaven._target(cache_dir, f["path"]), pid, cache_dir)
        if rels:
            _index(lib, f"sfx-{pid}", f"OpenGameArt ({p['author']})", p["license"], f,
                   [(rel, auto_kinds(rel)) for rel in rels])
            added.append(f"sfx-{pid}")
    if added:
        lib.save()
    return added


def download(plans: list[Asset], cache_dir: Path, http=_urlopen) -> dict[str, list[Path]]:
    """Fetch sound assets (approval first), unpack packs, add every clip to the library with its kinds, source and
    licence. Returns {asset id: [clip files]}."""
    cache_dir = Path(cache_dir)
    lib = recorded.Library(cache_dir)
    result: dict[str, list[Path]] = {}
    for a in plans:
        polyhaven.download([a], cache_dir, http)
        paths = []
        for f in a.files:
            if f["role"] == "archive":
                rels = _extract(polyhaven._target(cache_dir, f["path"]), a.id.removeprefix("sfx-"), cache_dir)
                clips = [(rel, auto_kinds(rel)) for rel in rels]
            else:
                clips = [(f["path"], list(f.get("kinds") or auto_kinds(f["path"])))]
            _index(lib, a.id, a.source, a.license, f, clips)
            paths += [polyhaven._target(cache_dir, rel) for rel, _ in clips]
        result[a.id] = paths
    lib.save()
    return result
