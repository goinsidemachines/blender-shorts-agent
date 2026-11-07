"""The local page server: host, token, origin, path and size checks, the state ETag, notes, decisions and idling."""

import http.client
import json
import os
import socket
import threading
import types

import pagefix
import pytest

from bsa import render_status
from bsa.review import decisions, notes, server


def _open(srv, method, path, *, host=None, headers=None, raw=None):
    conn = http.client.HTTPConnection("127.0.0.1", srv.port, timeout=10)
    conn.putrequest(method, path, skip_host=True, skip_accept_encoding=True)
    conn.putheader("Host", host or f"127.0.0.1:{srv.port}")
    for k, v in (headers or {}).items():
        conn.putheader(k, v)
    if raw is not None:
        conn.putheader("Content-Length", str(len(raw)))
    conn.endheaders(raw)
    r = conn.getresponse()
    srv.conns.append(conn)
    return r


def get(srv, path, *, host=None, headers=None):
    return _open(srv, "GET", path, host=host, headers=headers)


def api(srv, method, path, *, token=None, body=None, raw=None, origin=None, headers=None, host=None):
    h = {server.TOKEN_HEADER: srv.token if token is None else token, **(headers or {})}
    if origin is not None:
        h["Origin"] = origin
    if body is not None:
        raw = json.dumps(body, ensure_ascii=False).encode("utf-8")  # as a browser sends it
        h["Content-Type"] = "application/json"
    if method == "POST" and raw is None:
        raw = b""
    return _open(srv, method, path, host=host, headers=h, raw=raw)


def body(r) -> dict:
    return json.loads(r.read())


class Clock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t


@pytest.fixture
def srv(tmp_path):
    ws = tmp_path / "My Shorts é"
    sdir = pagefix.make_short(ws, "story")
    clock = Clock()
    s = server.make_server(ws, clock=clock)
    t = threading.Thread(target=s.serve_forever, daemon=True)
    t.start()
    fix = types.SimpleNamespace(server=s, port=s.server_address[1], token=s.token, sdir=sdir, ws=ws, clock=clock,
                                conns=[])  # fmt: skip
    yield fix
    for c in fix.conns:
        c.close()
    s.shutdown()
    s.server_close()


def test_constants():
    assert server.IDLE_S == 7200 and server.MAX_BODY == 16_384 and server.SEEN_WINDOW_S == 30
    assert server.SERVED_DIRS == ("stills", "review", "out") and server.TOKEN_HEADER == "X-BSA-Token"
    assert "frame-ancestors 'none'" in server.CSP


def test_binds_loopback_with_a_random_token(tmp_path):
    s = server.make_server(tmp_path)
    try:
        assert s.server_address[0] == "127.0.0.1" and s.lan is False and len(s.token) >= 24
        s2 = server.make_server(tmp_path)
        assert s2.token != s.token
        s2.server_close()
    finally:
        s.server_close()


def test_host_token_and_origin_checks(srv):
    assert get(srv, "/s/01-pendulum").status == 403  # no token
    assert get(srv, f"/s/01-pendulum?t={srv.token}x").status == 403  # wrong token
    assert get(srv, f"/s/01-pendulum?t={srv.token}", host="evil.test").status == 403
    assert get(srv, f"/s/01-pendulum?t={srv.token}", host=f"127.0.0.1:{srv.port + 1}").status == 403
    assert get(srv, f"/s/01-pendulum?t={srv.token}", host=f"localhost:{srv.port}").status == 200
    assert get(srv, f"/s/01-pendulum?t={srv.token}").status == 200
    assert api(srv, "GET", "/api/01-pendulum/state", token="nope").status == 403
    assert api(srv, "GET", "/api/01-pendulum/state", token="é").status == 403  # non-ASCII never raises
    assert get(srv, "/api/health").status == 403  # the API wants the header, not just any request
    assert get(srv, f"/api/health?t={srv.token}").status == 403  # ... and not the query
    r = api(srv, "GET", "/api/health")
    assert r.status == 200 and body(r) == {"ok": True}
    assert get(srv, "/files/01-pendulum/stills/s01.png?t=nope").status == 403
    assert api(srv, "POST", "/api/01-pendulum/hello", body={}, origin="http://evil.test").status == 403
    assert api(srv, "POST", "/api/01-pendulum/hello", body={}, origin="null").status == 403
    assert api(srv, "POST", "/api/01-pendulum/hello", body={}, origin=f"http://127.0.0.1:{srv.port}").status == 200
    assert api(srv, "POST", "/api/01-pendulum/hello", body={}).status == 200  # no Origin (the CLI): allowed
    r = api(srv, "POST", "/api/01-pendulum/hello", body={}, token="nope")
    assert r.status == 403 and "error" in body(r)


