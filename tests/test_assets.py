import hashlib
import io
import json
from pathlib import Path

import pytest

from bsa.assets import polyhaven
from bsa.short import Asset, Short, load_short, save_short

FIX = Path(__file__).parent / "fixtures" / "polyhaven"


def fx(name):
    return (FIX / name).read_bytes()


class FakeHttp:
    """Maps url -> bytes; records every request. Unknown URLs fail the test."""

    def __init__(self, routes):
        self.routes = dict(routes)
        self.calls = []

    def __call__(self, url):
        self.calls.append(url)
        if url not in self.routes:
            raise AssertionError(f"unexpected request: {url}")
        return io.BytesIO(self.routes[url])


def catalogue_http():
    return FakeHttp(
        {
            "https://api.polyhaven.com/assets?t=textures": fx("assets_textures.json"),
            "https://api.polyhaven.com/files/rock_boulder_dry": fx("files_rock_boulder_dry.json"),
            "https://api.polyhaven.com/files/kloofendal_43d_clear_puresky": fx("files_kloofendal_43d_clear_puresky.json"),
            "https://api.polyhaven.com/files/wooden_crate_01": fx("files_wooden_crate_01.json"),
            "https://api.polyhaven.com/assets?t=hdris": json.dumps(
                {"kloofendal_43d_clear_puresky": {"name": "Kloofendal 43d Clear (Pure Sky)", "tags": ["sky"], "categories": ["skies"]}}
            ).encode(),
            "https://api.polyhaven.com/assets?t=models": json.dumps(
                {"wooden_crate_01": {"name": "Wooden Crate 01", "tags": ["box"], "categories": ["containers"]}}
            ).encode(),
        }
    )


def test_search_filters_by_name_tags_categories():
    http = catalogue_http()
    by_name = polyhaven.search("textures", "asphalt", http=http)
    assert [r["id"] for r in by_name] == ["aerial_asphalt_01"]
    assert by_name[0]["name"] == "Aerial Asphalt 01"
    assert [r["id"] for r in polyhaven.search("textures", "boulder", http=http)] == ["rock_boulder_dry"]  # tag
    assert [r["id"] for r in polyhaven.search("textures", "sand", http=http)] == ["aerial_beach_01"]  # category
    assert polyhaven.search("textures", "rock", limit=1, http=http)[0]["id"] == "rock_boulder_dry"
    with pytest.raises(ValueError):
        polyhaven.search("sounds", "x", http=http)


def test_plan_sizes_and_license():
    http = catalogue_http()
    (tex,) = polyhaven.plan(["rock_boulder_dry"], resolution="2k", http=http)
    assert tex.license == "CC0" and tex.source == "Poly Haven" and tex.kind == "textures"
    assert tex.name == "Rock Boulder Dry"
    assert sorted(f["role"] for f in tex.files) == ["diffuse", "disp", "nor_gl", "rough"]
    assert all(f["md5"] and f["bytes"] > 0 and f["url"].startswith("https://") for f in tex.files)
    assert tex.bytes == sum(f["bytes"] for f in tex.files)
    assert all("2k" in f["url"] for f in tex.files)

    hdri, model = polyhaven.plan(["kloofendal_43d_clear_puresky", "wooden_crate_01"], http=http)
    assert hdri.kind == "hdris" and [f["role"] for f in hdri.files] == ["hdri"]
    assert hdri.files[0]["url"].endswith(".hdr") and hdri.bytes == 4624289
    assert model.kind == "models"
    roles = [f["role"] for f in model.files]
    assert roles[0] == "blend" and roles.count("texture") == 4
    assert model.bytes == 263309 + 6069816 + 2728494 + 122318 + 2453665
    assert any(f["path"] == "wooden_crate_01/textures/wooden_crate_01_diff_2k.jpg" for f in model.files)

    with pytest.raises(ValueError, match="4k"):
        polyhaven.plan(["wooden_crate_01"], resolution="4k", http=http)


def test_format_plan_table():
    plans = polyhaven.plan(["rock_boulder_dry", "kloofendal_43d_clear_puresky"], http=catalogue_http())
    text = polyhaven.format_plan(plans)
    assert text.count("CC0") == 2
    assert "| Rock Boulder Dry | Poly Haven | CC0 |" in text
    assert "Total" in text and "MB" in text
    total_mb = sum(p.bytes for p in plans) / 1_000_000
    assert f"{total_mb:.1f} MB" in text


def _small_plan(payloads):
    files = [
        {"url": f"https://dl.example/{name}", "bytes": len(data), "role": "diffuse",
         "md5": hashlib.md5(data).hexdigest(), "path": f"asset_a/{name}"}
        for name, data in payloads.items()
    ]
    from bsa.short import Asset

    return [Asset("asset_a", "Asset A", "textures", "Poly Haven", "CC0", files, sum(f["bytes"] for f in files))]


