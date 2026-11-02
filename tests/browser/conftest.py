"""Browser-test fixtures: fixture shorts served by a real in-thread page server, and Chromium pages at desktop and
phone sizes, checked for console errors, horizontal scroll and serious accessibility problems."""

from __future__ import annotations

import contextlib
import socket
import threading
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import quote

import pagefix
import pytest

from bsa.review import server

SIZES = {"desktop": (1280, 800), "phone": (375, 812)}
_ERRORS: dict[int, list[str]] = {}  # console errors per page, by id(page)


@dataclass
class LiveServer:
    srv: server.PageServer
    sdir: Path
    thread: threading.Thread
    conns: list = field(default_factory=list)
    closed: bool = field(default=False)

    @property
    def key(self) -> str:
        return self.sdir.name

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.srv.port}/s/{quote(self.key, safe='')}?t={self.srv.token}"

    def shutdown(self) -> None:
        """Stop serving and close the socket, as when the server process has gone."""
        if self.closed:
            return
        self.closed = True
        self.srv.shutdown()
        self.srv.server_close()
        for conn in self.conns:  # a browser's kept-alive connections die with the process too
            with contextlib.suppress(OSError):
                conn.shutdown(socket.SHUT_RDWR)
        self.thread.join(5)


@pytest.fixture(scope="session")
def browser():
    from playwright.sync_api import sync_playwright

    pw = sync_playwright().start()
    b = pw.chromium.launch()
    yield b
    b.close()
    pw.stop()


@pytest.fixture(params=list(SIZES), ids=list(SIZES))
def view(request, browser):
    w, h = SIZES[request.param]
    context = browser.new_context(viewport={"width": w, "height": h}, has_touch=request.param == "phone")
    p = context.new_page()
    errors = _ERRORS.setdefault(id(p), [])
    p.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
    p.on("pageerror", lambda e: errors.append(str(e)))
    yield p
    context.close()
    _ERRORS.pop(id(p), None)


@pytest.fixture
def workspace(tmp_path, bsa_home):
    ws = tmp_path / "My Shorts é"
    ws.mkdir()
    return ws


@pytest.fixture
def live_server(workspace):
    made: list[LiveServer] = []

    def start(stage: str) -> LiveServer:
        sdir = pagefix.make_short(workspace, stage)
        srv = server.make_server(workspace)
        thread = threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.1}, daemon=True)
        live = LiveServer(srv, sdir, thread)
        process = srv.process_request

        def track(request, client_address):
            live.conns.append(request)
            process(request, client_address)

        srv.process_request = track
        thread.start()
        made.append(live)
        return live

    yield start
    for live in made:
        live.shutdown()


def open_short(page, srv: LiveServer, stage: str | None = None):
    """Open the short's live page (on `stage` when given) and wait for its first render."""
    page.goto(srv.url + (f"#{stage}" if stage else ""))
    page.locator("html[data-ready='1']").wait_for(state="attached")
    return page


def assert_healthy(page) -> None:
    """No console errors, no horizontal scroll at phone size, and no serious or critical axe violations."""
    from axe_playwright_python.sync_playwright import Axe

    errors = _ERRORS.get(id(page), [])
    assert not errors, errors
    if page.viewport_size["width"] < 768:
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    results = Axe().run(page)
    bad = [v for v in results.response["violations"] if v.get("impact") in ("serious", "critical")]
    assert not bad, "\n".join(f"{v['id']}: {v['help']} {[n['target'] for n in v['nodes']]}" for v in bad)
