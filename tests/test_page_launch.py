"""Starting or reusing the workspace's page server, opening the page and confirming it checked in, the static
fallback, and CLI page SHORT / page serve."""

import http.client
import json
import os
import signal
import subprocess
import sys
import threading
import time
import types

import pagefix
import pytest

from bsa import render_status
from bsa.review import launch, server
from tests import test_cli
from tests.test_cli import CLI

home, workspace, pendulum = test_cli.home, test_cli.workspace, test_cli.pendulum  # the CLI tests' fixtures


def _post(port, token, path, body):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        conn.request("POST", path, json.dumps(body), {server.TOKEN_HEADER: token, "Content-Type": "application/json"})
        r = conn.getresponse()
        r.read()
        return r.status
    finally:
        conn.close()


@pytest.fixture
def live(tmp_path):
    """An in-thread server for a workspace, named by a hand-written SERVER_FILE as run_server would write it."""
    ws = tmp_path / "My Shorts é"
    sdir = pagefix.make_short(ws, "story")
    srv = server.make_server(ws)
    t = threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    t.start()
    pid = os.getpid()
    (ws / launch.SERVER_FILE).write_text(json.dumps({
        "pid": pid, "port": srv.port, "token": srv.token, "process_token": render_status.process_token(pid),
        "lan": False, "started": time.time()}), "utf-8")  # fmt: skip

    def hello():
        assert _post(srv.port, srv.token, "/api/01-pendulum/hello", {}) == 200

    def browser_that_checks_in(url):
        threading.Timer(0.2, hello).start()  # the tab loads a moment after the browser call returns
        return True

    yield types.SimpleNamespace(ws=ws, sdir=sdir, server=srv, port=srv.port, hello=hello,
                                browser_that_checks_in=browser_that_checks_in)  # fmt: skip
    srv.shutdown()
    srv.server_close()


def _embedded_json(page) -> str:
    text = page.read_text("utf-8")
    start = text.index("window.BSA_SNAPSHOT = ") + len("window.BSA_SNAPSHOT = ")
    return text[start : text.index(";</script>", start)]


def _kill(pid):
    if render_status.process_token(pid) is not None:
        os.kill(pid, signal.SIGTERM)  # TerminateProcess on Windows
    for _ in range(50):
        if render_status.process_token(pid) is None:
            return
        time.sleep(0.1)


def _no_browser() -> str:
    """A BROWSER command that opens nothing: webbrowser runs it with the URL and gets a failure back."""
    return "true" if sys.platform != "win32" else f"{sys.executable}"


def run(home, *args, timeout=120):
    env = {**os.environ, "BSA_HOME": str(home), "BROWSER": _no_browser()}
    env.pop("ELEVENLABS_API_KEY", None)
    return subprocess.run([sys.executable, str(CLI), *map(str, args)], check=False, capture_output=True, text=True,
                          env=env, cwd=str(home), timeout=timeout)  # fmt: skip


def ok(proc):
    assert proc.returncode == 0, proc.stdout + proc.stderr
    return json.loads(proc.stdout)


# ---------------------------------------------------------------------------------------------------- unit


def test_constants_and_url():
    assert launch.SERVER_FILE == ".page-server.json"
    info = {"port": 8123, "token": "tok-_9"}
    assert launch.page_url(info, "01-pendulum") == "http://127.0.0.1:8123/s/01-pendulum?t=tok-_9"
    assert launch.page_url(info, "02 my é", "storyboard", host="localhost") == (
        "http://localhost:8123/s/02%20my%20%C3%A9?t=tok-_9#storyboard"
    )


def test_server_info_reads_a_live_server(live):
    info = launch.server_info(live.ws)
    assert info and info["port"] == live.port and info["token"] == live.server.token and info["pid"] == os.getpid()
    assert launch.ensure_server(live.ws, spawn=lambda *a: pytest.fail("a valid server must be reused")) == info


