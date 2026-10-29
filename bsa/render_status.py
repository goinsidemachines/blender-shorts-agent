"""Live render status: while `BLENDER render` runs it keeps `review/render-status-<preset>.json` (and the page
`review/render-<preset>.html`) up to date, so anyone can see where a render is, how fast it goes and what the frame
checks found, without counting PNGs. A reader tells a dead render from a running one by its process (pid plus the
process's start time, so a reused pid is not mistaken for the render). Pure: stdlib + numpy-free; no bpy."""

from __future__ import annotations

import json
import os
import struct
import sys
import time
import zlib
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path
from typing import Self

from bsa.image_checks import Finding

VERSION = 1
_PNG_SIG = b"\x89PNG\r\n\x1a\n"
FINISHED = ("done", "failed", "stopped")
THROTTLE_S = 2.0  # at most one status write per this many seconds (shot changes, findings and the end always write)
CHECK_BUDGET = 0.02  # live frame checks may cost at most this share of the fresh frames' render time
RECENT = 20  # the ETA uses the mean time of the last RECENT fresh frames
MIN_MEASURED = 3  # below this many fresh frames the ETA comes from the estimate (when there is one)
MAX_FINDINGS = 50  # findings listed in the status (all are counted)
PREVIEW_OF_FINAL = 0.25  # the preview renders a quarter of the pixels: a rough seed from the final estimate
STALL_MIN_S = 120.0  # the page says "no update for a while" after max(this, 10 x the time per frame)


# --------------------------------------------------------------------------- paths


def status_path(short_dir: Path, preset: str) -> Path:
    return Path(short_dir) / "review" / f"render-status-{preset}.json"


def page_path(short_dir: Path, preset: str) -> Path:
    return Path(short_dir) / "review" / f"render-{preset}.html"


def thumb_rel(preset: str, shot: str) -> str:
    return f"render-status/{preset}-{shot}.jpg"


def thumb_path(short_dir: Path, preset: str, shot: str) -> Path:
    return Path(short_dir) / "review" / thumb_rel(preset, shot)


# --------------------------------------------------------------------------- files


