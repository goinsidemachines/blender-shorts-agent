"""One GPU job at a time per workspace (<workspace>/.gpu.lock), across sessions, with stale locks cleared."""

import json
import os
import sys
import time

import pytest

from bsa import gpulock
from bsa import proc as proc_mod
from bsa import render_status as rs


@pytest.fixture
def other():
    """Another live process (a stand-in for a second session's Blender)."""
    proc = proc_mod.popen([sys.executable, "-c", "import time; time.sleep(60)"])
    yield proc
    proc.kill()
    proc.wait()


def _hold(path, proc, short="pendulum", command="render"):
    path.write_text(json.dumps({"pid": proc.pid, "pid_token": rs.process_token(proc.pid), "short": short,
                                "command": command, "started": 1_000_000.0}), encoding="utf-8")  # fmt: skip


def test_take_and_release(tmp_path):
    path = gpulock.lock_path(tmp_path)
    assert gpulock.acquire(path, short="clock", command="render")
    held = gpulock.read(path)
    assert held["pid"] == os.getpid() and held["short"] == "clock" and held["command"] == "render"
    assert gpulock.acquire(path, short="clock", command="extremes") is False  # re-entrant in one process
    gpulock.release(path)
    assert not path.exists()


def test_no_wait_fails_fast_naming_the_holder(tmp_path, other):
    path = gpulock.lock_path(tmp_path)
    _hold(path, other)
    with pytest.raises(gpulock.GpuBusy, match=rf"pendulum render \(pid {other.pid}"):
        gpulock.acquire(path, short="clock", command="render", wait=False)
    gpulock.release(path)  # not ours: left alone
    assert gpulock.read(path)["pid"] == other.pid


def test_a_waiter_says_what_it_waits_for_and_takes_over_when_the_holder_ends(tmp_path, other):
    path = gpulock.lock_path(tmp_path)
    _hold(path, other)
    said = []

    def sleep(_s):
        other.kill()
        other.wait()

    assert gpulock.acquire(path, short="clock", command="render", on_wait=said.append, sleep=sleep)
    assert said and said[0].startswith(f"waiting for pendulum render (pid {other.pid}")
    assert gpulock.read(path)["pid"] == os.getpid()


def test_a_dead_holders_lock_is_cleared_at_once(tmp_path):
    path = gpulock.lock_path(tmp_path)
    proc = proc_mod.popen([sys.executable, "-c", "pass"])
    proc.wait()
    _hold(path, proc)
    with gpulock.held(path, short="clock", command="estimate", wait=False):
        assert gpulock.read(path)["command"] == "estimate"
    assert not path.exists()


def test_the_waiting_shows_in_the_live_status(tmp_path):
    path = rs.status_path(tmp_path, "final")
    rs.starting(path, short={"slug": "clock", "title": "Clock", "dir": str(tmp_path)}, preset="final", kind="render")
    rs.note_waiting(path, "waiting for pendulum render (pid 42, since 14:02:31)")
    d = rs.describe(rs.read_status(path))
    assert "waiting for the GPU: waiting for pendulum render (pid 42" not in d["summary"]
    assert "final render waiting for the GPU: pendulum render (pid 42, since 14:02:31)" in d["summary"]
    rs.note_waiting(path, None)
    assert "waiting" not in rs.describe(rs.read_status(path))["summary"]


def test_heavy_commands_take_no_wait():
    import bsa_blender

    p = bsa_blender.build_parser()
    for argv in (["render", "x", "--preset", "final", "--no-wait"], ["preview", "x", "--no-wait"],
                 ["extremes", "x", "--no-wait"], ["estimate", "x", "--no-wait"],
                 ["stills", "x", "--no-wait"]):  # fmt: skip
        assert p.parse_args(argv).no_wait is True
    assert bsa_blender.GPU_COMMANDS >= {"render", "extremes", "estimate", "test-frames", "stills"}


