"""Memory guard for heavy Blender commands: a small watchdog process that stops a runaway Blender before it takes the
machine down (a camera test once grew to 98 GB on a 30 GB PC).

It runs out of process on purpose: a thread inside Blender may never get the interpreter lock while a C-level render
operator runs. `start()` (called by bsa_blender.py at the start of a heavy command) spawns it with Blender's own
Python. Every `POLL_S` it reads the parent's resident memory; over the limit it kills the parent (SIGKILL, or
TerminateProcess on Windows), says why on its own stderr (the parent's, inherited, so the user sees it) and marks the
command's live status as failed. It exits by itself as soon as the parent is gone, so it never outlives it.

The limit is the setting `render.memory_limit_gb` (GB). Unset: 60 % of this machine's RAM. `0` or `off`: no guard.
When the RAM is unknown and nothing is set, there is no guard (and it says so once on stderr).

The killed Blender's GPU lock (`<workspace>/.gpu.lock`) needs no release: its holder's pid is gone, so the next
command clears it as stale (bsa.gpulock). Standard library only; no bpy.
"""

from __future__ import annotations

import contextlib
import math
import os
import signal
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path

POLL_S = 2.0
DEFAULT_SHARE = 0.6  # of the machine's RAM, when render.memory_limit_gb is not set
CAUSES = ("Common causes: Freestyle over many frames, live Boolean modifiers, many copied meshes instead of one "
          "shared mesh.")  # fmt: skip
GB = 1024**3
_WINDOWS = sys.platform == "win32"
_MAC = sys.platform == "darwin"


# --------------------------------------------------------------------------- the limit


def parse_limit(value) -> float | None:
    """A `render.memory_limit_gb` value as stored: GB as a float, `0` for off. ValueError for anything else.
    None (unset) stays None."""
    if value is None:
        return None
    if isinstance(value, str) and value.strip().lower() == "off":
        return 0.0
    try:
        gb = float(value)
    except (TypeError, ValueError):
        gb = float("nan")
    if isinstance(value, bool) or not math.isfinite(gb) or gb < 0:
        raise ValueError(f"{value!r} is not a valid render.memory_limit_gb: use a number of GB (e.g. 18), or 0 or off "
                         "for no memory guard")  # fmt: skip
    return gb


def resolve_limit(settings: dict, ram_gb: float | None) -> tuple[float | None, str | None]:
    """(limit in GB or None for no guard, a note to say once on stderr or None). A bad stored value counts as unset."""
    try:
        value = parse_limit((settings.get("render") or {}).get("memory_limit_gb"))
    except ValueError:
        value = None
    if value is not None:
        return (value or None), None
    if not ram_gb:
        return None, ("memory guard off: this machine's RAM is unknown. Set render.memory_limit_gb to turn it on")
    return round(ram_gb * DEFAULT_SHARE, 1), None


def message(short: str, command: str, used_gb: float, limit_gb: float) -> str:
    return (f"stopped {short} {command}: Blender used {used_gb:.1f} GB, over the {limit_gb:.1f} GB limit "
            f"(render.memory_limit_gb). {CAUSES}")  # fmt: skip


# --------------------------------------------------------------------------- a process's resident memory


def parse_proc_status(text: str) -> int | None:
    """Bytes from the VmRSS line of /proc/<pid>/status (in kB); None when there is none (a zombie or kernel task)."""
    for line in text.splitlines():
        if line.startswith("VmRSS:"):
            parts = line.split()
            if len(parts) >= 2 and parts[1].isdigit():
                return int(parts[1]) * 1024
    return None


def parse_ps(out: str) -> int | None:
    """Bytes from `ps -o rss= -p PID` output (kB); None when empty."""
    text = out.strip().split()
    return int(text[0]) * 1024 if text and text[0].isdigit() else None


def _windows_rss(pid: int) -> int | None:
    import ctypes
    from ctypes import wintypes

    class Counters(ctypes.Structure):
        _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                    ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                    ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                    ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t)]  # fmt: skip

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.OpenProcess.restype = wintypes.HANDLE
    k32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    k32.K32GetProcessMemoryInfo.argtypes = (wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD)
    k32.GetExitCodeProcess.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
    k32.CloseHandle.argtypes = (wintypes.HANDLE,)  # a 64-bit handle must not be passed as a C int
    handle = k32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return None
    try:
        code = wintypes.DWORD()
        if not k32.GetExitCodeProcess(handle, ctypes.byref(code)) or code.value != 259:  # STILL_ACTIVE
            return None
        c = Counters()
        c.cb = ctypes.sizeof(Counters)
        if not k32.K32GetProcessMemoryInfo(handle, ctypes.byref(c), c.cb):
            return None
        return int(c.WorkingSetSize)
    finally:
        k32.CloseHandle(handle)