def test_files_only_under_served_dirs(srv):
    (srv.sdir / "out").mkdir(exist_ok=True)
    for rel in (
        "source/short.json",
        "../01-pendulum/state.json",
        "%2e%2e/%2e%2e/x",
        "stills/../state.json",
        "stills/%2e%2e/state.json",
        "stills%2f..%2fstate.json",
        "stills\\..\\state.json",
        "C:/Windows/win.ini",
        "/etc/passwd",
        "stills",
        "stills/",
        "stills/nope.png",
        "state.json",
    ):
        assert get(srv, f"/files/01-pendulum/{rel}?t={srv.token}").status == 404, rel
    assert get(srv, f"/files/../01-pendulum/stills/s01.png?t={srv.token}").status == 404
    r = get(srv, f"/files/01-pendulum/stills/s01.png?t={srv.token}")
    assert r.status == 200 and r.getheader("Content-Type") == "image/png"
    assert r.getheader("X-Content-Type-Options") == "nosniff"
    assert r.read() == (srv.sdir / "stills" / "s01.png").read_bytes()


def test_files_refuse_a_symlink_out_of_the_served_dir(srv):
    secret = srv.sdir / "source" / "short.json"
    link = srv.sdir / "stills" / "leak.json"
    try:
        os.symlink(secret, link)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks are not allowed here")
    assert get(srv, f"/files/01-pendulum/stills/leak.json?t={srv.token}").status == 404


def test_files_refuse_a_linked_folder_out_of_the_served_dir(srv):
    """A directory symlink (or, on Windows without the symlink right, a junction) inside stills/ is not followed."""
    link = srv.sdir / "stills" / "leakdir"
    try:
        os.symlink(srv.sdir / "source", link, target_is_directory=True)
    except (OSError, NotImplementedError):
        try:
            import _winapi

            _winapi.CreateJunction(str(srv.sdir / "source"), str(link))
        except (ImportError, OSError):
            pytest.skip("neither symlinks nor junctions are allowed here")
    assert (link / "short.json").is_file()
    assert get(srv, f"/files/01-pendulum/stills/leakdir/short.json?t={srv.token}").status == 404


def test_size_limits(srv):
    assert api(srv, "POST", "/api/01-pendulum/notes", raw=b"x" * 16_385).status == 413
    assert api(srv, "POST", "/api/01-pendulum/notes", raw=b"not json").status == 400
    assert api(srv, "POST", "/api/01-pendulum/notes", raw=b"[1, 2]").status == 400


def test_note_text_limit_message(srv):  # Review Focus 5 (server side)
    r = api(srv, "POST", "/api/01-pendulum/notes", body={"op": "add", "stage": "story",
            "target": {"kind": "overall", "id": None, "version": 1}, "text": "é" * 4001})  # fmt: skip
    assert r.status == 400 and "4000" in body(r)["error"]
    weird = "<img src=x onerror=alert(1)></script> 🙂 שלום"
    assert api(srv, "POST", "/api/01-pendulum/notes", body={"op": "add", "stage": "story",
               "target": {"kind": "overall", "id": None, "version": 1}, "text": weird}).status == 200  # fmt: skip
    assert notes.load(srv.sdir)[-1]["text"] == weird
    assert notes.load(srv.sdir)[-1]["by"] == "page"


