"""bsa.memwatch: the memory guard stops a runaway Blender with a plain message, and ends with it."""

import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

from bsa import gpulock, memwatch, settings
from bsa import proc as proc_mod
from bsa import render_status as rs

ROOT = Path(__file__).resolve().parent.parent
GB = memwatch.GB


# ---- reading a process's resident memory


def test_parse_proc_status_reads_vmrss_in_kb():
    text = "Name:\tblender\nVmPeak:\t 9000 kB\nVmRSS:\t  2048 kB\nThreads:\t4\n"
    assert memwatch.parse_proc_status(text) == 2048 * 1024


def test_parse_proc_status_without_vmrss_is_gone():
    assert memwatch.parse_proc_status("Name:\tblender\nState:\tZ (zombie)\n") is None


def test_parse_ps_output():
    assert memwatch.parse_ps("  4096\n") == 4096 * 1024
    assert memwatch.parse_ps("") is None


def test_rss_on_macos_uses_ps(monkeypatch):
    seen = []

    def fake_run(cmd, **kw):
        seen.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, " 1000\n", "")

    monkeypatch.setattr(memwatch, "_WINDOWS", False)
    monkeypatch.setattr(memwatch, "_MAC", True)
    monkeypatch.setattr(proc_mod, "run", fake_run)
    assert memwatch.rss_bytes(42) == 1000 * 1024
    assert seen == [["ps", "-o", "rss=", "-p", "42"]]


def test_rss_on_macos_gone_process(monkeypatch):
    monkeypatch.setattr(memwatch, "_WINDOWS", False)
    monkeypatch.setattr(memwatch, "_MAC", True)
    monkeypatch.setattr(proc_mod, "run", lambda cmd, **kw: subprocess.CompletedProcess(cmd, 1, "", ""))
    assert memwatch.rss_bytes(42) is None


def test_rss_on_windows_uses_the_working_set(monkeypatch):
    monkeypatch.setattr(memwatch, "_WINDOWS", True)
    monkeypatch.setattr(memwatch, "_windows_rss", lambda pid: 7 * GB)
    assert memwatch.rss_bytes(5) == 7 * GB


def test_rss_of_this_process_on_linux():
    if not sys.platform.startswith("linux"):
        pytest.skip("reads /proc")
    import os

    assert memwatch.rss_bytes(os.getpid()) > 1024 * 1024
    assert memwatch.rss_bytes(2**22 + 12345) is None


# ---- the limit


def test_limit_from_settings():
    assert memwatch.resolve_limit({"render": {"memory_limit_gb": 18}}, 32.0) == (18.0, None)


def test_limit_defaults_to_60_percent_of_ram():
    assert memwatch.resolve_limit({"render": {"engine": "ask"}}, 30.0) == (18.0, None)


@pytest.mark.parametrize("off", [0, 0.0, "off", "OFF"])
def test_limit_can_be_turned_off(off):
    assert memwatch.resolve_limit({"render": {"memory_limit_gb": off}}, 30.0) == (None, None)


def test_unknown_ram_means_no_guard_and_says_so():
    limit, note = memwatch.resolve_limit({}, None)
    assert limit is None and "RAM is unknown" in note and "render.memory_limit_gb" in note


def test_unknown_ram_with_a_set_limit_still_guards():
    assert memwatch.resolve_limit({"render": {"memory_limit_gb": 10}}, None) == (10.0, None)


def test_setting_is_validated(tmp_path, monkeypatch):
    monkeypatch.setenv("BSA_HOME", str(tmp_path))
    assert settings.check_value("render.memory_limit_gb", "12.5") == 12.5
    assert settings.check_value("render.memory_limit_gb", "off") == 0.0
    assert settings.check_value("render.memory_limit_gb", 0) == 0.0
    for bad in (-1, "lots", True, float("nan"), "inf"):
        with pytest.raises(ValueError, match="render.memory_limit_gb"):
            settings.check_value("render.memory_limit_gb", bad)
    settings.set_value("render.memory_limit_gb", 20.0)
    assert settings.load_settings()["render"]["memory_limit_gb"] == 20.0


def test_message_is_plain():
    assert memwatch.message("pendulum", "camtest", 41.23, 18.0) == (
        "stopped pendulum camtest: Blender used 41.2 GB, over the 18.0 GB limit (render.memory_limit_gb). "
        "Common causes: Freestyle over many frames, live Boolean modifiers, many copied meshes instead of one "
        "shared mesh."
    )


# ---- the watch loop (no processes)