def test_download_uses_cache(tmp_path):
    payloads = {"a.jpg": b"hello" * 10, "b.jpg": b"world" * 7}
    plans = _small_plan(payloads)
    http = FakeHttp({f"https://dl.example/{n}": d for n, d in payloads.items()})
    out = polyhaven.download(plans, tmp_path / ".assets-cache", http=http)
    assert len(http.calls) == 2
    paths = out["asset_a"]
    assert [p.read_bytes() for p in paths] == list(payloads.values())
    assert not list((tmp_path / ".assets-cache").rglob("*.part"))

    http.calls.clear()
    again = polyhaven.download(plans, tmp_path / ".assets-cache", http=http)
    assert http.calls == [] and again == out


def test_size_mismatch_rejected(tmp_path):
    plans = _small_plan({"a.jpg": b"x" * 20})
    http = FakeHttp({"https://dl.example/a.jpg": b"x" * 19})
    with pytest.raises(RuntimeError, match="size"):
        polyhaven.download(plans, tmp_path, http=http)
    assert not list(tmp_path.rglob("a.jpg*"))


def test_md5_mismatch_rejected(tmp_path):
    plans = _small_plan({"a.jpg": b"x" * 20})
    http = FakeHttp({"https://dl.example/a.jpg": b"y" * 20})
    with pytest.raises(RuntimeError, match="md5"):
        polyhaven.download(plans, tmp_path, http=http)
    assert not list(tmp_path.rglob("a.jpg*"))


@pytest.mark.parametrize("bad", ["../escape.jpg", r"..\x", "C:x", "/abs", "a/../../x", r"a\b", "a//b", "./a", ""])
def test_unsafe_path_rejected(tmp_path, bad):
    plans = _small_plan({"a.jpg": b"x"})
    plans[0].files[0]["path"] = bad
    with pytest.raises(ValueError, match="unsafe"):
        polyhaven.download(plans, tmp_path / "cache", http=FakeHttp({}))
    assert not (tmp_path / "escape.jpg").exists() and not (tmp_path / "x").exists()


def test_non_https_url_rejected(tmp_path):
    plans = _small_plan({"a.jpg": b"x"})
    for url in ("http://dl.example/a.jpg", "file:///etc/passwd", "ftp://dl.example/a.jpg"):
        plans[0].files[0]["url"] = url
        http = FakeHttp({})
        with pytest.raises(ValueError, match="https"):
            polyhaven.download(plans, tmp_path, http=http)
        assert http.calls == []


def test_oversized_body_stops_early(tmp_path):
    plans = _small_plan({"a.jpg": b"x" * 20})

    class Endless:
        read_total = 0

        def read(self, n=-1):
            n = 1 << 20 if n is None or n < 0 else n
            self.read_total += n
            return b"x" * n

        def close(self):
            pass

    body = Endless()
    with pytest.raises(RuntimeError, match="size"):
        polyhaven.download(plans, tmp_path, http=lambda url: body)
    assert body.read_total <= 21
    assert not list(tmp_path.rglob("a.jpg*"))


def test_plan_uses_given_names():
    http = catalogue_http()
    (a,) = polyhaven.plan(["kloofendal_43d_clear_puresky"], http=http, names={"kloofendal_43d_clear_puresky": "Kloofendal 43d Clear (Pure Sky)"})
    assert a.name == "Kloofendal 43d Clear (Pure Sky)"
    (b,) = polyhaven.plan(["rock_boulder_dry"], http=http, names={"other": "X"})
    assert b.name == "Rock Boulder Dry"


def test_record_appends_assets_for_credits(tmp_path):
    plans = polyhaven.plan(["rock_boulder_dry"], http=catalogue_http())
    short = Short(slug="s", title="T", length_s=30)
    polyhaven.record(short, plans)
    polyhaven.record(short, plans)  # idempotent
    assert [a.id for a in short.assets] == ["rock_boulder_dry"]
    save_short(short, tmp_path / "short.json")
    assert load_short(tmp_path / "short.json").assets[0].license == "CC0"


def test_cache_dir_and_asset_paths_follow_the_short(tmp_path):
    """Downloads land in <workspace>/.assets-cache/<file.path>, where build.py finds them from the short's folder."""
    short_dir = tmp_path / "ws" / "03-damper"
    entry = {"url": "https://dl.polyhaven.org/x.jpg", "bytes": 3, "role": "diffuse", "path": "rock/rock_diff_2k.jpg"}
    asset = Asset("rock", "Rock", "textures", "Poly Haven", "CC0", files=[entry], bytes=3)
    assert polyhaven.cache_dir(short_dir) == tmp_path / "ws" / ".assets-cache"
    assert polyhaven.asset_files(short_dir, asset) == {"diffuse": tmp_path / "ws" / ".assets-cache" / "rock" / "rock_diff_2k.jpg"}
    assert polyhaven.asset_files(short_dir, {"id": "rock", "files": [entry]}) == polyhaven.asset_files(short_dir, asset)