def test_the_wait_says_who_holds_it_every_30_seconds():
    text = "waiting for watch render (pid 1234, since 14:02:31)"
    now = [0.0]
    beat = gpulock.Heartbeat(clock=lambda: now[0])
    got = []
    for t in range(0, 66, 2):
        now[0] = float(t)
        line = beat.line(text)
        if line is not None:
            got.append((t, line))
    assert [t for t, _ in got] == [0, 30, 60]
    assert got[0][1] == text
    assert got[1][1] == f"still {text}: waited 0 min 30 s"
    assert got[2][1] == f"still {text}: waited 1 min 0 s"


def test_the_beat_stays_steady_when_polls_come_late():
    """A poll that comes 3 s late does not push every later line 3 s back."""
    now = [0.0]
    beat = gpulock.Heartbeat(clock=lambda: now[0])
    got = []
    for t in (0, 33, 61, 64, 90):
        now[0] = float(t)
        if beat.line("waiting") is not None:
            got.append(t)
    assert got == [0, 33, 61, 90]
    assert gpulock.Heartbeat()._clock is time.monotonic


def test_every_wait_line_says_which_job_waits(tmp_path, monkeypatch, capsys):
    """The first line and each heartbeat after it start with the short and command that wait."""
    import argparse

    import bsa_blender

    calls = []

    def acquire(path, *, short, command, wait=True, on_wait=None, **kw):
        for _ in range(3):
            on_wait("waiting for watch render (pid 1234, since 14:02:31)")
        calls.append((short, command))
        return False

    now = [0.0]

    def clock():
        now[0] += 15.0
        return now[0]

    monkeypatch.setattr(gpulock, "acquire", acquire)
    real = gpulock.Heartbeat
    monkeypatch.setattr(gpulock, "Heartbeat", lambda: real(clock=clock))
    sdir = tmp_path / "ws" / "pendulum"
    sdir.mkdir(parents=True)
    with bsa_blender._gpu(argparse.Namespace(no_wait=False), sdir, "pendulum", "render"):
        pass
    err = [line for line in capsys.readouterr().err.splitlines() if line]
    assert calls == [("pendulum", "render")]
    assert err == ["pendulum render is waiting for watch render (pid 1234, since 14:02:31)",
                   "pendulum render is still waiting for watch render (pid 1234, since 14:02:31): waited 0 min 30 s"]


def test_test_frames_arguments():
    import argparse

    import bsa_blender

    args = bsa_blender.build_parser().parse_args(["test-frames", "x", "--at", "s01:0.5", "s05:0.3", "--no-wait"])
    assert args.at == [("s01", 0.5), ("s05", 0.3)] and args.scale == 0.4 and args.no_wait is True
    assert bsa_blender.build_parser().parse_args(["test-frames", "x", "--at", "a:b:1", "--scale", "0.25"]).at == [
        ("a:b", 1.0)]  # fmt: skip
    for bad in ("s01", "s01:1.5", ":0.5", "s01:half"):
        with pytest.raises(argparse.ArgumentTypeError):
            bsa_blender._at_spec(bad)


def test_style_sample_takes_the_workspace_gpu_lock(tmp_path, monkeypatch):
    """style-sample renders (one Blender job per style): it holds the workspace's GPU lock like the other heavy
    commands, though it has no short; without a workspace it runs unlocked."""
    import bsa_blender

    home, ws = tmp_path / "home", tmp_path / "ws"
    home.mkdir()
    ws.mkdir()
    monkeypatch.setenv("BSA_HOME", str(home))
    seen = []
    monkeypatch.setattr(bsa_blender, "cmd_style_sample", lambda args: seen.append(gpulock.read(gpulock.lock_path(ws)))
                        or {"ok": True})  # fmt: skip
    assert "style-sample" in bsa_blender.GPU_COMMANDS
    argv = ["style-sample", "--engine", "eevee", "--out", str(tmp_path / "x.png")]
    (home / "settings.json").write_text(json.dumps({"workspace": str(ws)}), encoding="utf-8")
    assert bsa_blender.main(argv) == 0
    assert seen[-1] and seen[-1]["pid"] == os.getpid() and seen[-1]["command"] == "style-sample"
    assert not gpulock.lock_path(ws).exists()  # released
    (home / "settings.json").write_text("{}", encoding="utf-8")
    assert bsa_blender.main(argv) == 0 and len(seen) == 2


