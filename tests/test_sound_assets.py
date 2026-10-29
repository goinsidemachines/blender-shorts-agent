"""Recorded sound effects as assets: search, a plan the user approves, verified download, the clip library.
Every HTTP request is faked; nothing is downloaded."""

import hashlib
import io
import json
import wave
import zipfile
from pathlib import Path

import numpy as np
import pytest

from bsa.assets import gfonts, polyhaven, sounds
from bsa.audio import recorded
from bsa.short import Short, load_short, save_short


class FakeHttp:
    """url -> bytes (or an exception to raise); records each request and its headers."""

    def __init__(self, routes, sizes=None):
        self.routes = dict(routes)
        self.sizes = dict(sizes or {})
        self.calls = []

    def __call__(self, url, headers=None):
        self.calls.append((url, dict(headers or {})))
        if url not in self.routes:
            raise AssertionError(f"unexpected request: {url}")
        body = self.routes[url]
        if isinstance(body, Exception):
            raise body
        return io.BytesIO(body)

    def head(self, url):
        self.calls.append(("HEAD " + url, {}))
        return self.sizes.get(url)


def _wav_bytes(f=300.0, sec=0.2):
    buf = io.BytesIO()
    t = np.arange(int(sec * 48000)) / 48000
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(48000)
        w.writeframes((0.5 * np.sin(2 * np.pi * f * t) * 32767).astype("<i2").tobytes())
    return buf.getvalue()