def test_server_info_refuses_a_port_answering_another_token(live):
    path = live.ws / launch.SERVER_FILE
    data = json.loads(path.read_text("utf-8"))
    path.write_text(json.dumps({**data, "token": "someone-else"}), "utf-8")  # the port is another program's now
    assert launch.server_info(live.ws) is None


def test_server_info_refuses_a_reused_pid(live):
    path = live.ws / launch.SERVER_FILE
    data = json.loads(path.read_text("utf-8"))
    path.write_text(json.dumps({**data, "process_token": "started-at-another-time"}), "utf-8")
    assert launch.server_info(live.ws) is None


@pytest.mark.parametrize("raw", ["", "not json", "[]", '{"pid": "1", "port": 9, "token": "x"}'])
def test_server_info_of_a_damaged_file_is_none(tmp_path, raw):
    (tmp_path / launch.SERVER_FILE).write_text(raw, "utf-8")
    assert launch.server_info(tmp_path) is None
    assert launch.server_info(tmp_path / "missing") is None


def test_open_confirms_check_in(live):
    out = launch.open_page(live.ws, live.sdir, browser=live.browser_that_checks_in, wait_s=3)
    assert out["opened"] is True and out["already_open"] is False and out["mode"] == "server"
    assert out["url"].startswith(f"http://127.0.0.1:{live.port}/s/01-pendulum?t=")
    assert out["stage"] is None


def test_no_check_in_says_false(live):
    out = launch.open_page(live.ws, live.sdir, browser=lambda url: True, wait_s=0.5)
    assert out["opened"] is False and out["url"] and out["already_open"] is False


def test_an_old_check_in_does_not_confirm_a_new_open(live):
    live.hello()
    out = launch.open_page(live.ws, live.sdir, force=True, browser=lambda url: True, wait_s=0.5)
    assert out["opened"] is False and out["already_open"] is False


def test_already_open_tab_is_left_alone_and_switched(live):
    live.hello()
    calls = []
    out = launch.open_page(live.ws, live.sdir, stage="storyboard", browser=calls.append, wait_s=0.5)
    assert calls == [] and out["already_open"] is True and live.server.hints["01-pendulum"] == "storyboard"
    assert out["opened"] is True and out["url"].endswith("#storyboard") and out["stage"] == "storyboard"
    assert launch.open_page(live.ws, live.sdir, force=True, browser=calls.append, wait_s=0.2) and len(calls) == 1


def test_a_check_in_older_than_30_s_is_not_already_open(live):
    live.hello()
    live.server.seen["01-pendulum"] -= 31
    calls = []
    out = launch.open_page(live.ws, live.sdir, browser=calls.append, wait_s=0.2)
    assert len(calls) == 1 and out["already_open"] is False


def test_unknown_stage_is_refused(live):
    with pytest.raises(ValueError, match="stage"):
        launch.open_page(live.ws, live.sdir, stage="nope", browser=lambda u: True, wait_s=0.1)


def test_stale_server_file_is_replaced(tmp_path):  # Review Focus 4
    ws = tmp_path / "My Shorts é"
    pagefix.make_short(ws, "story")
    (ws / ".page-server.json").write_text(json.dumps({"pid": 999999, "port": 9, "token": "x",
                                                      "process_token": "gone"}))  # fmt: skip
    assert launch.server_info(ws) is None
    spawned = []
    with pytest.raises(launch.ServerUnavailable):
        launch.ensure_server(ws, spawn=lambda ws, lan: spawned.append(ws) or 0, timeout_s=0.3)
    assert spawned == [ws]


def test_ensure_waits_for_the_spawned_server(live):
    path = live.ws / launch.SERVER_FILE
    good = path.read_text("utf-8")
    path.unlink()
    spawned = []

    def spawn(ws, lan):
        spawned.append(lan)
        threading.Timer(0.3, lambda: path.write_text(good, "utf-8")).start()  # the server writes it once bound

    info = launch.ensure_server(live.ws, lan=True, spawn=spawn, timeout_s=5)
    assert spawned == [True] and info["port"] == live.port