def test_watch_kills_over_the_limit_and_fails_the_status(tmp_path, capsys):
    status = tmp_path / "render-status-preview.json"
    status.write_text(json.dumps({"state": "running", "pid": 77, "started": 1.0, "run": {"steps": [
        {"name": "render", "state": "running"}]}}), encoding="utf-8")  # fmt: skip
    killed, reads = [], iter([1 * GB, 3 * GB])
    got = memwatch.watch(77, 2.0, short="clock", command="render", status=status, poll_s=0, sleep=lambda s: None,
                         rss=lambda pid: next(reads), kill_fn=killed.append, check_parent=False)  # fmt: skip
    assert got == (True, 3 * GB) and killed == [77]
    assert "stopped clock render: Blender used 3.0 GB, over the 2.0 GB limit" in capsys.readouterr().err
    data = rs.read_status(status)
    assert data["state"] == "failed" and "render.memory_limit_gb" in data["error"]
    assert data["run"]["steps"][0]["state"] == "failed"


def test_watch_ends_quietly_when_the_process_is_gone():
    reads = iter([GB, None])
    got = memwatch.watch(9, 5.0, short="s", command="check", poll_s=0, sleep=lambda s: None,
                         rss=lambda pid: next(reads), kill_fn=lambda pid: pytest.fail("killed"), check_parent=False)  # fmt: skip
    assert got == (False, GB)


def test_fail_killed_leaves_other_runs_alone(tmp_path):
    path = tmp_path / "s.json"
    path.write_text(json.dumps({"state": "running", "pid": 1}), encoding="utf-8")
    assert rs.fail_killed(path, 2, "x") is False
    assert rs.read_status(path)["state"] == "running"


# ---- real processes (no Blender)


def _wait(p, seconds=20):
    deadline = time.time() + seconds
    while p.poll() is None and time.time() < deadline:
        time.sleep(0.05)
    return p.poll()


def _child_code(limit, work):
    """A stand-in for Blender: it starts its own watchdog (as bsa_blender.py does), prints the watchdog's pid, then
    does `work`."""
    return ("import sys, time\n"
            f"sys.path.insert(0, {str(ROOT)!r})\n"
            "from bsa import memwatch\n"
            f"dog = memwatch.start({str(ROOT)!r}, short='clock', command='camtest', status=None, poll_s=0.2,\n"
            f"    settings={{'render': {{'memory_limit_gb': {limit}}}}}, ram_gb=None)\n"
            "print(dog.pid, flush=True)\n" + work)  # fmt: skip


def _run_child(code, tmp_path):
    err = (tmp_path / "err.txt").open("w")
    child = proc_mod.popen([sys.executable, "-c", code], stdout=subprocess.PIPE, stderr=err, text=True)
    err.close()
    return child, int(child.stdout.readline())


def _gone(pid, seconds=20):
    import os

    deadline = time.time() + seconds
    while time.time() < deadline:
        try:
            os.kill(pid, 0)
            with open(f"/proc/{pid}/status") as f:
                if "State:\tZ" in f.read():
                    return True
        except (ProcessLookupError, FileNotFoundError):
            return True
        time.sleep(0.05)
    return False


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="uses /proc")
def test_watchdog_kills_a_runaway_parent_and_exits(tmp_path):
    work = ("buf = bytearray(400 * 1024 * 1024)\n"
            "for i in range(0, len(buf), 4096): buf[i] = 1\n"
            "time.sleep(60)\n")  # fmt: skip
    child, dog_pid = _run_child(_child_code(0.2, work), tmp_path)
    try:
        assert _wait(child) == -9, "the runaway was not stopped"
        assert _gone(dog_pid), "the watchdog outlived its parent"
        err = (tmp_path / "err.txt").read_text()
        assert "stopped clock camtest: Blender used" in err and "over the 0.2 GB limit (render.memory_limit_gb)" in err
    finally:
        child.kill()
        child.wait()


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="uses /proc")
def test_watchdog_exits_when_the_parent_ends_normally(tmp_path):
    child, dog_pid = _run_child(_child_code(5, "time.sleep(1)\n"), tmp_path)
    assert _wait(child) == 0
    assert _gone(dog_pid), "the watchdog outlived its parent"
    assert (tmp_path / "err.txt").read_text() == ""


def test_no_watchdog_when_off(capsys):
    assert memwatch.start(ROOT, short="s", command="render", status=None, settings={"render": {"memory_limit_gb": "off"}},
                          ram_gb=32.0) is None  # fmt: skip


# ---- the GPU lock after a kill


def test_a_waiter_gets_the_gpu_lock_after_the_holder_is_killed(tmp_path):
    holder = proc_mod.popen([sys.executable, "-c", "import time; time.sleep(60)"])
    path = gpulock.lock_path(tmp_path)
    path.write_text(json.dumps({"pid": holder.pid, "pid_token": rs.process_token(holder.pid), "short": "clock",
                                "command": "camtest", "started": 1_000_000.0}), encoding="utf-8")  # fmt: skip
    with pytest.raises(gpulock.GpuBusy):
        gpulock.acquire(path, short="other", command="render", wait=False)
    memwatch.kill(holder.pid)
    holder.wait()
    assert gpulock.acquire(path, short="other", command="render", wait=False) is True
    assert gpulock.read(path)["short"] == "other"