def test_frame_note_shot_must_be_plain(srv):
    target = {"kind": "frame", "id": "12", "shot": {"x": 1}, "version": 1}
    r = api(srv, "POST", "/api/01-pendulum/notes", body={"op": "add", "stage": "final", "target": target, "text": "x"})
    assert r.status == 400 and "plain values" in body(r)["error"]
    target["shot"] = "s02"
    r = api(srv, "POST", "/api/01-pendulum/notes", body={"op": "add", "stage": "final", "target": target, "text": "x"})
    assert r.status == 200 and notes.load(srv.sdir)[-1]["target"]["shot"] == "s02"


def test_notes_send_and_withdraw(srv):
    target = {"kind": "overall", "id": None, "version": 1}
    a = body(api(srv, "POST", "/api/01-pendulum/notes", body={"op": "add", "stage": "story", "target": target,
                                                                "text": "one"}))["note"]  # fmt: skip
    b = body(api(srv, "POST", "/api/01-pendulum/notes", body={"op": "add", "stage": "story", "target": target,
                                                                "text": "two"}))["note"]  # fmt: skip
    r = api(srv, "POST", "/api/01-pendulum/notes", body={"op": "send", "ids": [a["id"]]})
    assert r.status == 200 and [n["id"] for n in body(r)["sent"]] == [a["id"]]
    assert api(srv, "POST", "/api/01-pendulum/notes", body={"op": "withdraw", "id": b["id"]}).status == 200
    assert [n["id"] for n in notes.load(srv.sdir)] == [a["id"]]
    r = api(srv, "POST", "/api/01-pendulum/notes", body={"op": "withdraw", "id": a["id"]})  # sent: refused
    assert r.status == 400 and "only open notes" in body(r)["error"]
    assert api(srv, "POST", "/api/01-pendulum/notes", body={"op": "nope"}).status == 400
    assert api(srv, "POST", "/api/01-pendulum/notes", body={"op": "send", "ids": "n1"}).status == 400


def test_etag_304_then_change(srv):
    r1 = api(srv, "GET", "/api/01-pendulum/state")
    tag = r1.getheader("ETag")
    snap = body(r1)
    assert r1.status == 200 and tag and snap["short"]["key"] == "01-pendulum" and snap["show"] is None
    assert api(srv, "GET", "/api/01-pendulum/state", headers={"If-None-Match": tag}).status == 304
    p = srv.sdir / "source" / "short.json"
    p.write_text(p.read_text("utf-8").replace("beat.", "beat!"), "utf-8")
    assert api(srv, "GET", "/api/01-pendulum/state", headers={"If-None-Match": tag}).status == 200


def test_state_records_history_and_lists_it(srv):
    first = body(api(srv, "GET", "/api/01-pendulum/state"))
    story = next(s for s in first["stages"] if s["id"] == "story")
    assert story["version"] == 1 and story["history"] == []
    p = srv.sdir / "source" / "short.json"
    p.write_text(p.read_text("utf-8").replace("beat.", "swing."), "utf-8")
    snap = body(api(srv, "GET", "/api/01-pendulum/state"))
    story = next(s for s in snap["stages"] if s["id"] == "story")
    assert story["version"] == 2 and [h["version"] for h in story["history"]] == [1]
    assert get(srv, f"/files/01-pendulum/{story['history'][0]['files'][0]}?t={srv.token}").status == 200


def test_state_writes_the_post_pack(srv):
    pack = srv.sdir / "out" / "post-pack.md"
    api(srv, "GET", "/api/01-pendulum/state")
    assert "## Title\nWhy a pendulum keeps perfect time" in pack.read_text("utf-8")
    p = srv.sdir / "source" / "short.json"
    d = json.loads(p.read_text("utf-8"))
    d["post"]["title"] = "New title"
    p.write_text(json.dumps(d), "utf-8")
    api(srv, "GET", "/api/01-pendulum/state")
    assert "## Title\nNew title" in pack.read_text("utf-8")