def write_atomic(path: Path, text: str, tries: int = 8) -> bool:
    """Write text to a temporary file next to path, then replace path with it, so a reader never sees half a file.
    On Windows the replace fails while another program has path open: retry briefly, then give up (False); the next
    update writes it."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(text, encoding="utf-8")
    for i in range(tries):
        try:
            os.replace(tmp, path)
            return True
        except PermissionError:
            time.sleep(0.01 * (i + 1))
    tmp.unlink(missing_ok=True)
    return False


def read_status(path: Path) -> dict | None:
    for i in range(5):
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else None
        except FileNotFoundError:
            return None
        except (PermissionError, ValueError):
            time.sleep(0.02 * (i + 1))
    return None


# --------------------------------------------------------------------------- is the render's process alive?


def _windows_token(pid: int) -> str | None:
    import ctypes
    from ctypes import wintypes

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.OpenProcess.restype = wintypes.HANDLE
    k32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    handle = k32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return None
    try:
        code = wintypes.DWORD()
        if not k32.GetExitCodeProcess(handle, ctypes.byref(code)) or code.value != 259:  # STILL_ACTIVE
            return None
        times = [wintypes.FILETIME() for _ in range(4)]
        if not k32.GetProcessTimes(handle, *(ctypes.byref(t) for t in times)):
            return ""
        return str((times[0].dwHighDateTime << 32) | times[0].dwLowDateTime)
    finally:
        k32.CloseHandle(handle)


def process_token(pid: int | None) -> str | None:
    """None when no process `pid` is running; otherwise its start time as an opaque string ("" when this platform
    cannot say), so a pid reused by another program later does not look like the render."""
    if not isinstance(pid, int) or pid <= 0:
        return None
    try:
        if sys.platform == "win32":
            return _windows_token(pid)
        stat = Path(f"/proc/{pid}/stat")
        if stat.parent.parent.is_dir() and Path("/proc/self").exists():
            try:
                fields = stat.read_text().rsplit(")", 1)[1].split()
            except OSError:
                return None
            return None if fields[0] in ("Z", "X") else fields[19]
        os.kill(pid, 0)
        return ""
    except ProcessLookupError:
        return None
    except PermissionError:
        return ""  # it exists, owned by someone else
    except Exception:  # noqa: BLE001 - never let a status read fail on a platform quirk
        return ""


def is_alive(status: dict) -> bool:
    """The render that wrote `status` is still running (its process exists and is the same process)."""
    token = process_token(status.get("pid"))
    if token is None:
        return False
    want = status.get("pid_token")
    return not (want and token and want != token)


# --------------------------------------------------------------------------- planning a (resumed) render


def frames_plan(folder: Path, f0: int, f1: int, stamp: dict) -> dict:
    """What a render of frames f0..f1 into folder will keep and clear, as bsa.freshness.prepare_frames and
    render_range do: frames of the same shot content (fingerprint), range, engine and preset are kept, everything
    else (and empty placeholders) is stale and rendered again. "reason" says why a shot renders again ("fingerprint
    changed", "new range", "engine changed", "not rendered yet"), None when its frames are kept."""
    from bsa.freshness import frames_reason, read_frames_stamp

    folder = Path(folder)
    total = f1 - f0 + 1
    pngs = list(folder.glob("*.png")) if folder.is_dir() else []
    reason = frames_reason(read_frames_stamp(folder), stamp)
    if reason is not None:
        return {"total": total, "reused": 0, "stale": len(pngs), "reason": reason}
    wanted = {f"{f:04d}.png" for f in range(f0, f1 + 1)}
    sizes = {p.name: p.stat().st_size for p in pngs}
    reused = sum(1 for n, s in sizes.items() if n in wanted and s > 0)
    return {"total": total, "reused": reused, "stale": sum(1 for s in sizes.values() if s == 0),
            "reason": None if reused == total else "resuming"}  # fmt: skip


def seed_from_estimate(short_dir: Path, engine: str | None, preset: str) -> float | None:
    """Seconds per frame from `BLENDER estimate`'s review/estimate.json (final quality), or None."""
    if engine not in ("eevee", "cycles"):
        return None
    data = read_status(Path(short_dir) / "review" / "estimate.json") or {}
    frames, secs = data.get("frames"), data.get(f"{engine}_s")
    if not isinstance(frames, int) or frames <= 0 or not isinstance(secs, (int, float)) or secs <= 0:
        return None
    per = secs / frames
    return per if preset == "final" else per * PREVIEW_OF_FINAL


# --------------------------------------------------------------------------- stuck frames from the PNG data


def pixel_digest(png: Path) -> int | None:
    """CRC of a PNG's header and image data, leaving out metadata (Blender writes the frame number and render time
    into each file): two frames with the same digest have the same pixels. None if it can't be read."""
    try:
        data = Path(png).read_bytes()
    except OSError:
        return None
    if not data.startswith(_PNG_SIG):
        return None
    pos, crc = 8, 0
    while pos + 8 <= len(data):
        length, kind = struct.unpack(">I4s", data[pos : pos + 8])
        if kind in (b"IHDR", b"IDAT"):
            crc = zlib.crc32(data[pos + 8 : pos + 8 + length], crc)
        elif kind == b"IEND":
            break
        pos += 12 + length
    return crc


class FrameKey:
    """A rendered frame compared by its pixels, cheaply: by file size first (a stat, no read: reading a file just
    written costs milliseconds on Windows while it is scanned), by the image-data digest only on a tie. Blender's
    metadata has a fixed width, so the same pixels make the same file size."""

    __slots__ = ("_digest", "path", "size")

    def __init__(self, path: Path, size: int):
        self.path, self.size, self._digest = Path(path), size, None

    def digest(self) -> int | None:
        if self._digest is None:
            self._digest = pixel_digest(self.path)
        return self._digest

    def __eq__(self, other) -> bool:
        if not isinstance(other, FrameKey):
            return NotImplemented
        return self.size == other.size and self.digest() == other.digest()

    __hash__ = None


def frame_key(png: Path) -> FrameKey | None:
    try:
        return FrameKey(png, Path(png).stat().st_size)
    except OSError:
        return None


def stuck_from_digests(history: list[tuple[int, int]]) -> list[Finding]:
    """With the last four consecutive frames a, b, c, d: b == c while a != b and c != d -> c is stuck (the rule of
    bsa.image_checks.frame_checks, on digests instead of pixels)."""
    if len(history) < 4:
        return []
    last = history[-4:]
    numbers = [n for n, _ in last]
    if numbers != list(range(numbers[0], numbers[0] + 4)):
        return []
    a, b, c, d = (dg for _, dg in last)
    if b == c and a != b and c != d:
        return [Finding("stuck_frame", "warning", None, numbers[2], "frame identical to the previous one mid-motion")]
    return []


# --------------------------------------------------------------------------- the tracker (the render's side)


class Tracker:
    """The render's running status. The render calls start, begin_shot, frame_started / frame_written for each fresh
    frame, end_shot, and finish; writes are throttled to one per THROTTLE_S except at shot changes, new findings
    and the end. `render_page(status)` writes the HTML page after each JSON write."""

    def __init__(self, path: Path, *, short: dict, preset: str, engine: str | None, shots: list[dict],
                 stamp: dict, seed_s_per_frame: float | None = None, clock: Callable[[], float] = time.time,
                 render_page: Callable[[dict], object] | None = None, run: Run | None = None):  # fmt: skip
        self.path = Path(path)
        self.run = run  # an inert run (another live process owns the file) keeps this render from writing at all
        self.clock = clock
        self.render_page = render_page
        self.short, self.preset, self.engine, self.stamp = short, preset, engine, stamp
        self.seed = seed_s_per_frame
        self.settings: dict = {}
        self.shots = []
        for s in shots:
            total = s["f1"] - s["f0"] + 1
            self.shots.append({"id": s["id"], "f0": s["f0"], "f1": s["f1"], "total": total,
                               "done": s.get("reused", 0), "reused": s.get("reused", 0), "rendered": 0,
                               "stale": s.get("stale", 0), "current_frame": None, "state": "pending",
                               "thumb": None, "thumb_frame": None, "checked": 0, "warnings": 0,
                               "errors": 0, "reason": s.get("reason")})  # fmt: skip
        self._by_id = {s["id"]: s for s in self.shots}
        self.state, self.phase, self.error = "running", "starting", None
        self.started = self.updated = self.finished = None
        self.pid = os.getpid()
        self.pid_token = process_token(self.pid)
        self.current: dict | None = None
        self.durations: list[float] = []
        self.render_s = 0.0
        self.check_s = 0.0
        self.last_check_cost = 0.0
        self.frames_checked = 0
        self.checks_live = True  # False when this Blender can't hand frame pixels to the live checks
        self.findings: list[dict] = []
        self.n_errors = self.n_warnings = 0
        self.overhead_s = 0.0
        self._t_pre: float | None = None
        self._last_written: tuple[str, float] | None = None
        self._last_write = float("-inf")

    # ---- events

    def set_settings(self, settings: dict) -> None:
        self.settings.update(settings)

    def start(self) -> None:
        self.started = self.clock()
        self.phase = "rendering"
        self.write(force=True)

    def begin_shot(self, shot_id: str) -> None:
        shot = self._by_id[shot_id]
        shot["state"] = "rendering"
        self.current = {"shot": shot_id, "frame": None}
        self._last_written = None
        self.write(force=True)

    def frame_started(self, frame: int) -> None:
        self._t_pre = self.clock()

    def frame_written(self, frame: int) -> None:
        now = self.clock()
        shot_id = self.current["shot"] if self.current else None
        if self._last_written and self._last_written[0] == shot_id:
            since = self._last_written[1]
        else:
            since = self._t_pre if self._t_pre is not None else now
        dt = max(now - since, 0.0)
        self.durations.append(dt)
        self.render_s += dt
        self._last_written = (shot_id, now)
        self._t_pre = None
        first = False
        if shot_id is not None:
            shot = self._by_id[shot_id]
            shot["rendered"] += 1
            shot["done"] = min(shot["done"] + 1, shot["total"])
            shot["current_frame"] = frame
            self.current["frame"] = frame
            first = shot["rendered"] == 1
        self.write(force=first)

    def end_shot(self, shot_id: str) -> None:
        shot = self._by_id[shot_id]
        shot["state"], shot["done"] = "done", shot["total"]
        self.write(force=True)

    def set_phase(self, phase: str) -> None:
        self.phase = phase
        self.write(force=True)

    def finish(self, state: str, error: str | None = None) -> None:
        if state not in FINISHED:
            raise ValueError(f"unknown render state {state!r}; choose from {FINISHED}")
        self.state, self.error = state, error
        self.phase = "done" if state == "done" else state
        self.finished = self.clock()
        if self.current and state == "done":
            self.current = None
        self.write(force=True)

    # ---- frame checks

    def should_check(self) -> bool:
        """Check this frame? The first fresh frame always; then only while the checks' total cost stays within
        CHECK_BUDGET of the fresh frames' render time."""
        if self.frames_checked == 0:
            return True
        return self.check_s + self.last_check_cost <= CHECK_BUDGET * self.render_s

    def note_check(self, shot_id: str, seconds: float) -> None:
        self.frames_checked += 1
        self.check_s += seconds
        self.last_check_cost = seconds
        self._by_id[shot_id]["checked"] += 1

    def add_findings(self, shot_id: str, findings: list[Finding]) -> None:
        if not findings:
            return
        shot = self._by_id.get(shot_id)
        for f in findings:
            err = f.severity == "error"
            self.n_errors += err
            self.n_warnings += not err
            if shot is not None:
                shot["errors" if err else "warnings"] += 1
            if len(self.findings) < MAX_FINDINGS:
                self.findings.append({**asdict(f), "shot": shot_id})
        self.write(force=True)

    def set_thumb(self, shot_id: str, frame: int) -> None:
        shot = self._by_id[shot_id]
        shot["thumb"], shot["thumb_frame"] = thumb_rel(self.preset, shot_id), frame

    def add_overhead(self, seconds: float) -> None:
        self.overhead_s += seconds

    # ---- output

    def snapshot(self) -> dict:
        now = self.clock()
        total = sum(s["total"] for s in self.shots)
        done = sum(s["done"] for s in self.shots)
        reused = sum(s["reused"] for s in self.shots)
        rendered = sum(s["rendered"] for s in self.shots)
        stale = sum(s["stale"] for s in self.shots)
        recent = self.durations[-RECENT:]
        if len(self.durations) >= MIN_MEASURED:
            spf, source = sum(recent) / len(recent), "measured"
        elif self.seed:
            spf, source = self.seed, "estimate"
        else:
            spf, source = None, None
        remaining = total - done
        running = self.state == "running"
        eta = (remaining * spf if spf is not None else None) if running else 0.0
        end = self.finished if self.finished is not None else now
        elapsed = end - self.started if self.started is not None else 0.0
        return {
            "version": VERSION,
            "short": self.short,
            "preset": self.preset,
            "engine": self.engine,
            "state": self.state,
            "phase": self.phase,
            "error": self.error,
            "pid": self.pid,
            "pid_token": self.pid_token,
            "started": self.started,
            "updated": now,
            "finished": self.finished,
            "elapsed_s": round(elapsed, 2),
            "frames": {"total": total, "done": done, "reused": reused, "rendered": rendered, "stale": stale,
                       "to_render": total - reused},
            "percent": round(100.0 * done / total, 2) if total else 100.0,
            "current": self.current,
            "avg_s_per_frame": round(self.render_s / len(self.durations), 3) if self.durations else None,
            "recent_s_per_frame": round(sum(recent) / len(recent), 3) if recent else None,
            "seed_s_per_frame": self.seed,
            "eta_s": round(eta, 1) if eta is not None else None,
            "eta_source": source if running else None,
            "eta_at": now + eta if eta is not None and running else None,
            "stall_after_s": max(STALL_MIN_S, 10 * (spf or 0.0)),
            "shots": [dict(s) for s in self.shots],
            "settings": dict(self.settings),
            "checks": {"frames_checked": self.frames_checked, "errors": self.n_errors, "warnings": self.n_warnings,
                       "check_s": round(self.check_s, 3), "budget": CHECK_BUDGET, "live": self.checks_live,
                       "findings": list(self.findings)},
            "overhead_s": round(self.overhead_s, 3),
            "overhead_pct": round(100.0 * self.overhead_s / elapsed, 2) if elapsed > 0 else 0.0,
            "stamp": self.stamp,
            "throttle_s": THROTTLE_S,
        }

    def write(self, force: bool = False) -> None:
        now = self.clock()
        if not force and now - self._last_write < THROTTLE_S:
            return
        t0 = time.perf_counter()
        self._last_write = now
        status = self.snapshot()
        if self.run is not None:  # inside a run: the run's file holds this render as its "render" section
            self.run.set_render(status)
            self.overhead_s += time.perf_counter() - t0
            return
        write_atomic(self.path, json.dumps(status, indent=1, default=str))
        if self.render_page is not None:
            try:
                self.render_page(status)
            except Exception as exc:  # noqa: BLE001 - the page must never stop a render
                print(f"render status page: {exc}", file=sys.stderr)
        self.overhead_s += time.perf_counter() - t0


# --------------------------------------------------------------------------- runs: the preview chain or one step

RUN_VERSION = 2
CHAIN_STEPS = ("timeline", "build", "build done", "mix", "check", "render", "frame-check", "edit")
STEP_STATES = ("pending", "running", "done", "skipped", "failed", "stopped")
MAX_ERROR = 400  # characters of an error kept in the status

_ACTIVE: Run | None = None


def active() -> Run | None:
    """The run this process is recording (the preview chain while it runs), or None."""
    return _ACTIVE


def _owned_elsewhere(status: dict | None) -> bool:
    """`status` belongs to a run or render that is still going in another process."""
    if not status or status.get("state") != "running" or status.get("pid") == os.getpid():
        return False
    return is_alive(status)


def run_id(pid: int, started: float) -> str:
    """A run's id, "<pid>-<start, whole seconds>": the same from its "starting" status to its end."""
    return f"{pid}-{int(started)}"


def _outcome(status: dict | None, now: float) -> dict | None:
    """The short history a new run keeps of the one it supersedes: what it was, how it ended and when."""
    if not status or status.get("state") in (None, "none"):
        return None
    d = describe(status, now, ago=False)
    run = status.get("run") or {}
    state = "stopped" if d.get("stale") else status.get("state")
    return {"kind": status.get("kind") or run.get("kind") or "render", "state": state, "run_id": status.get("run_id"),
            "started": status.get("started"), "finished": status.get("finished") or status.get("updated"),
            "error": status.get("error"), "step": run.get("current") if state != "done" else None,
            "summary": d.get("summary")}  # fmt: skip


class Run:
    """One run recorded in review/render-status-<preset>.json: the preview chain (its steps pending, running, done,
    skipped, failed or stopped, with start and end times and a summary each) or a single step (`check`,
    `frame-check`, `edit`, `render` on its own). Starting a run supersedes what the file held (it keeps that run's
    outcome under "previous"), unless that is still running in another process: then this run records nothing.
    Use it as a context manager; `step(name)` is one too. A render inside the run hands its tracker's status to
    `set_render`, so the same file and page show the chain and the render."""

    def __init__(self, path: Path, *, short: dict, preset: str, kind: str, steps: list[str] | tuple[str, ...],
                 clock: Callable[[], float] = time.time,
                 render_page: Callable[[dict], object] | None = None):  # fmt: skip
        self.path = Path(path)
        self.short, self.preset, self.kind = short, preset, kind
        self.clock = clock
        self.render_page = render_page
        self.steps = [{"name": n, "state": "pending", "started": None, "finished": None, "summary": None,
                       "progress": None} for n in steps]  # fmt: skip
        self._by_name = {s["name"]: s for s in self.steps}
        self.state, self.error = "pending", None
        self.started = self.finished = None
        self.pid = os.getpid()
        self.pid_token = process_token(self.pid)
        self.previous: dict | None = None
        self.run_id: str | None = None
        self.render: dict | None = None
        self.inert = False
        self.current: str | None = None
        self._last_write = float("-inf")
        self._t0: float | None = None  # when the current step's progress started counting
        self._prev_active: Run | None = None

    # ---- lifecycle

    def start(self) -> None:
        global _ACTIVE
        old = read_status(self.path)
        self._prev_active, _ACTIVE = _ACTIVE, self
        if _owned_elsewhere(old):
            self.inert = True
            print(f"status: {self.path.name} belongs to a run still going in another process (pid {old.get('pid')});"
                  " this one is not recorded there", file=sys.stderr)  # fmt: skip
            return
        now = self.clock()
        if _own_starting(old):  # this process said it was starting: the run it supersedes is the one before that
            self.previous = old.get("previous")
            self.run_id = old.get("run_id") or run_id(self.pid, now)
        else:
            self.previous = _outcome(old, now)
            self.run_id = run_id(self.pid, now)
        self.started, self.state = now, "running"
        self.write(force=True)

    def finish(self, state: str, error: str | None = None) -> None:
        if state not in FINISHED:
            raise ValueError(f"unknown run state {state!r}; choose from {FINISHED}")
        self.state, self.error = state, (error or None) and str(error)[:MAX_ERROR]
        self.finished = self.clock()
        if state == "done":
            self.current = None
        elif self.current and self._by_name[self.current]["state"] == "running":  # it ended inside that step
            st = self._by_name[self.current]
            if st["progress"] is not None:
                st["progress"] = {**self._progress_out(st["progress"], self.finished), "eta_s": None, "eta_at": None}
            st["state"], st["finished"] = state, self.finished
            st.setdefault("error", self.error)
        self.write(force=True)

    def __enter__(self) -> Self:
        self.start()
        return self

    def __exit__(self, kind, exc, tb) -> bool:
        global _ACTIVE
        try:
            if self.state == "running":
                if exc is None:
                    self.finish("done")
                else:
                    self.finish(_ended(exc), error=_one_line(exc))
        finally:
            _ACTIVE = self._prev_active
        return False

    # ---- steps

    def _step(self, name: str) -> dict:
        if name not in self._by_name:  # a step the run did not plan (a chain that grew): add it at the end
            self._by_name[name] = {"name": name, "state": "pending", "started": None, "finished": None,
                                   "summary": None, "progress": None}  # fmt: skip
            self.steps.append(self._by_name[name])
        return self._by_name[name]

    def step(self, name: str):
        return _StepCtx(self, name)

    def begin(self, name: str) -> dict:
        st = self._step(name)
        st.update(state="running", started=self.clock(), finished=None, progress=None)
        self.current, self._t0 = name, None
        self.write(force=True)
        return st

    def end(self, name: str, state: str = "done", error: str | None = None) -> None:
        st = self._step(name)
        now = self.clock()
        if st["progress"] is not None and st["state"] == "running":  # frozen at its last count
            st["progress"] = {**self._progress_out(st["progress"], now), "eta_s": None, "eta_at": None}
        st["state"], st["finished"] = state, now
        if error:
            st["error"] = str(error)[:MAX_ERROR]
        if state == "done" and self.current == name:
            self.current = None
        self.write(force=True)

    def skip(self, name: str, why: str) -> None:
        st = self._step(name)
        st.update(state="skipped", summary={"skipped": why})
        self.write(force=True)

    def summarize(self, name: str, summary: dict) -> None:
        self._step(name)["summary"] = summary
        self.write(force=True)

    def progress(self, done: int, total: int, *, unit: str = "frames", current: str | None = None,
                 items: list[dict] | None = None, force: bool = False) -> None:  # fmt: skip
        """Progress of the current step: `done` of `total` units, what it is on now (`current`, e.g. "shot s02") and
        optionally a row per item (shot). Cheap to call per frame: it writes at most every THROTTLE_S, and at once
        when `current` changes or `force`."""
        if self.current is None or self.inert:
            return
        now = self.clock()
        if self._t0 is None:
            self._t0 = now
        st = self._by_name[self.current]
        p = st["progress"]
        if p is None:
            p = st["progress"] = {}
        changed = current != p.get("current")
        p.update(done=done, total=total, unit=unit, current=current)
        if items is not None:
            p["_items"] = items
        if force or changed or now - self._last_write >= THROTTLE_S:
            self.write(force=True)

    def _progress_out(self, p: dict, now: float) -> dict:
        done, total = p.get("done") or 0, p.get("total") or 0
        elapsed = max(now - self._t0, 0.0) if self._t0 is not None else 0.0
        per = elapsed / done if done >= MIN_MEASURED and elapsed > 0 else None
        eta = (total - done) * per if per is not None else None
        out = {k: v for k, v in p.items() if not k.startswith("_")}
        out.update(percent=round(100.0 * done / total, 2) if total else 100.0, elapsed_s=round(elapsed, 2),
                   s_per_unit=round(per, 4) if per is not None else None,
                   eta_s=round(eta, 1) if eta is not None else None, eta_at=now + eta if eta is not None else None)  # fmt: skip
        if "_items" in p:
            out["items"] = [dict(i) for i in p["_items"]]
        return out

    # ---- the render inside the run

    def set_render(self, status: dict) -> None:
        self.render = status
        self.write(force=True)

    # ---- output

    def snapshot(self) -> dict:
        now = self.clock()
        steps = []
        for st in self.steps:
            row = dict(st)
            if st["progress"] is not None and st["state"] == "running":
                row["progress"] = self._progress_out(st["progress"], now)
            steps.append(row)
        end = self.finished if self.finished is not None else now
        cur = self._by_name.get(self.current) if self.current else None
        stall = None
        if cur is not None and cur["name"] == "render" and self.render and self.render.get("state") == "running":
            stall = self.render.get("stall_after_s")
        elif cur is not None and cur.get("progress"):
            per = self._progress_out(cur["progress"], now).get("s_per_unit")
            stall = max(STALL_MIN_S, 10 * (per or 0.0))
        return {
            "version": RUN_VERSION,
            "short": self.short,
            "preset": self.preset,
            "kind": self.kind,
            "state": self.state,
            "phase": self.current or self.state,
            "error": self.error,
            "pid": self.pid,
            "pid_token": self.pid_token,
            "run_id": self.run_id,
            "started": self.started,
            "updated": now,
            "finished": self.finished,
            "elapsed_s": round(end - self.started, 2) if self.started is not None else 0.0,
            "run": {"kind": self.kind, "current": self.current, "steps": steps},
            "render": self.render,
            "previous": self.previous,
            "stall_after_s": stall,
            "throttle_s": THROTTLE_S,
        }

    def write(self, force: bool = False) -> None:
        if self.inert or self.state == "pending":
            return
        now = self.clock()
        if not force and now - self._last_write < THROTTLE_S:
            return
        self._last_write = now
        status = self.snapshot()
        write_atomic(self.path, json.dumps(status, indent=1, default=str))
        if self.render_page is not None:
            try:
                self.render_page(status)
            except Exception as exc:  # noqa: BLE001 - the page must never stop the work
                print(f"status page: {exc}", file=sys.stderr)


class _StepCtx:
    def __init__(self, run: Run, name: str):
        self.run, self.name = run, name

    def __enter__(self) -> dict:
        return self.run.begin(self.name)

    def __exit__(self, kind, exc, tb) -> bool:
        if exc is None:
            self.run.end(self.name)
        else:
            self.run.end(self.name, _ended(exc), error=_one_line(exc))
        return False


def _ended(exc: BaseException) -> str:
    return "stopped" if isinstance(exc, (KeyboardInterrupt, SystemExit)) else "failed"


def _one_line(exc: BaseException) -> str:
    text = " ".join(str(exc).split())
    return text or type(exc).__name__


def _own_starting(status: dict | None) -> bool:
    """`status` is the "starting" status this process wrote (starting()), not yet replaced by its run."""
    return bool(status) and status.get("phase") == "starting" and status.get("state") == "running" \
        and status.get("pid") == os.getpid()  # fmt: skip


def starting(path: Path, *, short: dict, preset: str, kind: str, clock: Callable[[], float] = time.time,
             render_page: Callable[[dict], object] | None = None) -> bool:  # fmt: skip
    """Say at once that a new run (`kind`: render, check, frame-check, edit or the preview chain) is starting in
    this process: the first thing a BLENDER command does, before Blender opens the .blend and before its run is
    set up, so render-status and the page never show the last run as the current one meanwhile. The run that
    follows (Run.start) replaces it and keeps the last run's outcome under "previous". Leaves the file alone (and
    returns False) when a run of another live process owns it."""
    old = read_status(path)
    if _owned_elsewhere(old):
        return False
    now = clock()
    steps = list(CHAIN_STEPS) if kind == "preview" else [kind]
    status = {
        "version": RUN_VERSION, "short": short, "preset": preset, "kind": kind, "state": "running",
        "phase": "starting", "error": None, "pid": os.getpid(), "pid_token": process_token(os.getpid()),
        "run_id": run_id(os.getpid(), now), "started": now, "updated": now, "finished": None, "elapsed_s": 0.0,
        "run": {"kind": kind, "current": None, "steps": [{"name": n, "state": "pending", "started": None,
                "finished": None, "summary": None, "progress": None} for n in steps]},
        "render": None, "previous": old.get("previous") if _own_starting(old) else _outcome(old, now),
        "stall_after_s": STALL_MIN_S, "throttle_s": THROTTLE_S,
    }  # fmt: skip
    path.parent.mkdir(parents=True, exist_ok=True)
    write_atomic(path, json.dumps(status, indent=1, default=str))
    if render_page is not None:
        try:
            render_page(status)
        except Exception as exc:  # noqa: BLE001 - the page must never stop the work
            print(f"status page: {exc}", file=sys.stderr)
    return True


def note_waiting(path: Path, text: str | None, clock: Callable[[], float] = time.time,
                 render_page: Callable[[dict], object] | None = None) -> None:  # fmt: skip
    """Say in this process's status that it waits for the GPU lock (`text`: "waiting for pendulum render (pid 1234,
    since 14:02:31)"), or no longer (None). A status another process wrote is left alone."""
    old = read_status(path)
    if not old or old.get("pid") != os.getpid():
        return
    status = dict(old)
    if text:
        status["waiting"] = str(text).removeprefix("waiting for ")
    else:
        status.pop("waiting", None)
    status["updated"] = clock()
    write_atomic(path, json.dumps(status, indent=1, default=str))
    if render_page is not None:
        try:
            render_page(status)
        except Exception as exc:  # noqa: BLE001 - the page must never stop the work
            print(f"status page: {exc}", file=sys.stderr)


def settle_starting(path: Path, error: str | None = None, clock: Callable[[], float] = time.time,
                    render_page: Callable[[dict], object] | None = None) -> None:  # fmt: skip
    """At the end of a command: if its "starting" status was never replaced by a run (it failed before, e.g. a
    stale build), end it as failed with `error` (or done without one). Anything else in the file is left alone."""
    old = read_status(path)
    if not _own_starting(old):
        return
    now = clock()
    status = {**old, "state": "failed" if error else "done", "phase": "failed" if error else "done",
              "error": (error or None) and str(error)[:MAX_ERROR], "finished": now, "updated": now,
              "elapsed_s": round(now - (old.get("started") or now), 2)}  # fmt: skip
    write_atomic(path, json.dumps(status, indent=1, default=str))
    if render_page is not None:
        try:
            render_page(status)
        except Exception as exc:  # noqa: BLE001
            print(f"status page: {exc}", file=sys.stderr)


@contextmanager
def recording(path: Path, *, short: dict, preset: str, step: str, render_page=None, clock=time.time):
    """The run a step records itself in: inside the preview chain (a run on the same file is active) the chain's
    own step, which the chain begins and ends; on its own a one-step run of kind `step`."""
    run = active()
    if run is not None and run.path == Path(path):
        yield run
        return
    run = Run(path, short=short, preset=preset, kind=step, steps=[step], clock=clock, render_page=render_page)
    with run, run.step(step):
        yield run


# --------------------------------------------------------------------------- reading it


def fmt_duration(seconds: float | None) -> str:
    if seconds is None:
        return "unknown"
    s = round(seconds)
    if s < 60:
        return f"{s} s"
    if s < 3600:
        m, s = divmod(s, 60)
        return f"{m} min {s} s" if s and m < 10 else f"{m} min"
    h, rest = divmod(s, 3600)
    return f"{h} h {rest // 60} min"


# fields of the render section a run's description also carries at the top, where readers of a render-only status
# found them
LIFTED = ("frames", "percent", "current", "eta_s", "eta_source", "eta_at", "avg_s_per_frame", "recent_s_per_frame",
          "seed_s_per_frame", "shots", "checks", "settings", "engine", "overhead_s", "overhead_pct", "stamp")  # fmt: skip


def again_command(kind: str | None, preset: str) -> str:
    """The command that runs `kind` again (the preview chain skips the steps already current)."""
    return {"preview": "BLENDER preview SHORT", "render": f"BLENDER render SHORT --preset {preset}",
            "check": "BLENDER check SHORT", "frame-check": f"BLENDER frame-check SHORT --preset {preset}",
            "edit": f"BLENDER edit SHORT --preset {preset}"}.get(kind or "", f"BLENDER {kind} SHORT")  # fmt: skip


def run_label(kind: str | None, preset: str) -> str:
    return {"preview": f"{preset} chain", "render": f"{preset} render"}.get(kind or "", str(kind))


def _step_text(step: dict | None, render: dict | None, now: float, named: bool = True) -> str:
    """What the current step is doing, in a few words: the render's own summary, a count with time left, or how
    long it has run."""
    if step is None:
        return ""
    name = step["name"]
    head = f"{name}: " if named else ""
    if name == "render" and render:
        text = _describe_render(render, now)["summary"]
        prefix = f"{render.get('preset', '?')} render running: "
        return head + text.removeprefix(prefix)
    p = step.get("progress")
    if p and p.get("total"):
        pct = 100.0 * (p.get("done") or 0) / p["total"]
        where = f", {p['current']}" if p.get("current") else ""
        left = f", about {fmt_duration(p['eta_s'])} left" if p.get("eta_s") is not None else ""
        return f"{head}{p.get('done', 0)} of {p['total']} {p.get('unit', 'frames')} ({pct:.0f}%){where}{left}"
    started = step.get("started")
    took = fmt_duration(now - started) if isinstance(started, (int, float)) else "a moment"
    return f"{head}running for {took}" if named else f"running for {took}"


def describe(status: dict, now: float | None = None, ago: bool = True) -> dict:
    """The status plus what a reader works out: `alive` (the process that writes it is running), `stale` (it says
    running but its process is gone: killed, crashed or the machine slept it away), `updated_ago_s` and a one-line
    `summary`. For a run (the preview chain or a single step) also `step` (the current step, or the one it ended
    at: name, index of total, state, progress), `idle` (nothing is running) and, when idle, `last_run` (how the
    run in the file ended and when); the render section's frames, shots, checks and settings are lifted to the top
    as a render-only status has them."""
    now = time.time() if now is None else now
    if "run" not in status:
        return _describe_render(status, now)
    out = dict(status)
    state = status.get("state")
    preset = status.get("preset", "?")
    kind = status.get("kind") or (status.get("run") or {}).get("kind")
    alive = state == "running" and is_alive(status)
    out["alive"], out["stale"], out["idle"] = alive, state == "running" and not alive, not alive
    updated = status.get("updated")
    out["updated_ago_s"] = round(now - updated, 2) if isinstance(updated, (int, float)) else None
    render = status.get("render") or None
    for k in LIFTED:
        if render and k in render and k not in out:
            out[k] = render[k]
    steps = (status.get("run") or {}).get("steps") or []
    name = (status.get("run") or {}).get("current")
    if name is None and state in ("failed", "stopped"):
        name = next((s["name"] for s in steps if s.get("state") in ("failed", "stopped")), None)
    idx = next((i for i, s in enumerate(steps) if s["name"] == name), None)
    step = None
    if idx is not None:
        s = steps[idx]
        started = s.get("started")
        step = {"name": s["name"], "index": idx + 1, "of": len(steps), "state": s.get("state"),
                "started": started, "elapsed_s": round(now - started, 2) if isinstance(started, (int, float))
                and s.get("state") == "running" else None, "progress": s.get("progress"),
                "summary": s.get("summary"), "error": s.get("error")}  # fmt: skip
    out["step"] = step
    label = run_label(kind, preset)
    again = again_command(kind, preset)
    finished = status.get("finished") or updated
    ago = f" ({fmt_duration(now - finished)} ago)" if ago and isinstance(finished, (int, float)) else ""
    if out["stale"]:
        during = f", during step {step['index']} of {step['of']} ({step['name']})" if step and len(steps) > 1 else ""
        resume = "; it resumes from what is on disk" if kind in ("preview", "render") else ""
        summary = (f"{label} stopped: its Blender process (pid {status.get('pid')}) is gone{during}; run {again} "
                   f"again{resume}")  # fmt: skip
    elif state == "running":
        if status.get("waiting"):
            summary = f"{label} waiting for the GPU: {status['waiting']}"
        elif status.get("phase") == "starting":
            took = fmt_duration(now - status["started"]) if isinstance(status.get("started"), (int, float)) else ""
            summary = (f"{label} starting: Blender (pid {status.get('pid')}) is opening the short"
                       + (f", for {took}" if took else ""))  # fmt: skip
        elif kind == "render" and step and step["name"] == "render" and render:
            summary = _describe_render(render, now)["summary"]
        elif len(steps) > 1 and step:
            summary = f"{label} running: step {step['index']} of {step['of']}, {_step_text(step, render, now)}"
        else:
            summary = f"{label} running: {_step_text(step, render, now, named=False)}"
    elif state == "done":
        took = fmt_duration((status.get("finished") or now) - (status.get("started") or now))
        if kind == "render" and render:
            summary = _describe_render(render, now)["summary"] + ago
        else:
            ran = sum(1 for s in steps if s.get("state") == "done")
            skipped = sum(1 for s in steps if s.get("state") == "skipped")
            parts = [f"{ran} step{'s' if ran != 1 else ''} run"] + ([f"{skipped} skipped"] if skipped else [])
            fr = (render or {}).get("frames") or {}
            if fr and len(steps) > 1:
                parts.append(f"render: {fr.get('rendered', 0)} frames rendered, {fr.get('reused', 0)} reused")
            summary = f"{label} done in {took}{ago}" + (f": {', '.join(parts)}" if len(steps) > 1 else "")
    elif state in ("failed", "stopped"):
        at = f" at {step['name']}" if step and len(steps) > 1 else ""
        why = f": {status['error']}" if status.get("error") else ""
        resume = " (steps already current are skipped)" if kind == "preview" else ""
        if kind == "render" and render:
            summary = _describe_render(render, now)["summary"]
        else:
            summary = f"{label} {state}{at}{why}{ago}; run {again} again{resume}"
    else:
        summary = f"no {preset} run has started for this short yet"
    started = status.get("started")
    out["run_id"] = status.get("run_id")
    out["started_at"] = time.strftime("%H:%M:%S", time.localtime(started)) if isinstance(started, (int, float))         else None  # fmt: skip
    if out["started_at"] and state not in (None, "none"):
        summary += f" (run started {out['started_at']})"
        if not alive:
            out["note"] = (f"This is the last run (started {out['started_at']}), and it has ended. If you just "
                           "launched a new one, no new run has written its status yet: it writes \"starting\" as "
                           "soon as Blender is up, within seconds. Check again shortly.")  # fmt: skip
    out["summary"] = summary
    if not alive and state not in (None, "none"):
        out["last_run"] = {"kind": kind, "state": "stopped" if out["stale"] else state,
                           "started": status.get("started"), "finished": finished, "error": status.get("error"),
                           "step": name if state != "done" else None, "summary": summary}  # fmt: skip
    return out


def _describe_render(status: dict, now: float | None = None) -> dict:
    """describe() for a render-only status (a render tracker's own file, or a run's render section)."""
    now = time.time() if now is None else now
    out = dict(status)
    state = status.get("state")
    preset = status.get("preset", "?")
    alive = state == "running" and is_alive(status)
    out["alive"] = alive
    out["stale"] = state == "running" and not alive
    updated = status.get("updated")
    out["updated_ago_s"] = round(now - updated, 2) if isinstance(updated, (int, float)) else None
    fr = status.get("frames") or {}
    done, total = fr.get("done", 0), fr.get("total", 0)
    checks = status.get("checks") or {}
    notes = []
    if checks.get("errors"):
        notes.append(f"{checks['errors']} frame error{'s' if checks['errors'] != 1 else ''}")
    if checks.get("warnings"):
        notes.append(f"{checks['warnings']} warning{'s' if checks['warnings'] != 1 else ''}")
    tail = f"; {', '.join(notes)}" if notes else ""
    again = f"run BLENDER render SHORT --preset {preset} again to resume from the frames on disk"
    if out["stale"]:
        summary = (f"{preset} render stopped: its Blender process (pid {status.get('pid')}) is gone, at {done} of "
                   f"{total} frames; {again}{tail}")  # fmt: skip
    elif state == "running":
        cur = status.get("current") or {}
        where = f", shot {cur['shot']} frame {cur.get('frame') or '-'}" if cur.get("shot") else ""
        if status.get("phase") == "anchors":
            where = ", placing labels (anchors) after the last frame"
        eta = status.get("eta_s")
        left = f", about {fmt_duration(eta)} left ({status.get('eta_source')})" if eta is not None else ""
        pct = 100.0 * done / total if total else 100.0
        summary = f"{preset} render running: {done} of {total} frames ({pct:.0f}%){where}{left}{tail}"
    elif state == "done":
        took = fmt_duration((status.get("finished") or now) - (status.get("started") or now))
        summary = (f"{preset} render done: {total} frames ({fr.get('rendered', 0)} rendered, {fr.get('reused', 0)} "
                   f"reused) in {took}{tail}")  # fmt: skip
    elif state in ("failed", "stopped"):
        why = f": {status['error']}" if status.get("error") else ""
        summary = f"{preset} render {state} at {done} of {total} frames{why}; {again}{tail}"
    else:
        summary = f"no {preset} render has started for this short yet"
    out["summary"] = summary
    return out