def test_run_server_writes_the_file_after_binding_and_removes_it(tmp_path, monkeypatch):
    ws = tmp_path / "ws"
    pagefix.make_short(ws, "story")
    seen = {}

    def serve_then_stop(srv, tick_s=60):  # valid while serving: bound, and health answers
        t = threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
        t.start()
        try:
            seen["info"] = launch.server_info(ws)
        finally:
            srv.shutdown()
            srv.server_close()

    monkeypatch.setattr(launch.server, "serve", serve_then_stop)
    launch.run_server(ws, False)
    assert seen["info"]["pid"] == os.getpid() and seen["info"]["lan"] is False and seen["info"]["started"]
    assert not (ws / launch.SERVER_FILE).exists()


def test_run_server_leaves_a_newer_servers_file(tmp_path, monkeypatch):
    ws = tmp_path / "ws"
    pagefix.make_short(ws, "story")
    other = {"pid": 4242, "port": 1, "token": "t", "process_token": "p"}

    def replaced(srv, tick_s=60):
        srv.server_close()
        (ws / launch.SERVER_FILE).write_text(json.dumps(other), "utf-8")

    monkeypatch.setattr(launch.server, "serve", replaced)
    launch.run_server(ws, False)
    assert json.loads((ws / launch.SERVER_FILE).read_text("utf-8")) == other


def test_fallback_static_page(tmp_path, monkeypatch):
    ws = tmp_path / "ws"
    sdir = pagefix.make_short(ws, "story")
    build = launch.snapshot.build
    monkeypatch.setattr(launch.snapshot, "build", lambda d: {**build(d), "x": "</script><b>"})
    opened = []

    def boom(*a, **k):
        raise launch.ServerUnavailable("port refused")

    out = launch.open_page(ws, sdir, browser=opened.append, ensure=boom)
    page = sdir / "review" / "short.html"
    assert out == {**out, "opened": False, "mode": "static", "already_open": False}
    assert out["url"] == page.as_uri() and opened == [page.as_uri()]
    assert "</script>" not in _embedded_json(page) and "<\\/script><b>" in _embedded_json(page)
    assert json.loads(_embedded_json(page).replace("<\\/", "</"))["x"] == "</script><b>"
    assert 'data-mode="static"' in page.read_text("utf-8")


# ---------------------------------------------------------------------------------------------------- CLI


def test_cli_page_starts_a_detached_server_that_outlives_the_call(home, pendulum):
    out = ok(run(home, "page", "pendulum", "--stage", "story"))  # BROWSER is a no-op in run()
    info = launch.server_info(pendulum.parent)
    try:
        assert info and out["url"].startswith(f"http://127.0.0.1:{info['port']}/s/")
        assert out["mode"] == "server" and out["opened"] is False and out["stage"] == "story"
        assert out["url"].endswith("#story")
        doctor = json.loads((pendulum / "review" / "doctor.json").read_text("utf-8"))
        assert {c["id"]: c["level"] for c in doctor["checks"]}["server"] == "ok"
        assert all(c["level"] != "ok" for c in out["health"]) and "server" not in [c["id"] for c in out["health"]]
        again = ok(run(home, "page", "pendulum"))
        assert again["url"].split("?")[0] == out["url"].split("?")[0].split("#")[0]
        assert launch.server_info(pendulum.parent)["pid"] == info["pid"]  # reused, not a second server
    finally:
        if info:
            _kill(info["pid"])


def test_cli_page_unknown_stage_is_one_line(home, pendulum):
    proc = run(home, "page", "pendulum", "--stage", "nope")
    assert proc.returncode == 1 and "stage" in proc.stderr and "Traceback" not in proc.stderr
    assert not (pendulum.parent / launch.SERVER_FILE).exists()  # refused before any server starts
