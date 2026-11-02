"""Starting or reusing the workspace's page server, opening the page and confirming it checked in, the static
fallback, and CLI page SHORT / page serve."""

import contextlib
import http.client
import json
import os
import signal
import subprocess
import sys
import threading
import time
import types
from pathlib import Path

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


def _no_browser(home) -> str:
    """A BROWSER command that opens nothing and succeeds, so webbrowser never falls back to the real default
    browser (on Windows it would: a failing command is followed by os.startfile)."""
    if sys.platform != "win32":
        return "true"
    cmd = Path(home) / "no-browser.cmd"
    cmd.write_bytes(b"@exit /b 0\r\n")
    return str(cmd)


def run(home, *args, timeout=120):
    env = {**os.environ, "BSA_HOME": str(home), "BROWSER": _no_browser(home)}
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


def test_a_polling_tab_does_not_confirm_a_forced_open(live):
    live.hello()
    stop = threading.Event()

    def poll():  # the open tab polls state every 0.1 s; the browser call opens nothing
        while not stop.wait(0.1):
            conn = http.client.HTTPConnection("127.0.0.1", live.port, timeout=5)
            conn.request("GET", "/api/01-pendulum/state", headers={server.TOKEN_HEADER: live.server.token})
            conn.getresponse().read()
            conn.close()

    t = threading.Thread(target=poll, daemon=True)
    t.start()
    try:
        out = launch.open_page(live.ws, live.sdir, force=True, browser=lambda url: True, wait_s=0.8)
    finally:
        stop.set()
        t.join(5)
    assert out["opened"] is False and out["already_open"] is False
    assert live.server.seen["01-pendulum"] > live.server.hello_at["01-pendulum"]  # it did poll during the wait


def test_presence_polls_never_overrun_wait_s(live, monkeypatch):
    timeouts = []
    real = launch._request
    monkeypatch.setattr(launch, "_request", lambda *a, **k: timeouts.append(k.get("timeout_s")) or real(*a, **k))
    t0 = time.monotonic()
    launch.open_page(live.ws, live.sdir, force=True, browser=lambda url: True, wait_s=0.3)
    assert time.monotonic() - t0 < 1.5 and all(t is not None and t <= 2.0 for t in timeouts[1:])
    assert min(timeouts[2:]) < 0.3


def test_ensure_fails_at_once_when_the_server_dies(tmp_path):
    ws = tmp_path / "ws"
    pagefix.make_short(ws, "story")
    t0 = time.monotonic()
    with pytest.raises(launch.ServerUnavailable, match="stopped"):
        launch.ensure_server(ws, spawn=lambda ws, lan: subprocess.Popen([sys.executable, "-c", "pass"]), timeout_s=20)
    assert time.monotonic() - t0 < 10


def test_ensure_kills_a_server_that_never_answers(tmp_path):
    ws = tmp_path / "ws"
    pagefix.make_short(ws, "story")
    procs = []

    def spawn(ws, lan):
        procs.append(subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"]))
        return procs[-1]

    with pytest.raises(launch.ServerUnavailable, match="did not answer"):
        launch.ensure_server(ws, spawn=spawn, timeout_s=0.5)
    assert procs[0].poll() is not None


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
    try:
        out = ok(run(home, "page", "pendulum", "--stage", "story"))  # BROWSER is a no-op in run()
        info = launch.server_info(pendulum.parent)
        assert info and out["url"].startswith(f"http://127.0.0.1:{info['port']}/s/")
        assert out["mode"] == "server" and out["opened"] is False and out["stage"] == "story"
        assert out["url"].endswith("#story")
        doctor = json.loads((pendulum / "review" / "doctor.json").read_text("utf-8"))
        assert {c["id"]: c["level"] for c in doctor["checks"]}["server"] == "ok"
        assert all(c["level"] != "ok" for c in out["health"]) and "server" not in [c["id"] for c in out["health"]]
        again = ok(run(home, "page", "pendulum"))
        assert again["url"].split("?")[0] == out["url"].split("?")[0].split("#")[0]
        assert launch.server_info(pendulum.parent)["pid"] == info["pid"]  # reused, not a second server
        checks = ok(run(home, "doctor", "pendulum"))["checks"]  # the same workspace rule: it finds the server
        assert {c["id"]: c["level"] for c in checks}["server"] == "ok"
    finally:
        with contextlib.suppress(OSError, ValueError, KeyError):  # whatever failed, never leave a server running
            _kill(json.loads((pendulum.parent / launch.SERVER_FILE).read_text("utf-8"))["pid"])


def test_cli_page_unknown_stage_is_one_line(home, pendulum):
    proc = run(home, "page", "pendulum", "--stage", "nope")
    assert proc.returncode == 1 and "stage" in proc.stderr and "Traceback" not in proc.stderr
    assert not (pendulum.parent / launch.SERVER_FILE).exists()  # refused before any server starts


def test_cli_page_restarts_the_server_when_page_lan_changes(home, pendulum):
    pids = []
    try:
        out = ok(run(home, "page", "pendulum"))
        first = launch.server_info(pendulum.parent)
        pids.append(first["pid"])
        assert first["lan"] is False and out["phone"] is None
        assert ok(run(home, "settings", "set", "page.lan", "true"))["settings"]["page"]["lan"] is True
        out = ok(run(home, "page", "pendulum"))
        second = launch.server_info(pendulum.parent)
        pids.append(second["pid"])
        assert second["lan"] is True and second["pid"] != first["pid"]
        assert first["bind"] == second["bind"] == "127.0.0.1"  # BSA_PAGE_BIND (tests/conftest.py): never 0.0.0.0
        assert render_status.process_token(first["pid"]) != first["process_token"]  # the old one is gone
        ip = launch.lan_ip()
        assert out["phone"] == (f"http://{ip}:{second['port']}/s/{pendulum.name}?t={second['token']}" if ip else None)
    finally:
        for pid in pids:
            with contextlib.suppress(OSError):
                _kill(pid)
