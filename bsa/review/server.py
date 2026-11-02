"""The local page server: one per workspace, serving each short's live review page and its small JSON API.

It binds 127.0.0.1 on a free port (the local network only with lan=True) and checks every request in this order:
the Host header (127.0.0.1:<port> or localhost:<port>, plus the LAN address with lan), the token (the `t` query for
the page and its files, the X-BSA-Token header for the API, compared in constant time), and on POST a same-origin
Origin when one is sent. Only a short's stills/, review/ and out/ are served, never anything outside them (symlinks
included). It never runs commands; its only writes are notes, decisions, the versions a snapshot records and the
plugin feedback's "reported" mark.
A page Approve only records the decision; `page wait` applies it. With lan=True it binds every interface (_LAN_BIND,
or the BSA_PAGE_BIND variable, which the tests set to 127.0.0.1) but still answers only a Host of 127.0.0.1,
localhost or this machine's one LAN address, with the same token.
A connection whose request got an error reply, or whose body was not read in full, is closed, so leftover bytes are
never read as another request. Files are served sandboxed (their own CSP), and HTML or SVG only as downloads."""

from __future__ import annotations

import contextlib
import hmac
import json
import mimetypes
import os
import re
import secrets
import threading
import time
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlsplit

from bsa import feedback, messages, render_status
from bsa import settings as settings_mod
from bsa.review import decisions, notes, page, snapshot, stages, store

IDLE_S = 7200
_LAN_BIND = "0.0.0.0"  # phone access: every interface (the Host check still admits only this machine's addresses)
BIND_ENV = "BSA_PAGE_BIND"  # overrides _LAN_BIND (tests bind 127.0.0.1 so nothing opens the developer's network)
MAX_BODY = 16_384
SEEN_WINDOW_S = 30
SERVED_DIRS = ("stills", "review", "out")
TOKEN_HEADER = "X-BSA-Token"
CSP = (
    "default-src 'self'; img-src 'self' data:; media-src 'self'; style-src 'unsafe-inline'; "
    "script-src 'unsafe-inline'; connect-src 'self'; frame-ancestors 'none'"
)
_DRAIN_MAX = 1 << 20  # an oversized body up to this size is read and dropped, so the 413 reaches the client
_CHUNK = 64 * 1024
_BAD_IN_NAME = ("/", "\\", ":", "\0")
_RANGE = re.compile(r"bytes=(\d*)-(\d*)")
FILES_CSP = "sandbox; default-src 'none'; img-src 'self'; media-src 'self'"
_ACTIVE_TYPES = ("text/html", "image/svg+xml")  # served only as downloads, never rendered as documents


class _Refuse(Exception):
    def __init__(self, status: int, line: str):
        super().__init__(line)
        self.status = status


class PageServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False  # never share the port with another process

    workspace: Path
    token: str
    lan: bool
    lan_ip: str | None
    hints: dict[str, str]
    seen: dict[str, float]  # the last check-in of any kind (a hello or a state poll), per short
    hello_at: dict[str, float]  # the last hello: a page that has just loaded
    clock: Callable[[], float]
    last_activity: float
    allowed_hosts: frozenset[str]
    lock: threading.Lock

    def handle_error(self, request, client_address) -> None:  # a dropped connection is not worth a traceback
        pass

    @property
    def port(self) -> int:
        return self.server_address[1]

    def phone_url(self, short_key: str) -> str | None:
        """The page's link on this machine's LAN address, only with lan on (and an address found)."""
        if not (self.lan and self.lan_ip):
            return None
        return f"http://{self.lan_ip}:{self.port}/s/{quote(short_key, safe='')}?t={quote(self.token, safe='')}"

    def last_seen(self) -> float:
        with self.lock:
            return max([self.last_activity, *self.seen.values()])


def bind_address(lan: bool) -> str:
    """Where the server listens: loopback only, or with lan every interface (BSA_PAGE_BIND overrides that)."""
    return (os.environ.get(BIND_ENV) or _LAN_BIND) if lan else "127.0.0.1"