def test_decision_round_trip_and_version_mismatch(srv):
    r = api(srv, "POST", "/api/01-pendulum/decision", body={"stage": "story", "version": 9, "decision": "approve"})
    assert r.status == 409 and "version 9" in body(r)["error"]
    r = api(srv, "POST", "/api/01-pendulum/decision", body={"stage": "story", "version": 1, "decision": "approve"})
    assert r.status == 200
    made = decisions.load(srv.sdir)[-1]
    assert made["by"] == "page" and made["applied"] is False  # the page only records; `page wait` applies
    assert "story" not in json.loads((srv.sdir / "state.json").read_text("utf-8"))["stages"]
    r = api(srv, "POST", "/api/01-pendulum/decision", body={"stage": "story", "version": 1, "decision": "approve"})
    assert r.status == 409  # already approved
    assert api(srv, "POST", "/api/01-pendulum/decision", body={"stage": "story", "version": 1,
               "decision": "changes"}).status == 400  # no open note  # fmt: skip
    for bad in ({"stage": "story", "version": "1", "decision": "approve"},
                {"stage": "story", "version": True, "decision": "approve"},
                {"stage": 3, "version": 1, "decision": "approve"}):  # fmt: skip
        assert api(srv, "POST", "/api/01-pendulum/decision", body=bad).status == 400, bad
    assert api(srv, "POST", "/api/01-pendulum/decision", body={"stage": "render", "version": 1,
               "decision": "approve"}).status == 409  # fmt: skip


def test_a_camera_without_a_test_clip_is_not_approved(srv):
    from bsa import jobstate
    from bsa.review import snapshot

    def approve():
        version = snapshot.stage_version(srv.sdir, "camera")
        return api(srv, "POST", "/api/01-pendulum/decision", body={"stage": "camera", "version": version,
                   "decision": "approve"})  # fmt: skip

    path = srv.sdir / "source" / "short.json"
    data = json.loads(path.read_text("utf-8"))
    data["camera"] = {"approve": True}
    path.write_text(json.dumps(data), "utf-8")
    for stage in ("story", "storyboard"):
        jobstate.mark_done(srv.sdir, stage)
    r = approve()
    assert r.status == 409 and "no test clip yet: run BLENDER camtest" in body(r)["error"]
    assert not [d for d in decisions.load(srv.sdir) if d["stage"] == "camera"]
    clip = srv.sdir / "review" / "camtest" / "camtest.mp4"
    clip.parent.mkdir(parents=True)
    clip.write_bytes(b"mp4")
    r = approve()
    assert r.status == 200, body(r)


def test_changes_sends_the_open_notes(srv):
    target = {"kind": "overall", "id": None, "version": 1}
    api(srv, "POST", "/api/01-pendulum/notes", body={"op": "add", "stage": "story", "target": target, "text": "x"})
    r = api(srv, "POST", "/api/01-pendulum/decision", body={"stage": "story", "version": 1, "decision": "changes"})
    assert r.status == 200 and [n["status"] for n in body(r)["notes_sent"]] == ["sent"]
    assert decisions.load(srv.sdir)[-1]["decision"] == "changes"


def test_damaged_decisions_answer_503(srv):
    decisions.decisions_path(srv.sdir).write_text("{oops", "utf-8")
    r = api(srv, "POST", "/api/01-pendulum/decision", body={"stage": "story", "version": 1, "decision": "approve"})
    err = body(r)["error"]
    assert r.status == 503 and "decisions.json is damaged" in err and "\n" not in err
    assert decisions.decisions_path(srv.sdir).read_text("utf-8") == "{oops"  # never written over


def test_show_hint_is_one_shot_and_presence(srv):
    r = api(srv, "GET", "/api/01-pendulum/presence")
    assert r.status == 200 and body(r) == {"seen_s_ago": None, "hello_s_ago": None}
    srv.clock.t += 5
    assert api(srv, "POST", "/api/01-pendulum/hello", body={}).status == 200
    srv.clock.t += 3
    assert body(api(srv, "GET", "/api/01-pendulum/presence")) == {"seen_s_ago": 3.0, "hello_s_ago": 3.0}
    r1 = api(srv, "GET", "/api/01-pendulum/state")
    tag = r1.getheader("ETag")
    r1.read()
    srv.clock.t += 2  # a state poll is a check-in, but not a hello: only a newly loaded page says hello
    assert body(api(srv, "GET", "/api/01-pendulum/presence")) == {"seen_s_ago": 2.0, "hello_s_ago": 5.0}
    assert api(srv, "POST", "/api/01-pendulum/show", body={"stage": "nope"}).status == 400
    assert api(srv, "POST", "/api/01-pendulum/show", body={"stage": "storyboard"}).status == 200
    r = api(srv, "GET", "/api/01-pendulum/state", headers={"If-None-Match": tag})
    assert r.status == 200 and body(r)["show"] == "storyboard"  # a pending hint beats the 304
    r = api(srv, "GET", "/api/01-pendulum/state", headers={"If-None-Match": tag})
    assert r.status == 304
    r = api(srv, "GET", "/api/01-pendulum/state")
    assert body(r)["show"] is None  # popped
    assert srv.server.seen["01-pendulum"] == srv.clock.t


