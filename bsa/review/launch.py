"""Start or reuse the workspace's page server, open a short's page and confirm it checked in.

One server runs per workspace, detached from the command that started it, and names itself in
<workspace>/.page-server.json. That file is trusted only when its pid is still the same process (its process token)
and the port answers /api/health with its token, so a dead server, a reused pid or a port now held by another
program all lead to a fresh server, never to a dead link. When no server can start, the page is written as a static
file (review/short.html) and opened from disk; such a page can't check in, so it is never reported as opened."""

from __future__ import annotations

import contextlib
import http.client
import json
import os
import signal
import socket
import subprocess
import sys
import time
import webbrowser
from pathlib import Path
from urllib.parse import quote

from bsa import render_status
from bsa.review import history, page, server, snapshot, stages, store

SERVER_FILE = ".page-server.json"
ROOT = Path(__file__).resolve().parents[2]
_POLL_S = 0.1
_KEYS = {"pid": int, "port": int, "token": str, "process_token": str}


class ServerUnavailable(Exception):
    """The page server could not be started or reached."""


def _read_file(workspace: Path) -> dict | None:
    try:
        data = json.loads((Path(workspace) / SERVER_FILE).read_text("utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    for key, kind in _KEYS.items():
        if not isinstance(data.get(key), kind) or isinstance(data.get(key), bool):
            return None
    return data


def _request(info: dict, method: str, path: str, body: dict | None = None, timeout_s: float = 2.0) -> dict:
    """One API call to the server `info` names; the JSON reply. Raises OSError (or ValueError) on any failure."""
    port = info["port"]
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout_s)
    try:
        raw = None if body is None else json.dumps(body).encode("utf-8")
        headers = {server.TOKEN_HEADER: info["token"], "Host": f"127.0.0.1:{port}"}
        if raw is not None:
            headers["Content-Type"] = "application/json"
        conn.request(method, path, raw, headers)
        r = conn.getresponse()
        data = r.read()
    except (http.client.HTTPException, ValueError) as e:
        raise OSError(f"the page server gave a bad reply: {e}") from None
    finally:
        conn.close()
    try:
        reply = json.loads(data.decode("utf-8")) if data else {}
    except ValueError:
        reply = {}
    if r.status != 200:
        raise OSError(str(reply.get("error") if isinstance(reply, dict) else "") or f"HTTP {r.status}")
    return reply if isinstance(reply, dict) else {}


def server_info(workspace: Path, *, timeout_s: float = 1.0) -> dict | None:
    """The workspace's running server as SERVER_FILE names it, or None when that file is missing or damaged, its pid
    is gone or now another process, or its port doesn't answer /api/health with its token in time."""
    info = _read_file(workspace)
    if info is None or render_status.process_token(info["pid"]) != info["process_token"]:
        return None
    try:
        if _request(info, "GET", "/api/health", timeout_s=timeout_s).get("ok") is not True:
            return None
    except OSError:
        return None
    return info


def _spawn(workspace: Path, lan: bool):
    """Start `page serve` for the workspace, detached from this process and with no console or stdio."""
    cmd = [sys.executable, str(ROOT / "bsa_cli.py"), "page", "serve", "--workspace", str(workspace)]
    if lan:
        cmd.append("--lan")
    kw: dict = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL,
                "cwd": str(ROOT), "close_fds": True}  # fmt: skip
    if sys.platform == "win32":
        kw["creationflags"] = (
            subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
        )
    else:
        kw["start_new_session"] = True
    return subprocess.Popen(cmd, **kw)  # our own CLI, no shell


def _kill_tree(proc) -> None:
    """Stop a spawned server and its children (on Windows a venv's python.exe is a launcher with the real python as
    its child)."""
    with contextlib.suppress(OSError, subprocess.SubprocessError):
        if sys.platform == "win32":
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], check=False, capture_output=True,
                           timeout=10, creationflags=subprocess.CREATE_NO_WINDOW)  # fmt: skip
        else:
            os.killpg(proc.pid, signal.SIGKILL)  # start_new_session made it a group leader
    with contextlib.suppress(OSError):
        proc.kill()
    with contextlib.suppress(OSError, subprocess.SubprocessError):
        proc.wait(5)