def make_server(
    workspace: Path, *, port: int = 0, lan: bool = False, token: str | None = None, clock=time.time
) -> PageServer:
    srv = PageServer((bind_address(lan), port), _Handler)
    srv.workspace = Path(workspace).resolve()
    srv.token = token or secrets.token_urlsafe(24)
    srv.lan = lan
    if lan:
        from bsa.review import launch  # launch starts servers, so it imports this module

        srv.lan_ip = launch.lan_ip()
    else:
        srv.lan_ip = None
    srv.hints, srv.seen, srv.hello_at = {}, {}, {}
    srv.lock = threading.Lock()
    srv.clock = clock
    srv.last_activity = clock()
    p = srv.server_address[1]
    hosts = {f"127.0.0.1:{p}", f"localhost:{p}"}
    if srv.lan_ip:
        hosts.add(f"{srv.lan_ip}:{p}")
    srv.allowed_hosts = frozenset(hosts)
    return srv


def jobs_running(workspace: Path) -> bool:
    """A render or other job of any short in the workspace is running right now."""
    for path in Path(workspace).glob("*/review/render-status-*.json"):
        status = render_status.read_status(path)
        if status and status.get("state") == "running" and render_status.is_alive(status):
            return True
    return False


def idle_due(server: PageServer) -> bool:
    """No request and no page check-in for IDLE_S, and no job running."""
    return server.clock() - server.last_seen() >= IDLE_S and not jobs_running(server.workspace)


def serve(server: PageServer, tick_s: float = 60) -> None:
    """Serve until idle_due, then shut down and close the socket."""
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.5}, daemon=True)
    thread.start()
    try:
        while not idle_due(server):
            time.sleep(tick_s)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)


def _same(given: str | None, want: str) -> bool:
    return given is not None and hmac.compare_digest(given.encode("utf-8", "surrogatepass"), want.encode("utf-8"))


def _etag_matches(header: str | None, tag: str) -> bool:
    if not header:
        return False
    return header.strip() == "*" or tag in [t.strip().removeprefix("W/") for t in header.split(",")]