def test_range_request_206(srv):
    clip = srv.sdir / "out" / "pendulum.mp4"
    clip.parent.mkdir(exist_ok=True)
    data = bytes(range(100))
    clip.write_bytes(data)
    url = f"/files/01-pendulum/out/pendulum.mp4?t={srv.token}"
    r = get(srv, url, headers={"Range": "bytes=0-9"})
    assert r.status == 206 and r.read() == data[:10]
    assert r.getheader("Content-Range") == "bytes 0-9/100" and r.getheader("Content-Type") == "video/mp4"
    r = get(srv, url, headers={"Range": "bytes=95-"})
    assert r.status == 206 and r.read() == data[95:]
    r = get(srv, url, headers={"Range": "bytes=-3"})
    assert r.status == 206 and r.read() == data[-3:]
    r = get(srv, url, headers={"Range": "bytes=200-300"})
    assert r.status == 416 and r.getheader("Content-Range") == "bytes */100"
    r.read()
    r = get(srv, url)
    assert r.status == 200 and r.getheader("Accept-Ranges") == "bytes" and r.read() == data


def test_csp_header_on_page(srv):
    r = get(srv, f"/s/01-pendulum?t={srv.token}")
    assert r.status == 200 and r.getheader("Content-Security-Policy") == server.CSP
    assert r.getheader("Content-Type").startswith("text/html") and r.getheader("Referrer-Policy") == "no-referrer"
    assert b"<html" in r.read().lower()


def test_unknown_short_404(srv):
    (srv.ws / "not-a-short").mkdir()
    for short in ("nope", "not-a-short", "..", ".", "%2e%2e", "01-pendulum%2fstills"):
        r = get(srv, f"/s/{short}?t={srv.token}")
        assert r.status == 404, short
        assert "error" in body(r)
        assert api(srv, "GET", f"/api/{short}/state").status == 404, short
    assert api(srv, "GET", "/api/01-pendulum/nope").status == 404
    assert get(srv, f"/nope?t={srv.token}").status == 404


def _running_status(sdir, pid):
    path = render_status.status_path(sdir, "final")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"state": "running", "pid": pid, "pid_token": render_status.process_token(pid)}),
                    encoding="utf-8")  # fmt: skip
    return path


def test_idle_due_after_two_hours_unless_a_job_runs(tmp_path):
    sdir = pagefix.make_short(tmp_path, "story")
    clock = Clock(0.0)
    s = server.make_server(tmp_path, clock=clock)
    try:
        assert not server.jobs_running(tmp_path)
        clock.t = server.IDLE_S - 1
        assert not server.idle_due(s)
        s.seen["01-pendulum"] = 100.0  # a page checked in later than the start
        clock.t = server.IDLE_S + 50
        assert not server.idle_due(s)
        clock.t = server.IDLE_S + 100
        assert server.idle_due(s)
        status = _running_status(sdir, os.getpid())
        assert server.jobs_running(tmp_path) and not server.idle_due(s)
        status.write_text(json.dumps({"state": "done", "pid": os.getpid()}), encoding="utf-8")
        assert server.idle_due(s)
        _running_status(sdir, 2**22 + 12345)  # "running" but its process is gone
        assert not server.jobs_running(tmp_path) and server.idle_due(s)
    finally:
        s.server_close()