def lan_ip() -> str | None:
    """This machine's address on the local network, or None. A UDP connect only picks the route: no packet is sent
    (192.0.2.1 is a documentation address that never answers)."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("192.0.2.1", 9))
            ip = s.getsockname()[0]
    except OSError:
        return None
    return None if not ip or ip.startswith(("0.", "127.")) else ip


def phone_url(info: dict, short_key: str) -> str | None:
    """The page's link for a phone on the same network, only when the server answers on the local network. The
    address is the one the server recorded (`lan_ip` in SERVER_FILE), the only LAN Host it admits: detecting it again
    here could give another address (a network change since it started) that the server would refuse."""
    ip = info.get("lan_ip")
    if not info.get("lan") or not isinstance(ip, str) or not ip:
        return None
    return page_url(info, short_key, host=ip)


def _stop(info: dict) -> None:
    """Stop the server `info` names (its pid is checked to still be that process first)."""
    pid = info["pid"]
    if render_status.process_token(pid) != info.get("process_token"):
        return
    with contextlib.suppress(OSError):
        os.kill(pid, signal.SIGTERM)  # TerminateProcess on Windows


def _same_lan(info: dict, lan: bool) -> bool:
    return bool(info.get("lan")) == bool(lan)


def ensure_server(workspace: Path, *, lan: bool = False, spawn=None, stop=None, timeout_s: float = 10.0) -> dict:
    """The workspace's valid server, starting one (detached) when there is none. A running server whose `lan` differs
    from the one asked for is stopped and started again, so turning phone access on (or off) works at once. Raises
    ServerUnavailable when none answers within timeout_s. A lock keeps two commands from starting two servers at
    once."""
    workspace = Path(workspace)
    info = server_info(workspace)
    if info is not None and _same_lan(info, lan):
        return info
    try:
        with store.locked(workspace / SERVER_FILE, timeout_s=timeout_s + 5):
            info = server_info(workspace)  # another command may have started one while we waited
            if info is not None and _same_lan(info, lan):
                return info
            if info is not None:
                _replace(workspace, info, stop or _stop, timeout_s)
            try:
                proc = (spawn or _spawn)(workspace, lan)
            except OSError as e:
                raise ServerUnavailable(f"the page server did not start: {e}") from None
            poll = getattr(proc, "poll", None)  # a Popen; a test's spawn may return anything
            deadline = time.monotonic() + timeout_s
            while True:
                info = server_info(workspace)
                if info is not None:
                    return info
                if poll is not None and poll() is not None:
                    raise ServerUnavailable(f"the page server stopped as it started (exit code {proc.returncode})")
                if time.monotonic() >= deadline:
                    if poll is not None:
                        _kill_tree(proc)  # never leave a server running that nobody can find
                    raise ServerUnavailable(f"the page server did not answer within {timeout_s:g}s")
                time.sleep(_POLL_S)
    except (TimeoutError, OSError) as e:  # the lock stayed held, or the workspace can't be written
        raise ServerUnavailable(f"the page server could not be started: {e}") from None


def _replace(workspace: Path, old: dict, stop, timeout_s: float) -> None:
    """Stop the server `old` names and wait until it no longer answers."""
    stop(old)
    deadline = time.monotonic() + timeout_s
    while True:
        now = server_info(workspace)
        if now is None or now["pid"] != old["pid"] or now["port"] != old["port"]:
            return
        if time.monotonic() >= deadline:
            raise ServerUnavailable("the running page server did not stop, so phone access could not be changed")
        time.sleep(_POLL_S)


def _write_atomic(path: Path, data: dict) -> None:
    """Write the file via a temp file and a rename. It holds the server's token, so on POSIX it is created 0600
    (only this user can read it)."""
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(json.dumps(data, indent=2))
    if os.name == "posix":
        os.chmod(tmp, 0o600)  # an existing temp file keeps its old mode through O_CREAT
    os.replace(tmp, path)


def run_server(workspace: Path, lan: bool) -> None:
    """The body of `page serve`: bind, name this server in SERVER_FILE, serve until idle, then remove the file if it
    still names this process."""
    workspace = Path(workspace)
    srv = server.make_server(workspace, lan=lan)
    pid = os.getpid()
    info = {"pid": pid, "port": srv.port, "token": srv.token, "process_token": render_status.process_token(pid) or "",
            "lan": lan, "lan_ip": srv.lan_ip, "bind": srv.server_address[0], "started": time.time()}  # fmt: skip
    path = workspace / SERVER_FILE
    try:
        _write_atomic(path, info)
        server.serve(srv)
    finally:
        srv.server_close()
        current = _read_file(workspace)
        if current is not None and current["pid"] == pid and current["port"] == info["port"]:
            with contextlib.suppress(OSError):
                path.unlink()


def page_url(info: dict, short_key: str, stage: str | None = None, host: str = "127.0.0.1") -> str:
    url = f"http://{host}:{info['port']}/s/{quote(short_key, safe='')}?t={quote(info['token'], safe='')}"
    return f"{url}#{stage}" if stage else url


def static_page(sdir: Path) -> Path:
    """Write the page as a file, review/short.html, with the snapshot embedded (served to a person: its history is
    kept, history.shown)."""
    path = Path(sdir) / "review" / "short.html"
    path.parent.mkdir(parents=True, exist_ok=True)
    html = page.render(mode="static", snapshot=history.shown(Path(sdir), snapshot.build(Path(sdir))))
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    tmp.write_text(html, encoding="utf-8")
    os.replace(tmp, path)
    return path


def _presence(info: dict, key: str, timeout_s: float = 2.0) -> tuple[float | None, float | None]:
    """(seconds since the page's last check-in of any kind, seconds since its last hello), None when never or
    when the server can't say."""
    try:
        reply = _request(info, "GET", f"/api/{quote(key, safe='')}/presence", timeout_s=timeout_s)
    except OSError:
        return None, None

    def num(v):
        return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None

    return num(reply.get("seen_s_ago")), num(reply.get("hello_s_ago"))


def _call_browser(browser, url: str) -> None:
    with contextlib.suppress(Exception):  # a browser that can't start is the same as a page that never checks in
        browser(url)


def open_page(workspace: Path, sdir: Path, *, stage: str | None = None, force: bool = False, browser=None,
              wait_s: float = 10.0, clock=time.time, sleep=time.sleep, ensure=ensure_server) -> dict:  # fmt: skip
    """Open the short's page and say whether it really opened: a tab that checked in within the last 30 s is left
    alone (it is switched to `stage`) unless `force`; otherwise the browser is called and a newly loaded page must
    say hello within wait_s (a state poll from a tab already open is no proof). Without a server the page is a static
    file, opened but never claimed as opened."""
    if stage is not None and stage not in stages.PAGE_STAGES:
        raise ValueError(f"unknown stage {stage!r}; expected one of {', '.join(stages.PAGE_STAGES)}")
    browser = browser or webbrowser.open
    sdir = Path(sdir)
    result = {"url": None, "opened": False, "already_open": False, "mode": "server", "stage": stage}
    try:
        info = ensure(Path(workspace))
    except ServerUnavailable:
        path = static_page(sdir)
        url = path.as_uri() + (f"#{stage}" if stage else "")
        _call_browser(browser, url)
        return {**result, "url": url, "mode": "static"}
    key = sdir.name
    result["url"] = page_url(info, key, stage)
    if stage is not None:
        with contextlib.suppress(OSError):  # a tab that misses the hint still opens at its own stage
            _request(info, "POST", f"/api/{quote(key, safe='')}/show", {"stage": stage})
    seen, _hello = _presence(info, key)
    if not force and seen is not None and seen < server.SEEN_WINDOW_S:
        return {**result, "opened": True, "already_open": True}
    called = clock()
    _call_browser(browser, result["url"])
    while True:
        left = wait_s - (clock() - called)
        _seen, hello = _presence(info, key, timeout_s=min(2.0, max(left, 0.05)))
        # only a hello after the browser call confirms it: a tab already open polls state, which is no proof
        if hello is not None and hello <= clock() - called:
            return {**result, "opened": True}
        if clock() - called >= wait_s:
            return result
        sleep(min(0.25, max(wait_s - (clock() - called), 0)))
