"""Phone access (opt-in): the page.lan setting, binding the local network, the LAN Host, the phone link in the
state reply, restarting a server whose lan differs, and the footer credit being on the page only."""

import http.client
import json
import os
import threading
import time

import pagefix
import pytest

from bsa import render_status
from bsa.review import launch, server
from tests.test_cli import ROOT, home, ok, run  # noqa: F401 - home is a fixture

LAN = "192.168.1.20"


def _get(srv, path, host):
    conn = http.client.HTTPConnection("127.0.0.1", srv.port, timeout=5)
    try:
        conn.request("GET", path, headers={"Host": host, server.TOKEN_HEADER: srv.token})
        r = conn.getresponse()
        return r.status, r.read()
    finally:
        conn.close()


def _serving(srv):
    t = threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    t.start()
    return t


def test_lan_off_binds_loopback_only(tmp_path):
    s = server.make_server(tmp_path)
    try:
        assert s.server_address[0] == "127.0.0.1" and s.lan is False and s.lan_ip is None
    finally:
        s.server_close()


def test_lan_picks_every_interface_without_binding_here(monkeypatch):
    assert server._LAN_BIND == "0.0.0.0"
    monkeypatch.delenv(server.BIND_ENV, raising=False)
    assert server.bind_address(True) == "0.0.0.0" and server.bind_address(False) == "127.0.0.1"
    monkeypatch.setenv(server.BIND_ENV, "127.0.0.1")  # what every test (and the servers they spawn) uses
    assert server.bind_address(True) == "127.0.0.1" and server.bind_address(False) == "127.0.0.1"


def test_lan_ip_sends_nothing_and_is_an_address_or_none():
    ip = launch.lan_ip()
    assert ip is None or (isinstance(ip, str) and ip.count(".") == 3)


def test_lan_on_accepts_lan_host(tmp_path, monkeypatch):
    monkeypatch.setattr(launch, "lan_ip", lambda: LAN)
    s = server.make_server(tmp_path, lan=True)
    _serving(s)
    try:
        assert s.server_address[0] == "127.0.0.1" and s.lan is True and s.lan_ip == LAN  # BSA_PAGE_BIND
        assert _get(s, "/api/health", f"{LAN}:{s.port}")[0] == 200
        assert _get(s, "/api/health", f"192.168.1.21:{s.port}")[0] == 403
    finally:
        s.shutdown()
        s.server_close()


def test_lan_off_refuses_the_lan_host(tmp_path, monkeypatch):
    monkeypatch.setattr(launch, "lan_ip", lambda: LAN)
    s = server.make_server(tmp_path)
    _serving(s)
    try:
        assert _get(s, "/api/health", f"{LAN}:{s.port}")[0] == 403
    finally:
        s.shutdown()
        s.server_close()


@pytest.mark.parametrize("lan", [False, True])
def test_phone_link_only_with_lan(tmp_path, monkeypatch, lan):
    monkeypatch.setattr(launch, "lan_ip", lambda: LAN)
    sdir = pagefix.make_short(tmp_path / "My Shorts é", "story")
    s = server.make_server(sdir.parent, lan=lan)
    _serving(s)
    try:
        status, body = _get(s, f"/api/{sdir.name}/state", f"127.0.0.1:{s.port}")
        assert status == 200
        phone = json.loads(body)["phone"]
        if lan:
            assert phone == {"url": f"http://{LAN}:{s.port}/s/{sdir.name}?t={s.token}"}
        else:
            assert phone is None
    finally:
        s.shutdown()
        s.server_close()


def test_phone_url_from_server_info(monkeypatch):
    monkeypatch.setattr(launch, "lan_ip", lambda: LAN)
    info = {"port": 8123, "token": "tok", "lan": True}
    assert launch.phone_url(info, "01 é") == f"http://{LAN}:8123/s/01%20%C3%A9?t=tok"
    assert launch.phone_url({**info, "lan": False}, "x") is None
    monkeypatch.setattr(launch, "lan_ip", lambda: None)
    assert launch.phone_url(info, "x") is None


def test_page_lan_setting_is_boolean(home):  # noqa: F811
    assert ok(run(home, "settings", "set", "page.lan", "true"))["settings"]["page"]["lan"] is True
    assert ok(run(home, "settings", "set", "page.lan", "false"))["settings"]["page"]["lan"] is False
    bad = run(home, "settings", "set", "page.lan", "maybe")
    assert bad.returncode == 1 and "page.lan" in bad.stderr


def test_page_lan_defaults_off(home, monkeypatch):  # noqa: F811
    from bsa import settings

    monkeypatch.setenv("BSA_HOME", str(home))
    assert settings.load_settings()["page"] == {"lan": False}


def test_credit_only_on_the_page():
    hits = [p for p in (ROOT / "bsa").rglob("*") if p.is_file() and p.suffix in (".py", ".js", ".html", ".css")
            and "See what others made" in p.read_text("utf-8", errors="ignore")]  # fmt: skip
    assert hits == [ROOT / "bsa" / "review" / "templates" / "page" / "page.html"]


# ------------------------------------------------------------------ a running server whose lan differs


@pytest.fixture
def live(tmp_path):
    ws = tmp_path / "My Shorts é"
    pagefix.make_short(ws, "story")
    srv = server.make_server(ws)
    _serving(srv)
    pid = os.getpid()
    (ws / launch.SERVER_FILE).write_text(json.dumps({
        "pid": pid, "port": srv.port, "token": srv.token, "process_token": render_status.process_token(pid),
        "lan": False, "started": time.time()}), "utf-8")  # fmt: skip
    yield ws, srv
    srv.shutdown()
    srv.server_close()


def test_a_server_whose_lan_differs_is_restarted(live):
    ws, srv = live
    path = ws / launch.SERVER_FILE
    old = json.loads(path.read_text("utf-8"))
    stopped, spawned = [], []

    def stop(info):  # as the real stop: the process ends and its file names a dead process
        stopped.append(info["port"])
        path.unlink()

    def spawn(ws_, lan):
        spawned.append(lan)
        threading.Timer(0.2, lambda: path.write_text(json.dumps({**old, "lan": True}), "utf-8")).start()

    info = launch.ensure_server(ws, lan=True, spawn=spawn, stop=stop, timeout_s=5)
    assert stopped == [srv.port] and spawned == [True] and info["lan"] is True


def test_a_server_whose_lan_matches_is_reused(live):
    ws, _srv = live
    info = launch.ensure_server(ws, lan=False, spawn=lambda *a: pytest.fail("reuse it"),
                                stop=lambda *a: pytest.fail("keep it"))  # fmt: skip
    assert info["lan"] is False


def test_a_server_that_wont_stop_is_unavailable(live):
    ws, _srv = live
    with pytest.raises(launch.ServerUnavailable, match="stop"):
        launch.ensure_server(ws, lan=True, spawn=lambda *a: pytest.fail("never two servers"),
                             stop=lambda info: None, timeout_s=0.5)  # fmt: skip
