"""Static font weights from Google Fonts, for a brand font installed only as a variable font (Blender loads just the
variable font's default instance, often Light).

`font:Family@WEIGHT` plans one static TTF: Google's CSS API (keyless) serves each weight of a family as its own
static file; the licence comes from the family's METADATA.pb in the public google/fonts repository and must be one
that allows commercial use and drawing the font into a video (OFL, Apache 2.0, Ubuntu Font Licence). Nothing is
downloaded until the user says yes to the plan; the file lands in `<workspace>/.assets-cache/fonts/`.
"""

from __future__ import annotations

import re
import urllib.parse
from urllib.error import HTTPError

from bsa.assets import polyhaven
from bsa.assets.sounds import _urlopen, content_length
from bsa.fonts import weight_name
from bsa.short import Asset

CSS_API = "https://fonts.googleapis.com/css2"
REPO = "https://raw.githubusercontent.com/google/fonts/main"
LICENCE_DIRS = ("ofl", "apache", "ufl")
LICENCES = {"OFL": "OFL-1.1", "APACHE2": "Apache-2.0", "UFL": "UFL-1.0"}
_SRC = re.compile(r"src:\s*url\((https://fonts\.gstatic\.com/[^)\s]+\.ttf)\)")


def css_url(family: str, weight: int) -> str:
    return f"{CSS_API}?" + urllib.parse.urlencode({"family": f"{family}:wght@{int(weight)}"})


def metadata_url(folder: str, family: str) -> str:
    return f"{REPO}/{folder}/{re.sub(r'[^a-z0-9]', '', family.lower())}/METADATA.pb"


def _read(url: str, http) -> str:
    resp = http(url)
    try:
        return resp.read().decode("utf-8", "replace")
    finally:
        resp.close()


def _licence(family: str, http) -> str:
    for folder in LICENCE_DIRS:
        try:
            text = _read(metadata_url(folder, family), http)
        except (HTTPError, OSError):
            continue
        m = re.search(r'^license:\s*"([A-Z0-9]+)"', text, re.MULTILINE)
        if m and m.group(1) in LICENCES:
            return LICENCES[m.group(1)]
    raise ValueError(f"couldn't confirm the licence of {family} on Google Fonts, so it is not offered")


def parse(item: str) -> tuple[str, int]:
    """("Fredoka", 600) from "font:Fredoka@600"."""
    spec = item.removeprefix("font:")
    family, _, weight = spec.rpartition("@")
    if not family or not weight.isdigit() or not 100 <= int(weight) <= 900:
        raise ValueError(f"{item!r}: give a family and a weight from 100 to 900, like font:Fredoka@600")
    return family.strip(), int(weight)


def plan(items: list[str], http=_urlopen, head=content_length) -> list[Asset]:
    out = []
    for item in items:
        family, weight = parse(item)
        m = _SRC.search(_read(css_url(family, weight), http))
        if not m:
            raise ValueError(f"Google Fonts has no static {family} at weight {weight}")
        url = m.group(1)
        licence = _licence(family, http)
        size = head(url)
        f = {"url": url, "role": "font", "path": f"fonts/{re.sub(r'[^A-Za-z0-9]', '', family)}-{weight}.ttf",
             **({"bytes": size} if size else {"bytes": 300_000, "max_bytes": 5_000_000})}  # fmt: skip
        out.append(Asset(id=f"font-{re.sub(r'[^a-z0-9]', '', family.lower())}-{weight}",
                         name=f"{family} {weight_name(weight)}", kind="fonts", source="Google Fonts", license=licence,
                         files=[f], bytes=f["bytes"]))  # fmt: skip
    return out


def is_font_id(item: str) -> bool:
    return item.startswith("font:")


def download(plans: list[Asset], cache_dir, http=_urlopen):
    """The font files (approval first): {asset id: [path]}; set a short's style.font to the path."""
    return polyhaven.download(plans, cache_dir, http)