def test_serve_stops_once_idle(tmp_path):
    clock = Clock(0.0)
    s = server.make_server(tmp_path, clock=clock)
    done = threading.Event()

    def run():
        server.serve(s, tick_s=0.02)
        done.set()

    threading.Thread(target=run, daemon=True).start()
    assert not done.wait(0.2)
    clock.t = server.IDLE_S + 1
    assert done.wait(5)


def test_lan_adds_only_this_machines_address(tmp_path):
    s = server.make_server(tmp_path, lan=True, token="fixed-token-for-the-test")
    try:
        p = s.server_address[1]
        assert s.lan is True and s.token == "fixed-token-for-the-test"
        assert {f"127.0.0.1:{p}", f"localhost:{p}"} <= s.allowed_hosts
        assert s.allowed_hosts - {f"127.0.0.1:{p}", f"localhost:{p}"} == ({f"{s.lan_ip}:{p}"} if s.lan_ip else set())
    finally:
        s.server_close()


def _raw(srv, data: bytes, wait_s: float = 3.0) -> bytes:
    """Send raw bytes on one connection and read everything until the server closes it (or wait_s passes)."""
    c = socket.create_connection(("127.0.0.1", srv.port))
    c.settimeout(wait_s)
    out = b""
    try:
        c.sendall(data)
        while chunk := c.recv(65536):
            out += chunk
    except (TimeoutError, ConnectionError):
        pass
    finally:
        c.close()
    return out


def _smuggled(srv) -> bytes:
    good = f"GET /api/health HTTP/1.1\r\nHost: 127.0.0.1:{srv.port}\r\n{server.TOKEN_HEADER}: {srv.token}\r\n\r\n"
    return good.encode()


@pytest.mark.parametrize(
    "head",
    [
        "POST /api/01-pendulum/hello HTTP/1.1\r\nHost: evil.test:{port}\r\n",  # bad Host
        "POST /api/01-pendulum/hello HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nX-BSA-Token: nope\r\n",  # bad token
        (
            "POST /api/01-pendulum/hello HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nX-BSA-Token: {token}\r\n"
            "Origin: http://evil.test\r\n"
        ),  # bad Origin
        "POST /nope HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\n",  # unknown route
        "GET /api/health HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nX-BSA-Token: {token}\r\n",  # a GET's body: unread
    ],
)
def test_an_unread_body_is_never_read_as_the_next_request(srv, head):
    inner = _smuggled(srv)
    head = head.format(port=srv.port, token=srv.token)
    out = _raw(srv, f"{head}Content-Length: {len(inner)}\r\n\r\n".encode() + inner)
    assert out.count(b"HTTP/1.1 ") == 1, out  # one reply, then the connection is closed


def test_keep_alive_still_works_for_good_requests(srv):
    out = _raw(srv, _smuggled(srv) * 2, wait_s=1.0)
    assert out.count(b"HTTP/1.1 200") == 2


def test_a_stalled_body_is_a_408_not_a_store_problem(srv, monkeypatch):
    monkeypatch.setattr(server._Handler, "timeout", 0.3)
    head = (f"POST /api/01-pendulum/hello HTTP/1.1\r\nHost: 127.0.0.1:{srv.port}\r\n{server.TOKEN_HEADER}: "
            f"{srv.token}\r\nContent-Length: 50\r\n\r\n{{}}")  # fmt: skip
    out = _raw(srv, head.encode())
    assert out.startswith(b"HTTP/1.1 408"), out


def test_origin_is_compared_with_the_normalised_host(srv):
    r = api(srv, "POST", "/api/01-pendulum/hello", body={}, host=f"LocalHost:{srv.port}",
            origin=f"http://localhost:{srv.port}")  # fmt: skip
    assert r.status == 200


