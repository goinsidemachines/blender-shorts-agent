"""Sound sources that fail are named with a retry hint (never silently left out), and a pack already in the cache,
in this layout or an older one (`.assets-cache/sfx/<folder>/`), is reused instead of downloaded again."""

import json

import pytest

import bsa_cli
from bsa.assets import polyhaven, sounds
from tests.test_sound_assets import FakeHttp, _commons_json, _wav_bytes, _zip


def test_search_names_the_skipped_source_and_suggests_a_retry():
    q = sounds.commons_search_url("click")
    out = sounds.search("click", http=FakeHttp({q: OSError("getaddrinfo failed")}), freesound_key=None)
    (skip,) = out["skipped"]
    assert skip["source"] == "Wikimedia Commons" and "getaddrinfo failed" in skip["error"]
    assert "again" in skip["retry"]
    note = next(n for n in out["notes"] if "Wikimedia Commons" in n)
    assert "skipped" in note and "again" in note
    assert any(r["id"].startswith("pack:") for r in out["results"])


def test_search_with_every_source_answering_skips_nothing():
    q = sounds.commons_search_url("click")
    out = sounds.search("click", http=FakeHttp({q: _commons_json()}), freesound_key=None)
    assert out["skipped"] == []


def test_cli_plan_lists_a_source_that_failed_instead_of_dropping_it(tmp_path, monkeypatch, capsys):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("BSA_HOME", str(home))
    monkeypatch.delenv("FREESOUND_API_KEY", raising=False)
    assert bsa_cli.main(["settings", "set", "workspace", str(tmp_path / "ws")]) == 0
    capsys.readouterr()
    assert bsa_cli.main(["new-short", "Clicks"]) == 0
    sdir = json.loads(capsys.readouterr().out)["short"]
    commons = sounds.commons_file_url("Click.ogg")

    def fake_open(url, headers=None):
        if url == commons:
            raise OSError("getaddrinfo failed")
        raise AssertionError(url)

    monkeypatch.setattr(sounds, "_urlopen", fake_open)
    monkeypatch.setattr(sounds, "content_length", lambda url: 1_000_000)
    assert bsa_cli.main(["assets", "plan", sdir, "pack:kenney-rpg", "commons:Click.ogg"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert [a["id"] for a in out["assets"]] == ["sfx-kenney-rpg"]
    (skip,) = out["skipped"]
    assert skip["id"] == "commons:Click.ogg" and skip["source"] == "Wikimedia Commons" and "again" in skip["retry"]
    assert "commons:Click.ogg" in out["table"] and "not in this plan" in out["table"]
    # only failures: no plan at all, and the error says which source and to retry
    assert bsa_cli.main(["assets", "plan", sdir, "commons:Click.ogg"]) != 0
    err = capsys.readouterr()
    assert "Wikimedia Commons" in err.out + err.err and "again" in err.out + err.err


def test_plan_reuses_a_pack_already_in_the_cache(tmp_path):
    archive = _zip({"sfx/impactWood_heavy_001.wav": _wav_bytes(200)})
    pack = sounds.PACKS["oga-metal-wood"]
    http = FakeHttp({pack["url"]: archive}, sizes={pack["url"]: len(archive)})
    cache = tmp_path / ".assets-cache"
    (plan,) = sounds.plan(["pack:oga-metal-wood"], http=http, head=http.head, cache_dir=cache)
    assert not plan.files[0].get("cached")
    sounds.download([plan], cache, http=http)
    (again,) = sounds.plan(["pack:oga-metal-wood"], http=http, head=http.head, cache_dir=cache)
    assert again.files[0]["cached"] is True
    assert "already in the cache" in polyhaven.format_plan([again])
    assert "Total: 0.0 MB" in polyhaven.format_plan([again])


@pytest.mark.parametrize("known_size", [True, False])
def test_an_older_cache_folder_is_found_and_migrated(tmp_path, known_size):
    """Earlier versions kept a pack as `.assets-cache/sfx/<folder>/pack.zip`: it is found (by its exact size, or
    by its folder name when the size is not known) and copied into the current layout, so nothing is downloaded."""
    archive = _zip({"files/metal_hit_01.ogg": b"OggS" + b"x" * 500, "files/hammer_01.ogg": b"OggS" + b"y" * 300})
    pack = sounds.PACKS["oga-metal-wood"]
    cache = tmp_path / ".assets-cache"
    old = cache / "sfx" / "rubberduck-100-wood-metal"
    old.mkdir(parents=True)
    (old / "pack.zip").write_bytes(archive)
    (cache / "sfx" / "unrelated").mkdir()
    (cache / "sfx" / "unrelated" / "pack.zip").write_bytes(_zip({"a.ogg": b"OggS"}))
    http = FakeHttp({}, sizes={pack["url"]: len(archive)} if known_size else {})
    (plan,) = sounds.plan(["pack:oga-metal-wood"], http=http, head=http.head, cache_dir=cache)
    f = plan.files[0]
    assert f["cached"] is True and "sfx/rubberduck-100-wood-metal/pack.zip" in f["reused_from"].replace("\\", "/")
    target = polyhaven._target(cache, f["path"])
    assert target.read_bytes() == archive and (old / "pack.zip").is_file()  # copied, the old folder left alone
    got = sounds.download([plan], cache, http=FakeHttp({}))  # no request at all
    names = sorted(p.name for p in got[plan.id])
    assert names == ["hammer_01.ogg", "metal_hit_01.ogg"]


def test_an_old_zip_of_another_size_is_not_taken_for_the_pack(tmp_path):
    pack = sounds.PACKS["oga-metal-wood"]
    cache = tmp_path / ".assets-cache"
    old = cache / "sfx" / "rubberduck-100-wood-metal"
    old.mkdir(parents=True)
    (old / "pack.zip").write_bytes(_zip({"a.ogg": b"OggS"}))
    http = FakeHttp({}, sizes={pack["url"]: 2_000_123})
    (plan,) = sounds.plan(["pack:oga-metal-wood"], http=http, head=http.head, cache_dir=cache)
    assert not plan.files[0].get("cached")


def test_old_layout_packs_are_indexed_once_without_any_request(tmp_path):
    """An earlier version's `.assets-cache/sfx/<folder>/` (files/, wav48/, pack.zip) is found with no network at all:
    the pack is copied into the current layout, unpacked and added to the library, once."""
    archive = _zip({"files/hammer_01.ogg": b"OggS" + b"y" * 300, "files/door_open_01.ogg": b"OggS" + b"z" * 200})
    cache = tmp_path / ".assets-cache"
    old = cache / "sfx" / "rubberduck-100-wood-metal"
    (old / "files").mkdir(parents=True)
    (old / "wav48").mkdir()
    (old / "files" / "hammer_01.ogg").write_bytes(b"OggS")
    (old / "pack.zip").write_bytes(archive)
    assert sounds.adopt_legacy(cache) == ["sfx-oga-metal-wood"]
    from bsa.audio import recorded

    lib = recorded.Library(cache)
    hit = lib.find("hammer_01")
    assert hit["asset"] == "sfx-oga-metal-wood" and "impact" in hit["kinds"] and hit["license"] == "CC0"
    assert (old / "pack.zip").is_file()  # the old folder is left as it was
    assert sounds.adopt_legacy(cache) == []  # one time: already indexed
    # planning the pack afterwards needs no download
    http = FakeHttp({}, sizes={})
    (plan,) = sounds.plan(["pack:oga-metal-wood"], http=http, head=http.head, cache_dir=cache)
    assert plan.files[0]["cached"] is True


def test_cli_sound_library_adopts_old_packs(tmp_path, monkeypatch, capsys):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("BSA_HOME", str(home))
    assert bsa_cli.main(["settings", "set", "workspace", str(tmp_path / "ws")]) == 0
    capsys.readouterr()
    assert bsa_cli.main(["new-short", "Clicks"]) == 0
    from pathlib import Path

    sdir = Path(json.loads(capsys.readouterr().out)["short"])
    cache = polyhaven.cache_dir(sdir)
    old = cache / "sfx" / "rubberduck-100-wood-metal"
    old.mkdir(parents=True)
    (old / "pack.zip").write_bytes(_zip({"files/hammer_01.wav": _wav_bytes(200)}))
    lib = bsa_cli._sound_library(sdir)
    assert lib.clips_for("impact")


def test_designed_ui_packs_are_flagged_in_the_plan_and_the_library(tmp_path):
    cache = tmp_path / ".assets-cache"
    pack = sounds.PACKS["kenney-ui-sfx"]
    archive = _zip({"Audio/click5.wav": _wav_bytes(1000)})
    http = FakeHttp({pack["url"]: archive}, sizes={pack["url"]: len(archive)})
    (plan,) = sounds.plan(["pack:kenney-ui-sfx"], http=http, head=http.head, cache_dir=cache)
    table = polyhaven.format_plan([plan])
    assert "designed" in table and "natural-only" in table
    sounds.download([plan], cache, http=http)
    from bsa.audio import recorded

    (clip,) = recorded.Library(cache).clips
    assert clip["designed"] is True and 0 <= clip["flatness"] < 0.05  # a pure tone
    (meta,) = [r for r in sounds.search("click", http=FakeHttp({sounds.commons_search_url("click"): _commons_json()}))
               ["results"] if r["id"] == "pack:kenney-ui-sfx"]  # fmt: skip
    assert meta["designed"]


def test_a_network_failure_reads_as_a_plain_note():
    import socket
    import urllib.error

    q = sounds.commons_search_url("click")
    err = urllib.error.URLError(socket.gaierror(11001, "getaddrinfo failed"))
    out = sounds.search("click", http=FakeHttp({q: err}), freesound_key=None)
    (skip,) = out["skipped"]
    assert skip["error"] == "can't reach commons.wikimedia.org (network or DNS)"
    note = next(n for n in out["notes"] if "commons" in n)
    assert "can't reach commons.wikimedia.org (network or DNS); searched the other sources only" in note
    assert "urlopen" not in note and "Errno" not in note
    plan_skip = sounds.skipped("Wikimedia Commons", err, "assets plan", "commons:Click.ogg")
    assert plan_skip["error"] == "can't reach commons.wikimedia.org (network or DNS)"