def _zip(members: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, data in members.items():
            z.writestr(name, data)
    return buf.getvalue()


def _commons_page(title, licence="CC0", size=1234, url=None, sha1="", mime="audio/ogg"):
    return {"title": f"File:{title}", "imageinfo": [{
        "url": url or f"https://upload.wikimedia.org/wikipedia/commons/a/ab/{title}", "size": size, "sha1": sha1,
        "mime": mime, "descriptionurl": f"https://commons.wikimedia.org/wiki/File:{title}",
        "extmetadata": {"LicenseShortName": {"value": licence}, "Artist": {"value": "<a href='x'>Jane Recordist</a>"}}}]}  # fmt: skip


def _commons_json(*pages):
    return json.dumps({"query": {"pages": {str(i): p for i, p in enumerate(pages)}}}).encode()


def test_auto_kinds_from_file_names():
    assert sounds.auto_kinds("impactWood_heavy_003.ogg") == ["impact", "thud"]
    assert "click" in sounds.auto_kinds("metalLatch.ogg") and "creak" in sounds.auto_kinds("doorOpen_1.ogg")
    assert sounds.auto_kinds("wood_scrape_02.wav") == ["scrape"]
    assert sounds.auto_kinds("drawKnife2.ogg") == ["slide"]
    assert sounds.auto_kinds("music_loop.ogg") == []


def test_catalogue_packs_are_cc0_https_and_not_sonniss():
    for pack in sounds.PACKS.values():
        assert pack["license"] == "CC0" and pack["url"].startswith("https://") and pack["page"].startswith("https://")
        assert "sonniss" not in (pack["url"] + pack["page"] + pack["name"]).lower()


def test_search_packs_and_commons_cc0_only():
    q = sounds.commons_search_url("wood thud")
    http = FakeHttp({q: _commons_json(_commons_page("Wood_thud.ogg"), _commons_page("Bang.ogg", licence="CC BY-SA 4.0"),
                                      _commons_page("Thud.png", mime="image/png"))})  # fmt: skip
    out = sounds.search("wood thud", http=http, freesound_key=None)
    ids = [r["id"] for r in out["results"]]
    assert "pack:oga-metal-wood" in ids and "commons:Wood_thud.ogg" in ids
    assert not any("Bang" in i or "png" in i for i in ids)  # not CC0 / not audio
    assert all(r["license"] == "CC0" for r in out["results"])
    assert any("Freesound" in n and "api_key" in n for n in out["notes"])


def test_search_survives_a_provider_failure():
    q = sounds.commons_search_url("click")
    out = sounds.search("click", http=FakeHttp({q: OSError("offline")}), freesound_key=None)
    assert any(r["id"].startswith("pack:") for r in out["results"])
    assert any("Wikimedia Commons" in n for n in out["notes"])


def test_freesound_search_uses_the_key_in_a_header_and_filters_cc0():
    q = sounds.commons_search_url("scrape")
    fs = sounds.freesound_search_url("scrape")
    body = json.dumps({"results": [
        {"id": 11, "name": "metal scrape", "license": "http://creativecommons.org/publicdomain/zero/1.0/",
         "username": "rec", "duration": 1.2, "url": "https://freesound.org/people/rec/sounds/11/",
         "previews": {"preview-hq-ogg": "https://cdn.freesound.org/previews/0/11_x-hq.ogg"}},
        {"id": 12, "name": "scrape by", "license": "https://creativecommons.org/licenses/by/4.0/", "username": "r",
         "previews": {}}]}).encode()  # fmt: skip
    http = FakeHttp({q: _commons_json(), fs: body})
    out = sounds.search("scrape", http=http, freesound_key="SECRET")
    ids = [r["id"] for r in out["results"]]
    assert "freesound:11" in ids and "freesound:12" not in ids
    (call,) = [c for c in http.calls if c[0] == fs]
    assert "SECRET" not in call[0] and call[1]["Authorization"] == "Token SECRET"


def test_plan_pack_commons_and_freesound():
    pack = sounds.PACKS["oga-metal-wood"]
    title_url = sounds.commons_file_url("Wood_thud.ogg")
    fs_url = sounds.freesound_sound_url("11")
    preview = "https://cdn.freesound.org/previews/0/11_x-hq.ogg"
    http = FakeHttp({
        title_url: _commons_json(_commons_page("Wood_thud.ogg", size=5000, sha1="ab" * 20)),
        fs_url: json.dumps({"id": 11, "name": "metal scrape", "license":
                            "http://creativecommons.org/publicdomain/zero/1.0/", "username": "rec",
                            "url": "https://freesound.org/people/rec/sounds/11/",
                            "previews": {"preview-hq-ogg": preview}}).encode(),
    }, sizes={pack["url"]: 2_100_000, preview: 40_000})  # fmt: skip
    plans = sounds.plan(["pack:oga-metal-wood", "commons:Wood_thud.ogg=thud", "freesound:11=scrape,slide"],
                        http=http, head=http.head, freesound_key="K")  # fmt: skip
    p, c, f = plans
    assert all(a.kind == "sounds" and a.license == "CC0" for a in plans)
    assert p.bytes == 2_100_000 and p.files[0]["role"] == "archive" and p.files[0]["path"].startswith("sounds/")
    assert "rubberduck" in p.source and p.files[0]["page"] == pack["page"]
    assert c.files[0]["kinds"] == ["thud"] and c.files[0]["sha1"] == "ab" * 20 and c.bytes == 5000
    assert "Jane Recordist" in c.source and "<a" not in c.source
    assert f.files[0]["kinds"] == ["scrape", "slide"] and f.files[0]["url"] == preview and f.bytes == 40_000
    table = polyhaven.format_plan(plans)
    assert "CC0" in table and "Wikimedia Commons" in table


def test_plan_refuses_non_cc0_and_unknown_ids():
    url = sounds.commons_file_url("Bang.ogg")
    http = FakeHttp({url: _commons_json(_commons_page("Bang.ogg", licence="CC BY 4.0"))})
    with pytest.raises(ValueError, match="CC0"):
        sounds.plan(["commons:Bang.ogg"], http=http, head=http.head)
    with pytest.raises(ValueError, match="no such pack"):
        sounds.plan(["pack:sonniss-gdc"], http=http, head=http.head)
    with pytest.raises(ValueError, match="kind"):
        sounds.plan(["commons:Bang.ogg=music"], http=http, head=http.head)
    with pytest.raises(ValueError, match="API key"):
        sounds.plan(["freesound:1"], http=http, head=http.head, freesound_key=None)


def test_plan_without_a_size_caps_the_download():
    http = FakeHttp({}, sizes={})
    (p,) = sounds.plan(["pack:kenney-rpg"], http=http, head=http.head)
    assert p.files[0]["max_bytes"] >= p.files[0]["bytes"] > 0


def test_download_extracts_a_pack_into_the_library(tmp_path):
    archive = _zip({"100 sfx/impactWood_heavy_001.wav": _wav_bytes(200), "100 sfx/metal_scrape.wav": _wav_bytes(300),
                    "100 sfx/readme.txt": b"CC0", "__MACOSX/._x.wav": b"junk",
                    "../evil.wav": _wav_bytes()})  # fmt: skip
    pack = sounds.PACKS["oga-metal-wood"]
    http = FakeHttp({pack["url"]: archive}, sizes={pack["url"]: len(archive)})
    (plan,) = sounds.plan(["pack:oga-metal-wood"], http=http, head=http.head)
    cache = tmp_path / ".assets-cache"
    got = sounds.download([plan], cache, http=http)
    rels = sorted(str(p.relative_to(cache)).replace("\\", "/") for p in got[plan.id])
    assert rels == ["sounds/oga-metal-wood/100 sfx/impactWood_heavy_001.wav", "sounds/oga-metal-wood/100 sfx/metal_scrape.wav"]
    assert not (tmp_path / "evil.wav").exists()
    lib = recorded.Library(cache)
    (thud,) = lib.clips_for("thud")
    assert thud["license"] == "CC0" and thud["asset"] == plan.id and thud["page"] == pack["page"]
    assert [c["path"] for c in lib.clips_for("scrape")] == ["sounds/oga-metal-wood/100 sfx/metal_scrape.wav"]
    n = len(http.calls)
    sounds.download([plan], cache, http=http)  # cached: no new request
    assert len(http.calls) == n


def test_download_verifies_sha1(tmp_path):
    data = _wav_bytes()
    url = "https://upload.wikimedia.org/wikipedia/commons/a/ab/Wood_thud.wav"
    page = _commons_page("Wood_thud.wav", size=len(data), url=url, sha1=hashlib.sha1(b"other").hexdigest())
    http = FakeHttp({sounds.commons_file_url("Wood_thud.wav"): _commons_json(page), url: data})
    (plan,) = sounds.plan(["commons:Wood_thud.wav=thud"], http=http, head=http.head)
    with pytest.raises(RuntimeError, match="sha1"):
        sounds.download([plan], tmp_path / "c", http=http)
    assert recorded.Library(tmp_path / "c").clips_for("thud") == []
    page["imageinfo"][0]["sha1"] = hashlib.sha1(data).hexdigest()
    http.routes[sounds.commons_file_url("Wood_thud.wav")] = _commons_json(page)
    (plan,) = sounds.plan(["commons:Wood_thud.wav=thud"], http=http, head=http.head)
    sounds.download([plan], tmp_path / "c", http=http)
    (clip,) = recorded.Library(tmp_path / "c").clips_for("thud")
    assert "Jane Recordist" in clip["source"] and clip["license"] == "CC0"


# --------------------------------------------------------------------------- Google Fonts static weights


CSS = b"""@font-face {
  font-family: 'Fredoka';
  font-style: normal;
  font-weight: 600;
  src: url(https://fonts.gstatic.com/s/fredoka/v14/X7nP4b87HvSqjb_WIi2yDCRwoQ_k7367_DWs89XyHw.ttf) format('truetype');
}
"""


def test_gfonts_plan_static_weight():
    css_url = gfonts.css_url("Fredoka", 600)
    ttf = "https://fonts.gstatic.com/s/fredoka/v14/X7nP4b87HvSqjb_WIi2yDCRwoQ_k7367_DWs89XyHw.ttf"
    http = FakeHttp({css_url: CSS, gfonts.metadata_url("ofl", "Fredoka"): b'name: "Fredoka"\nlicense: "OFL"\n'},
                    sizes={ttf: 98_765})  # fmt: skip
    (a,) = gfonts.plan(["font:Fredoka@600"], http=http, head=http.head)
    assert a.kind == "fonts" and a.source == "Google Fonts" and a.license == "OFL-1.1" and a.bytes == 98_765
    assert a.files[0]["url"] == ttf and a.files[0]["path"] == "fonts/Fredoka-600.ttf" and a.name == "Fredoka SemiBold"


def test_gfonts_licence_must_be_known():
    css_url = gfonts.css_url("Mystery Sans", 700)
    from urllib.error import HTTPError

    missing = HTTPError("u", 404, "Not Found", {}, None)
    http = FakeHttp({css_url: CSS.replace(b"600", b"700"), **{gfonts.metadata_url(d, "Mystery Sans"): missing
                                                            for d in gfonts.LICENCE_DIRS}})  # fmt: skip
    with pytest.raises(ValueError, match="licence"):
        gfonts.plan(["font:Mystery Sans@700"], http=http, head=http.head)
    with pytest.raises(ValueError, match="weight"):
        gfonts.plan(["font:Fredoka"], http=http, head=http.head)


def test_record_and_short_round_trip(tmp_path):
    pack = sounds.PACKS["kenney-ui-sfx"]
    http = FakeHttp({}, sizes={pack["url"]: 10})
    plans = sounds.plan(["pack:kenney-ui-sfx"], http=http, head=http.head)
    short = Short(slug="t", title="T", length_s=10)
    polyhaven.record(short, plans)
    save_short(short, tmp_path / "short.json")
    back = load_short(tmp_path / "short.json")
    assert back.assets[0].license == "CC0" and back.assets[0].kind == "sounds"
    assert Path(back.assets[0].files[0]["path"]).name.endswith(".zip")