def test_files_are_sandboxed_and_html_or_svg_only_downloads(srv):
    (srv.sdir / "review" / "old.html").write_text("<script>alert(1)</script>", "utf-8")
    (srv.sdir / "stills" / "pic.svg").write_text("<svg xmlns='http://www.w3.org/2000/svg'/>", "utf-8")
    for rel, attach in (("review/old.html", True), ("stills/pic.svg", True), ("stills/s01.png", False)):
        r = get(srv, f"/files/01-pendulum/{rel}?t={srv.token}")
        r.read()
        assert r.status == 200, rel
        assert (
            r.getheader("Content-Security-Policy")
            == server.FILES_CSP
            == ("sandbox; default-src 'none'; img-src 'self'; media-src 'self'")
        )
        assert r.getheader("Referrer-Policy") == "no-referrer" and r.getheader("X-Content-Type-Options") == "nosniff"
        assert (r.getheader("Content-Disposition") == "attachment") is attach, rel
    r = get(srv, f"/files/01-pendulum/review/old.html?t={srv.token}", headers={"Range": "bytes=0-3"})
    assert r.status == 206 and r.getheader("Content-Disposition") == "attachment"
    assert r.getheader("Content-Security-Policy") == server.FILES_CSP


def test_feedback_reported_marks_only_the_given_entries(srv, bsa_home):
    from bsa import feedback

    path = "/api/01-pendulum/feedback-reported"
    feedback.add(srv.sdir, "blocking", "one")
    feedback.add(srv.sdir, "rough-edge", "two")
    r = api(srv, "POST", path, body={"ids": ["f1"]})
    assert r.status == 409 and feedback.pending(srv.sdir)  # the user has not said yes to the panel yet
    feedback.allow_panel(srv.sdir)
    assert api(srv, "POST", path, token="", body={"ids": ["f1"]}).status == 403
    assert api(srv, "POST", path, body={"ids": ["f1"]}, origin="http://evil.example").status == 403
    assert len(feedback.pending(srv.sdir)) == 2  # neither marked anything
    for bad in ({}, {"ids": "f1"}, {"ids": [1]}):
        assert api(srv, "POST", path, body=bad).status == 400, bad
    r = api(srv, "POST", path, body={"ids": ["f1"]})
    assert r.status == 200 and body(r) == {"reported": 1}
    assert [e["id"] for e in feedback.pending(srv.sdir)] == ["f2"]
    assert api(srv, "GET", path).status == 404
    from bsa import settings as settings_mod

    settings_mod.save_settings({"feedback": "off"})
    assert api(srv, "POST", path, body={"ids": ["f2"]}).status == 409 and feedback.pending(srv.sdir)


@pytest.mark.parametrize(
    "framing",
    [
        "Content-Length: 2\r\nContent-Length: 2\r\n",  # two lengths: which one is the body?
        "Content-Length: 2\r\nTransfer-Encoding: chunked\r\n",  # both framings at once
    ],
)
def test_ambiguous_body_framing_is_a_400_and_closes(srv, framing):
    head = (f"POST /api/01-pendulum/hello HTTP/1.1\r\nHost: 127.0.0.1:{srv.port}\r\n{server.TOKEN_HEADER}: "
            f"{srv.token}\r\n{framing}\r\n{{}}")  # fmt: skip
    out = _raw(srv, head.encode() + _smuggled(srv))
    assert out.startswith(b"HTTP/1.1 400"), out
    assert out.count(b"HTTP/1.1 ") == 1, out  # the connection is closed: nothing after it is read as a request


def test_a_decision_on_a_stage_that_is_not_current_is_refused(srv):
    for stage in ("storyboard", "engine", "final"):  # the story is current: these are locked
        r = api(srv, "POST", "/api/01-pendulum/decision", body={"stage": stage, "version": 1, "decision": "approve",
                                                               "choice": {"engine": "eevee"}})  # fmt: skip
        assert r.status == 409 and "not the stage" in body(r)["error"], stage
    assert decisions.load(srv.sdir) == []


def test_final_can_be_answered_while_current_or_done(tmp_path):
    ws = tmp_path / "ws"
    for stage in ("final", "done"):
        sdir = pagefix.make_short(ws, stage, number=1 if stage == "final" else 2)
        s = server.make_server(ws)
        t = threading.Thread(target=s.serve_forever, daemon=True)
        t.start()
        fix = types.SimpleNamespace(port=s.server_address[1], token=s.token, conns=[])
        try:
            from bsa.review import snapshot

            v = snapshot.stage_version(sdir, "final")
            r = api(fix, "POST", f"/api/{sdir.name}/decision", body={"stage": "final", "version": v,
                                                                     "decision": "approve"})  # fmt: skip
            assert r.status == 200, (stage, body(r))
        finally:
            for c in fix.conns:
                c.close()
            s.shutdown()
            s.server_close()