def rss_bytes(pid: int) -> int | None:
    """Resident memory of process `pid` in bytes, None when it is gone (or can not be read)."""
    try:
        if _WINDOWS:
            return _windows_rss(pid)
        if _MAC:
            from bsa import proc

            out = proc.run(["ps", "-o", "rss=", "-p", str(pid)], capture_output=True, text=True, timeout=5,
                           check=False)  # fmt: skip
            return parse_ps(out.stdout) if out.returncode == 0 else None
        return parse_proc_status(Path(f"/proc/{pid}/status").read_text())
    except Exception:  # noqa: BLE001 - a read that fails counts as "gone"; the watchdog then ends
        return None


# --------------------------------------------------------------------------- stopping the parent


def kill(pid: int) -> None:
    if _WINDOWS:
        import ctypes

        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.OpenProcess.restype = ctypes.c_void_p
        handle = k32.OpenProcess(0x0001, False, pid)  # PROCESS_TERMINATE
        if handle:
            k32.TerminateProcess(ctypes.c_void_p(handle), 1)
            k32.CloseHandle(ctypes.c_void_p(handle))
        return
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def _parent_gone(pid: int) -> bool:
    """POSIX: a process whose parent ended is adopted by init, so the watched pid stops being our parent."""
    return os.name == "posix" and os.getppid() != pid


def watch(pid: int, limit_gb: float, *, short: str, command: str, status: Path | None = None,
          poll_s: float = POLL_S, rss: Callable[[int], int | None] = rss_bytes,
          kill_fn: Callable[[int], None] = kill, sleep: Callable[[float], object] = time.sleep,
          err=None, check_parent: bool = True) -> tuple[bool, int]:  # fmt: skip
    """Poll `pid` until it is gone (normal end) or over `limit_gb`. Returns (killed, peak bytes seen)."""
    err = err or sys.stderr
    limit = limit_gb * GB
    peak = 0
    while True:
        used = rss(pid)
        if used is None or (check_parent and _parent_gone(pid)):
            return False, peak
        peak = max(peak, used)
        if used > limit:
            text = message(short, command, used / GB, limit_gb)
            kill_fn(pid)
            print(text, file=err, flush=True)
            if status is not None:
                try:
                    from bsa import render_status as rs

                    rs.fail_killed(status, pid, text)
                except Exception as exc:  # noqa: BLE001 - the status is a courtesy; the kill is done
                    print(f"status: {type(exc).__name__}: {exc}", file=err, flush=True)
            return True, peak
        sleep(poll_s)


def main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(prog="bsa.memwatch")
    ap.add_argument("--pid", type=int, required=True)
    ap.add_argument("--limit-gb", type=float, required=True)
    ap.add_argument("--short", default="?")
    ap.add_argument("--command", default="?")
    ap.add_argument("--status", default=None)
    ap.add_argument("--poll", type=float, default=POLL_S)
    a = ap.parse_args(argv)
    watch(a.pid, a.limit_gb, short=a.short, command=a.command, poll_s=a.poll,
          status=Path(a.status) if a.status else None)  # fmt: skip
    return 0


# --------------------------------------------------------------------------- starting it (from bsa_blender.py)

_BOOT = "import sys; sys.path.insert(0, sys.argv[1]); from bsa import memwatch; sys.exit(memwatch.main(sys.argv[2:]))"


def start(
    plugin_root: Path,
    *,
    short: str,
    command: str,
    status: Path | None,
    settings: dict,
    ram_gb: float | None,
    pid: int | None = None,
    python: str | None = None,
    poll_s: float = POLL_S,
):
    """Start the watchdog on this process (`pid`) for a heavy command. Returns its Popen, or None when there is no
    guard (off, or the RAM unknown: said once on stderr). Stop it with `stop()` when the command ends."""
    from bsa import proc

    limit, note = resolve_limit(settings, ram_gb)
    if note:
        print(note, file=sys.stderr, flush=True)
    if limit is None:
        return None
    cmd = [python or sys.executable, "-I", "-c", _BOOT, str(plugin_root), "--pid", str(pid or os.getpid()),
           "--limit-gb", repr(limit), "--short", short, "--command", command, "--poll", repr(poll_s)]  # fmt: skip
    if status is not None:
        cmd += ["--status", str(status)]
    # stderr is inherited, so the message reaches the user; stdout must not be (a pipe the command's reader waits on)
    return proc.popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL)


def stop(watchdog) -> None:
    """End the watchdog (a command that finished in a process that goes on, e.g. tests)."""
    if watchdog is None:
        return
    with contextlib.suppress(Exception):
        watchdog.terminate()
        watchdog.wait(timeout=5)
