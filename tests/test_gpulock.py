"""One GPU job at a time per workspace (<workspace>/.gpu.lock), across sessions, with stale locks cleared."""

import json
import os
import subprocess
import sys

import pytest

from bsa import gpulock
from bsa import render_status as rs


@pytest.fixture
def other():
    """Another live process (a stand-in for a second session's Blender)."""
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
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
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
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
                 ["extremes", "x", "--no-wait"], ["estimate", "x", "--no-wait"]):  # fmt: skip
        assert p.parse_args(argv).no_wait is True
    assert bsa_blender.GPU_COMMANDS >= {"render", "extremes", "estimate"}