def test_a_range_response_is_capped_and_holds_no_file_open(srv, monkeypatch):
    """A video asks for "bytes=0-": each answer is at most RANGE_CAP bytes, read and closed before it is sent, so a
    page streaming the MP4 never holds it open for long (on Windows that would make the edit's replace fail)."""
    assert 1 << 20 <= server.RANGE_CAP <= 8 << 20
    monkeypatch.setattr(server, "RANGE_CAP", 10)
    clip = srv.sdir / "out" / "pendulum.mp4"
    clip.parent.mkdir(exist_ok=True)
    data = bytes(range(100))
    clip.write_bytes(data)
    url = f"/files/01-pendulum/out/pendulum.mp4?t={srv.token}"
    r = get(srv, url, headers={"Range": "bytes=0-"})
    assert r.status == 206 and r.read() == data[:10] and r.getheader("Content-Range") == "bytes 0-9/100"
    r = get(srv, url, headers={"Range": "bytes=95-"})
    assert r.status == 206 and r.read() == data[95:] and r.getheader("Content-Range") == "bytes 95-99/100"
    r = get(srv, url, headers={"Range": "bytes=-30"})
    assert r.read() == data[70:80] and r.getheader("Content-Range") == "bytes 70-79/100"
    r = get(srv, url)
    assert r.status == 200 and r.read() == data  # no Range: the whole file, as before


def test_thumbnail_route_chooses(srv):
    from bsa import thumbs

    pagefix.write_thumbs(srv.sdir, 2)
    r = api(srv, "POST", "/api/01-pendulum/thumbnail", body={"choice": 1})
    assert r.status == 200
    assert body(r) == {"thumbnail": {"chosen": 1, "files": ["out/thumbnail-1080x1920.png",
                                                            "out/thumbnail-1280x720.png"]}}  # fmt: skip
    assert thumbs.load(srv.sdir)["chosen"]["n"] == 1 and thumbs.load(srv.sdir)["chosen"]["by"] == "page"
    for bad in ({"choice": "1"}, {"choice": True}, {"choice": 1.0}, {}):
        r = api(srv, "POST", "/api/01-pendulum/thumbnail", body=bad)
        assert r.status == 400 and "whole number" in body(r)["error"], bad
    r = api(srv, "POST", "/api/01-pendulum/thumbnail", body={"choice": 7})
    assert r.status == 400 and body(r)["error"] == "There is no thumbnail candidate 7"
    assert thumbs.load(srv.sdir)["chosen"]["n"] == 1


def test_checklist_route_ticks(srv):
    from bsa.review import checklist

    r = api(srv, "POST", "/api/01-pendulum/checklist", body={"id": "upload", "done": True})
    assert r.status == 200 and body(r) == {"ticks": {"upload": True}}
    for bad in ({"id": "nope", "done": True}, {"id": "upload", "done": 1}, {"id": "upload"}, {"id": 3, "done": True},
                {"done": True}):  # fmt: skip
        r = api(srv, "POST", "/api/01-pendulum/checklist", body=bad)
        assert r.status == 400, bad
    r = api(srv, "POST", "/api/01-pendulum/checklist", body={"id": "upload", "done": False}, token="wrong")
    assert r.status == 403
    assert checklist.load(srv.sdir) == {"upload": True}


def test_binding_does_no_reverse_dns_lookup(tmp_path, monkeypatch):
    """HTTPServer's server_bind calls socket.getfqdn on the bind address, which can stall for many seconds on macOS
    (longer than a new server has to answer): the page server must bind without it."""

    def stall(*_a, **_k):
        raise AssertionError("socket.getfqdn was called while binding the page server")

    monkeypatch.setattr(socket, "getfqdn", stall)
    s = server.make_server(tmp_path)
    try:
        assert s.server_port == s.port and s.server_name == s.server_address[0]
    finally:
        s.server_close()