class _Handler(BaseHTTPRequestHandler):
    server: PageServer
    protocol_version = "HTTP/1.1"
    timeout = 30  # a client that stalls mid-request is dropped
    server_version = "bsa-page"
    sys_version = ""

    def log_message(self, format, *args) -> None:
        pass

    def do_GET(self) -> None:
        self._handle("GET")

    def do_POST(self) -> None:
        self._handle("POST")

    # ------------------------------------------------------------------ plumbing

    def _handle(self, method: str) -> None:
        self._started = False
        self._body_done = False
        try:
            self._route(method)
            if not self._body_done and self._sent_a_body():  # unread bytes must never be read as a request
                self.close_connection = True
                self._drain()
            return
        except ConnectionError:  # the client went away
            self.close_connection = True
            return
        except _Refuse as e:
            status, line = e.status, str(e)
        except decisions.DecisionRefused as e:
            status, line = 409, str(e)
        except ValueError as e:
            status, line = 400, str(e)
        except store.StoreDamaged as e:
            status, line = 503, str(e)
        except TimeoutError as e:  # a store lock stayed held
            status, line = 503, str(e)
        except Exception as e:  # noqa: BLE001 - one plain line, never a traceback
            status, line = 500, f"{type(e).__name__}: {e}"
        self.close_connection = True  # an error reply always ends the connection: the body may be unread
        self._drain()
        if self._started:  # the reply is already under way: all that can be done is to drop the connection
            return
        try:
            self._json(status, {"error": messages.one_line(line)})
        except ConnectionError:
            self.close_connection = True

    def _send(self, status: int, data: bytes, ctype: str, headers: dict[str, str] | None = None) -> None:
        self._started = True
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "no-cache")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def _json(self, status: int, obj, headers: dict[str, str] | None = None) -> None:
        data = json.dumps(obj, ensure_ascii=False, default=str).encode("utf-8")
        self._send(status, data, "application/json; charset=utf-8", headers)

    def _sent_a_body(self) -> bool:
        return bool(self.headers.get("Transfer-Encoding")) or (self.headers.get("Content-Length") or "0").strip() != "0"

    def _drain(self) -> None:
        """Read and drop a body nobody read (up to _DRAIN_MAX), so the reply reaches the client before the close
        instead of a connection reset. The connection is closed after this either way."""
        if self._body_done or self.headers.get("Transfer-Encoding"):
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return
        if 0 < length <= _DRAIN_MAX:
            with contextlib.suppress(OSError, _Refuse):
                self._read(length)

    def _read(self, length: int) -> bytes:
        self._body_done = True  # read (or tried): never read again on this connection
        try:
            raw = self.rfile.read(length)
        except TimeoutError:  # the client stalled mid-body: not a store problem
            raise _Refuse(408, "the request body did not arrive in time") from None
        if len(raw) != length:
            raise _Refuse(400, "the request body was cut short")
        return raw

    def _body(self) -> dict:
        if self.headers.get("Transfer-Encoding"):
            raise _Refuse(411, "send the body with a Content-Length")
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = -1
        if length < 0:
            raise _Refuse(400, "the Content-Length is not a number")
        if length > MAX_BODY:
            raise _Refuse(413, f"the request is too big: at most {MAX_BODY} bytes")
        raw = self._read(length) if length else b"{}"
        try:
            body = json.loads(raw.decode("utf-8"))
        except ValueError:
            raise ValueError("the body must be a JSON object") from None
        if not isinstance(body, dict):
            raise ValueError("the body must be a JSON object")  # noqa: TRY004 - a 400 for the client
        return body

    # ------------------------------------------------------------------ checks

    def _check(self, method: str, kind: str, query: dict) -> None:
        srv = self.server
        host = (self.headers.get("Host") or "").strip().lower()
        if host not in srv.allowed_hosts:
            raise _Refuse(403, "not allowed: this server only answers on this computer's own address")
        given = query.get("t", [None])[0] if kind in ("s", "files") else self.headers.get(TOKEN_HEADER)
        if not _same(given, srv.token):
            raise _Refuse(403, "not allowed: the link's token is missing or wrong; open the page again")
        if method == "POST":
            origin = self.headers.get("Origin")
            if origin is not None and origin.strip().lower() != f"http://{host}":
                raise _Refuse(403, "not allowed: the request came from another site")
        with srv.lock:
            srv.last_activity = srv.clock()

    def _short(self, raw: str) -> tuple[str, Path]:
        """The short's folder name and path: a direct child folder of the workspace with source/short.json or
        state.json in it. Anything else is a 404."""
        name = unquote(raw)
        ws = self.server.workspace
        if not name or name in (".", "..") or any(c in name for c in _BAD_IN_NAME):
            raise _Refuse(404, "no such short")
        sdir = ws / name
        try:
            ok = (
                any(p.name == name for p in ws.iterdir())
                and sdir.is_dir()
                and ((sdir / "source" / "short.json").is_file() or (sdir / "state.json").is_file())
                and sdir.resolve().parent == ws
            )
        except OSError:
            ok = False
        if not ok:
            raise _Refuse(404, f"no short {name!r} in this workspace")
        return name, sdir

    # ------------------------------------------------------------------ routes

    def _route(self, method: str) -> None:
        url = urlsplit(self.path)
        segs = url.path.split("/")[1:] if url.path.startswith("/") else []
        kind = segs[0] if segs else ""
        if kind not in ("s", "files", "api"):
            raise _Refuse(404, "nothing here")
        self._check(method, kind, parse_qs(url.query))
        if kind == "s" and method == "GET" and len(segs) == 2:
            return self._page(self._short(segs[1])[1])
        if kind == "files" and method == "GET" and len(segs) >= 3:
            return self._file(self._short(segs[1])[1], segs[2:])
        if kind == "api":
            if segs[1:] == ["health"] and method == "GET":
                return self._json(200, {"ok": True})
            if len(segs) == 3:
                route = (method, segs[2])
                if route in _API:
                    name, sdir = self._short(segs[1])
                    body = self._body() if method == "POST" else None
                    return _API[route](self, name, sdir, body)
        raise _Refuse(404, "nothing here")

    def _page(self, sdir: Path) -> None:
        html = page.render(mode="server", snapshot=snapshot.build(sdir)).encode("utf-8")
        self._send(200, html, "text/html; charset=utf-8",
                   {"Content-Security-Policy": CSP, "Referrer-Policy": "no-referrer", "Cache-Control": "no-store"})  # fmt: skip

    def _file(self, sdir: Path, raw_parts: list[str]) -> None:
        parts = [unquote(p) for p in raw_parts]
        if (
            len(parts) < 2
            or parts[0] not in SERVED_DIRS
            or any(p in ("", ".", "..") or any(c in p for c in _BAD_IN_NAME) for p in parts)
        ):
            raise _Refuse(404, "no such file")
        try:
            home = sdir.resolve()
            base = (sdir / parts[0]).resolve()
            target = sdir.joinpath(*parts).resolve()
            ok = base.is_relative_to(home) and target.is_relative_to(base) and target.is_file()
        except (OSError, ValueError):
            ok = False
        if not ok:
            raise _Refuse(404, "no such file")
        size = target.stat().st_size
        ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        guard = {"Content-Security-Policy": FILES_CSP, "Referrer-Policy": "no-referrer"}
        if ctype in _ACTIVE_TYPES:
            guard["Content-Disposition"] = "attachment"
        start, end, status = 0, size - 1, 200
        m = _RANGE.fullmatch((self.headers.get("Range") or "").strip())
        if m and (m[1] or m[2]):
            if m[1]:
                start = int(m[1])
                end = min(int(m[2]), size - 1) if m[2] else size - 1
                bad = start >= size or (m[2] and int(m[2]) < start)
            else:
                n = int(m[2])
                start, bad = max(size - n, 0), n == 0 or size == 0
            if bad:
                return self._send(416, b"", ctype, {**guard, "Content-Range": f"bytes */{size}"})
            status = 206
        length = max(end - start + 1, 0)
        self._started = True
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(length))
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "no-cache")
        for k, v in guard.items():
            self.send_header(k, v)
        if status == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        with target.open("rb") as f:
            f.seek(start)
            left = length
            while left > 0:
                chunk = f.read(min(_CHUNK, left))
                if not chunk:
                    break
                self.wfile.write(chunk)
                left -= len(chunk)
        if left:  # the file shrank under us: the promised length can't be kept
            self.close_connection = True

    def _state(self, name: str, sdir: Path, _body) -> None:
        snap = snapshot.build(sdir)
        srv = self.server
        phone = srv.phone_url(name)  # the snapshot can't know the port: the phone link is added here
        if phone:
            snap["phone"] = {"url": phone}
        tag = snapshot.etag(snap)
        with srv.lock:
            srv.seen[name] = srv.clock()
            hint = srv.hints.pop(name, None)
        if hint is None and _etag_matches(self.headers.get("If-None-Match"), tag):
            self._started = True
            self.send_response(304)
            self.send_header("ETag", tag)
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            return
        snap["show"] = hint
        self._json(200, snap, {"ETag": tag})

    def _presence(self, name: str, _sdir: Path, _body) -> None:
        srv = self.server
        with srv.lock:
            at, hello = srv.seen.get(name), srv.hello_at.get(name)
            now = srv.clock()
        self._json(200, {"seen_s_ago": None if at is None else now - at,
                         "hello_s_ago": None if hello is None else now - hello})  # fmt: skip

    def _hello(self, name: str, _sdir: Path, _body) -> None:
        srv = self.server
        with srv.lock:
            srv.seen[name] = srv.hello_at[name] = srv.clock()
        self._json(200, {"ok": True})

    def _show(self, name: str, _sdir: Path, body: dict) -> None:
        stage = body.get("stage")
        if not isinstance(stage, str) or stage not in stages.PAGE_STAGES:
            raise ValueError(f"unknown stage {stage!r}; expected one of {stages.PAGE_STAGES}")
        with self.server.lock:
            self.server.hints[name] = stage
        self._json(200, {"ok": True})

    def _notes(self, _name: str, sdir: Path, body: dict) -> None:
        op = body.get("op")
        if op == "add":
            stage = body.get("stage")
            if not isinstance(stage, str):
                raise ValueError(f"unknown stage {stage!r}")
            target = body.get("target")
            if isinstance(target, dict) and not all(isinstance(target.get(k), (str, int, type(None)))
                                                    for k in ("id", "version")):  # fmt: skip
                raise ValueError("a note's target id and version must be plain values")
            return self._json(200, {"note": notes.add(sdir, stage, target, body.get("text"), "page")})
        if op == "send":
            ids = body.get("ids")
            if not isinstance(ids, list) or not all(isinstance(i, str) for i in ids):
                raise ValueError("send needs ids: a list of note ids")
            return self._json(200, {"sent": notes.send(sdir, ids)})
        if op == "withdraw":
            note_id = body.get("id")
            if not isinstance(note_id, str):
                raise ValueError("withdraw needs the note's id")
            notes.withdraw(sdir, note_id)
            return self._json(200, {"ok": True})
        raise ValueError(f"unknown op {op!r}; expected add, send or withdraw")

    def _decision(self, _name: str, sdir: Path, body: dict) -> None:
        stage, version, decision = body.get("stage"), body.get("version"), body.get("decision")
        if not isinstance(stage, str) or stage not in stages.PAGE_STAGES:
            raise ValueError(f"unknown stage {stage!r}; expected one of {stages.PAGE_STAGES}")
        if not isinstance(version, int) or isinstance(version, bool):
            raise ValueError("the version must be a whole number")  # noqa: TRY004 - a 400 for the client
        if decision not in decisions.DECISIONS:
            raise ValueError(f"unknown decision {decision!r}; expected one of {decisions.DECISIONS}")
        label = stages.LABELS[stage]
        if stage not in (*stages.APPROVAL_STAGES, *stages.ACK_STAGES):
            raise decisions.DecisionRefused(f"The {label} has nothing to approve.")
        current = snapshot.stage_version(sdir, stage)
        if current is None:
            raise decisions.DecisionRefused(f"The {label} has nothing to answer yet.")
        if decision == "changes" and not any(n["stage"] == stage and n["status"] == "open" for n in notes.load(sdir)):
            raise ValueError("Add a note saying what to change first.")
        entry = decisions.record(sdir, stage, version, decision, "page", current=current, choice=body.get("choice"))
        sent = notes.send_stage(sdir, stage) if decision == "changes" else []
        self._json(200, {"decision": entry, "notes_sent": sent})

    def _feedback_reported(self, _name: str, sdir: Path, body: dict) -> None:
        """The user copied the plugin feedback report or opened it on GitHub: mark those entries reported. Only
        while the panel is shown (the user said yes, and the setting is not `off`)."""
        ids = body.get("ids")
        if not isinstance(ids, list) or not ids or not all(isinstance(i, str) for i in ids):
            raise ValueError("feedback-reported needs ids: a list of feedback entry ids")
        try:
            setting = settings_mod.load_settings().get("feedback")
        except Exception:  # noqa: BLE001 - unreadable settings: the default, `ask`
            setting = None
        if not feedback.panel_allowed(sdir, setting):
            raise _Refuse(409, "The plugin feedback is not shown for this short.")
        self._json(200, {"reported": feedback.mark_reported(sdir, ids)})


_API = {
    ("GET", "state"): _Handler._state,
    ("GET", "presence"): _Handler._presence,
    ("POST", "hello"): _Handler._hello,
    ("POST", "notes"): _Handler._notes,
    ("POST", "decision"): _Handler._decision,
    ("POST", "show"): _Handler._show,
    ("POST", "feedback-reported"): _Handler._feedback_reported,
}