def test_the_wait_line_shows_the_holders_memory_and_run_time(tmp_path, other, monkeypatch):
    from bsa import memwatch

    path = gpulock.lock_path(tmp_path)
    _hold(path, other)
    monkeypatch.setattr(memwatch, "rss_bytes", lambda pid: int(12.34 * memwatch.GB))
    monkeypatch.setattr(gpulock, "_children_rss", lambda pid: 0)
    monkeypatch.setattr(gpulock.time, "time", lambda: 1_000_000.0 + 2 * 3600 + 5 * 60)
    said = []
    with pytest.raises(gpulock.GpuBusy):
        gpulock.acquire(path, short="clock", command="render", on_wait=said.append, sleep=lambda s: None,
                        max_wait_s=0.0)  # looks once, says so, gives up
    assert said == [f"waiting for pendulum render (pid {other.pid}, since {time.strftime('%H:%M:%S', time.localtime(1_000_000.0))}), using 12.3 GB, running 2 h 5 min"]


def test_the_holders_usage_leaves_out_what_it_cannot_read(monkeypatch):
    from bsa import memwatch

    monkeypatch.setattr(memwatch, "rss_bytes", lambda pid: None)
    assert gpulock.holder_usage({"pid": 1, "started": 100.0}, now=160.0) == ", running 1 min"
    assert gpulock.holder_usage({"pid": 1}) == ""


def test_a_holders_child_processes_count_towards_its_memory():
    code = "import subprocess, sys, time; subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)']); time.sleep(30)"
    parent = proc_mod.popen([sys.executable, "-c", code])
    try:
        time.sleep(1.0)
        if sys.platform.startswith("linux"):
            assert gpulock._children_rss(parent.pid) > 0
    finally:
        parent.kill()
        parent.wait()


def test_max_wait_gives_up_naming_the_holder(tmp_path, other):
    path = gpulock.lock_path(tmp_path)
    _hold(path, other)
    now = [0.0]

    def sleep(s):
        now[0] += s

    with pytest.raises(gpulock.GpuBusy, match=rf"gave up waiting 0\.5 min for the GPU: pendulum render \(pid {other.pid}"):
        gpulock.acquire(path, short="clock", command="render", sleep=sleep, monotonic=lambda: now[0], max_wait_s=30)
    assert now[0] >= 30 and gpulock.read(path)["pid"] == other.pid  # waited the 30 s first; the lock is untouched


def test_no_max_wait_waits_as_long_as_it_takes(tmp_path, other):
    path = gpulock.lock_path(tmp_path)
    _hold(path, other)
    now = [0.0]
    polls = []

    def sleep(s):
        now[0] += 3600
        polls.append(1)
        if len(polls) == 5:  # five hours later the holder ends
            other.kill()
            other.wait()

    assert gpulock.acquire(path, short="clock", command="render", sleep=sleep, monotonic=lambda: now[0])


def test_max_wait_is_a_gpu_command_option():
    import bsa_blender

    p = bsa_blender.build_parser()
    for argv in (["render", "x", "--preset", "final"], ["preview", "x"], ["stills", "x"], ["estimate", "x"],
                 ["extremes", "x"]):
        assert p.parse_args([*argv, "--max-wait", "90"]).max_wait == 90.0
        assert p.parse_args(argv).max_wait is None
    with pytest.raises(SystemExit):
        p.parse_args(["render", "x", "--preset", "final", "--max-wait", "0"])


def test_gpu_gives_up_with_a_plain_error(tmp_path, other, monkeypatch):
    import types

    import bsa_blender

    sdir = tmp_path / "pendulum"
    sdir.mkdir()
    _hold(gpulock.lock_path(tmp_path), other)
    monkeypatch.setattr(gpulock.time, "sleep", lambda s: None)
    monkeypatch.setattr(gpulock, "POLL_S", 0)
    args = types.SimpleNamespace(no_wait=False, max_wait=0.0001)
    with (pytest.raises(bsa_blender.CliError, match=r"gave up waiting .* pendulum render"),
          bsa_blender._gpu(args, sdir, "clock", "render")):
        pytest.fail("took the lock")
