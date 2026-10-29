"""The live render status: what a render writes while it runs, how a reader tells a dead render from a running one,
the page that shows it, and CLI render-status."""

import json
import os
import struct
import subprocess
import sys
import zlib
from pathlib import Path

import pytest

from bsa import render_status as rs
from bsa.image_checks import Finding

ROOT = Path(__file__).resolve().parent.parent
CLI = ROOT / "bsa_cli.py"


class Clock:
    def __init__(self, t=1_000_000.0):
        self.t = t

    def __call__(self):
        return self.t

    def tick(self, dt):
        self.t += dt


def _tracker(tmp_path, clock, shots=None, seed=None, **kw):
    shots = shots or [{"id": "s01", "f0": 1, "f1": 10, "reused": 4, "stale": 0},
                      {"id": "s02", "f0": 11, "f1": 20, "reused": 0, "stale": 3}]  # fmt: skip
    pages = []
    t = rs.Tracker(
        tmp_path / "review" / "render-status-preview.json",
        short={"slug": "pendulum", "title": "Pendulum", "dir": str(tmp_path)},
        preset="preview",
        engine="eevee",
        shots=shots,
        stamp={"blend": 123, "timeline": "abc"},
        seed_s_per_frame=seed,
        clock=clock,
        render_page=lambda status: pages.append(status),
        **kw,
    )
    return t, pages


def _read(tmp_path, preset="preview"):
    return json.loads((tmp_path / "review" / f"render-status-{preset}.json").read_text(encoding="utf-8"))


def _render(t, clock, frames, secs=2.0):
    for f in frames:
        t.frame_started(f)
        clock.tick(secs)
        t.frame_written(f)


# --------------------------------------------------------------------------- tracker


def test_tracker_counts_reused_fresh_and_stale(tmp_path):
    clock = Clock()
    t, _ = _tracker(tmp_path, clock)
    t.start()
    s = _read(tmp_path)
    assert s["state"] == "running" and s["preset"] == "preview" and s["pid"] == os.getpid()
    assert s["frames"] == {"total": 20, "done": 4, "reused": 4, "rendered": 0, "stale": 3, "to_render": 16}
    assert [sh["state"] for sh in s["shots"]] == ["pending", "pending"]
    t.begin_shot("s01")
    _render(t, clock, [5, 6, 7])
    s = t.snapshot()
    one = s["shots"][0]
    assert (one["done"], one["rendered"], one["reused"], one["current_frame"], one["state"]) == (7, 3, 4, 7, "rendering")
    assert s["current"] == {"shot": "s01", "frame": 7}
    assert s["frames"]["done"] == 7 and s["frames"]["rendered"] == 3
    assert s["percent"] == pytest.approx(35.0)
    assert s["avg_s_per_frame"] == pytest.approx(2.0)


def test_eta_comes_from_the_estimate_until_frames_are_measured(tmp_path):
    clock = Clock()
    t, _ = _tracker(tmp_path, clock, seed=5.0)
    t.start()
    assert t.snapshot()["eta_source"] == "estimate"
    assert t.snapshot()["eta_s"] == pytest.approx(16 * 5.0)
    t.begin_shot("s01")
    _render(t, clock, [5, 6, 7], secs=1.0)
    s = t.snapshot()
    assert s["eta_source"] == "measured"
    assert s["eta_s"] == pytest.approx(13 * 1.0)
    assert s["eta_at"] == pytest.approx(clock.t + 13.0)


def test_no_eta_without_an_estimate_or_measured_frames(tmp_path):
    clock = Clock()
    t, _ = _tracker(tmp_path, clock)
    t.start()
    s = t.snapshot()
    assert s["eta_s"] is None and s["eta_source"] is None


def test_writes_are_throttled_but_shot_changes_and_the_end_always_write(tmp_path):
    clock = Clock()
    t, pages = _tracker(tmp_path, clock)
    t.start()
    n = len(pages)
    t.begin_shot("s01")
    assert len(pages) == n + 1  # a shot starting always writes
    t.frame_started(5)
    clock.tick(0.1)
    t.frame_written(5)  # the first fresh frame always writes
    assert len(pages) == n + 2
    for f in (6, 7, 8):
        t.frame_started(f)
        clock.tick(0.1)
        t.frame_written(f)
    assert len(pages) == n + 2  # throttled
    clock.tick(rs.THROTTLE_S)
    t.frame_started(9)
    t.frame_written(9)
    assert len(pages) == n + 3
    t.end_shot("s01")
    t.finish("done")
    s = _read(tmp_path)
    assert s["state"] == "done" and s["phase"] == "done" and s["finished"] == pytest.approx(clock.t)
    assert s["shots"][0]["state"] == "done"
    assert pages[-1]["state"] == "done"


def test_failed_and_stopped_keep_the_error(tmp_path):
    clock = Clock()
    t, _ = _tracker(tmp_path, clock)
    t.start()
    t.finish("failed", error="render left missing frames: 0007.png")
    s = _read(tmp_path)
    assert s["state"] == "failed" and "0007.png" in s["error"]
    with pytest.raises(ValueError):
        t.finish("exploded")


def test_findings_are_recorded_live_per_shot(tmp_path):
    clock = Clock()
    t, _ = _tracker(tmp_path, clock)
    t.start()
    t.begin_shot("s02")
    t.note_check("s02", 0.01)
    t.add_findings("s02", [Finding("black_frame", "error", None, 12, "frame is black (mean luminance 0.0010)")])
    s = _read(tmp_path)  # a finding writes at once
    assert s["checks"]["errors"] == 1 and s["checks"]["frames_checked"] == 1
    f = s["checks"]["findings"][0]
    assert (f["check"], f["shot"], f["frame"], f["severity"]) == ("black_frame", "s02", 12, "error")
    assert s["shots"][1]["errors"] == 1 and s["shots"][1]["checked"] == 1


def test_findings_list_is_capped(tmp_path):
    clock = Clock()
    t, _ = _tracker(tmp_path, clock)
    t.start()
    t.add_findings("s01", [Finding("fireflies", "warning", None, i, "30 firefly pixels") for i in range(200)])
    s = t.snapshot()
    assert s["checks"]["warnings"] == 200
    assert len(s["checks"]["findings"]) == rs.MAX_FINDINGS


def test_check_budget_keeps_overhead_small(tmp_path):
    clock = Clock()
    t, _ = _tracker(tmp_path, clock)
    t.start()
    t.begin_shot("s01")
    assert t.should_check()  # the first frame is always checked
    _render(t, clock, [5], secs=0.5)
    t.note_check("s01", 0.2)
    checks = 0
    for f in range(6, 11):
        _render(t, clock, [f], secs=0.5)
        if t.should_check():
            checks += 1
            t.note_check("s01", 0.2)
    # 3 s of rendering buys under 2% of 3 s = 0.06 s of checks: nothing more than the first
    assert checks == 0
    t.begin_shot("s02")
    for f in range(11, 21):
        _render(t, clock, [f], secs=2.0)
    assert t.should_check()  # 23 s of rendering now: 0.69 s of budget, 0.2 s spent


def test_overhead_is_reported(tmp_path):
    clock = Clock()
    t, _ = _tracker(tmp_path, clock)
    t.start()
    t.begin_shot("s01")
    _render(t, clock, [5, 6], secs=1.0)
    t.add_overhead(0.02)
    s = t.snapshot()
    assert s["overhead_s"] >= 0.02
    assert 0 < s["overhead_pct"] < 5


def test_thumbnails_are_relative_to_the_review_folder(tmp_path):
    clock = Clock()
    t, _ = _tracker(tmp_path, clock)
    t.start()
    t.set_thumb("s01", 7)
    sh = t.snapshot()["shots"][0]
    assert sh["thumb"] == "render-status/preview-s01.jpg" and sh["thumb_frame"] == 7
    assert rs.thumb_path(tmp_path, "preview", "s01") == tmp_path / "review" / "render-status" / "preview-s01.jpg"


def test_settings_are_merged(tmp_path):
    clock = Clock()
    t, _ = _tracker(tmp_path, clock)
    t.set_settings({"engine": "eevee", "resolution": [1080, 1920]})
    t.set_settings({"device": "GPU"})
    t.start()
    assert _read(tmp_path)["settings"] == {"engine": "eevee", "resolution": [1080, 1920], "device": "GPU"}


# --------------------------------------------------------------------------- planning a resumed render


def _png(path: Path, pixel=b"\x10\x20\x30", text=b"") -> None:
    raw = b"\x00" + pixel
    chunks = [(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))]
    if text:
        chunks.append((b"tEXt", text))
    chunks += [(b"IDAT", zlib.compress(raw)), (b"IEND", b"")]
    out = b"\x89PNG\r\n\x1a\n"
    for kind, body in chunks:
        out += struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(out)


def test_frames_plan_counts_what_a_render_keeps_and_clears(tmp_path):
    stamp = {"f0": 1, "f1": 5, "engine": "eevee", "preset": "preview", "fingerprint": "a"}
    folder = tmp_path / "s01"
    for f in (1, 2):
        _png(folder / f"{f:04d}.png")
    (folder / "0003.png").write_bytes(b"")  # an interrupted render's placeholder
    (folder / ".render.json").write_text(json.dumps(stamp), encoding="utf-8")
    assert rs.frames_plan(folder, 1, 5, stamp) == {"total": 5, "reused": 2, "stale": 1, "reason": "resuming"}
    assert rs.frames_plan(folder, 1, 5, {**stamp, "fingerprint": "b"}) == {"total": 5, "reused": 0, "stale": 3,
                                                                         "reason": "fingerprint changed"}
    assert rs.frames_plan(folder, 1, 6, {**stamp, "f1": 6})["reason"] == "new range"
    assert rs.frames_plan(folder, 1, 5, {**stamp, "engine": "cycles"})["reason"] == "engine changed"
    assert rs.frames_plan(tmp_path / "nothing", 1, 5, stamp) == {"total": 5, "reused": 0, "stale": 0,
                                                                 "reason": "not rendered yet"}
    for f in (3, 4, 5):
        _png(folder / f"{f:04d}.png")
    assert rs.frames_plan(folder, 1, 5, stamp) == {"total": 5, "reused": 5, "stale": 0, "reason": None}


def test_seed_from_estimate(tmp_path):
    review = tmp_path / "review"
    review.mkdir()
    assert rs.seed_from_estimate(tmp_path, "eevee", "final") is None
    (review / "estimate.json").write_text(json.dumps({"frames": 100, "eevee_s": 50.0, "cycles_s": 400.0}))
    assert rs.seed_from_estimate(tmp_path, "eevee", "final") == pytest.approx(0.5)
    assert rs.seed_from_estimate(tmp_path, "cycles", "final") == pytest.approx(4.0)
    assert rs.seed_from_estimate(tmp_path, "cycles", "preview") == pytest.approx(4.0 * rs.PREVIEW_OF_FINAL)
    assert rs.seed_from_estimate(tmp_path, None, "final") is None


# --------------------------------------------------------------------------- stuck frames from the PNG data


def test_pixel_digest_ignores_metadata(tmp_path):
    a, b, c = tmp_path / "a.png", tmp_path / "b.png", tmp_path / "c.png"
    _png(a, text=b"Frame\x000001")
    _png(b, text=b"Frame\x000002")
    _png(c, pixel=b"\x11\x20\x30", text=b"Frame\x000003")
    assert rs.pixel_digest(a) == rs.pixel_digest(b) != rs.pixel_digest(c)
    assert rs.pixel_digest(tmp_path / "missing.png") is None


def test_frame_keys_compare_pixels_cheaply(tmp_path):
    a, b, c = tmp_path / "a.png", tmp_path / "b.png", tmp_path / "c.png"
    _png(a, text=b"Frame\x000001")
    _png(b, text=b"Frame\x000002")  # other metadata of the same width, same pixels
    _png(c, pixel=b"\x11\x20\x30")
    ka, kb, kc = rs.frame_key(a), rs.frame_key(b), rs.frame_key(c)
    assert ka.size == a.stat().st_size
    assert ka == kb and ka != kc
    assert rs.frame_key(tmp_path / "missing.png") is None
    found = rs.stuck_from_digests([(1, kc), (2, ka), (3, kb), (4, kc)])
    assert [f.frame for f in found] == [3]


def test_stuck_frames_from_digests():
    hist = [(1, 10), (2, 20), (3, 20), (4, 30)]
    found = rs.stuck_from_digests(hist)
    assert [(f.check, f.frame, f.severity) for f in found] == [("stuck_frame", 3, "warning")]
    assert rs.stuck_from_digests([(1, 10), (2, 20), (3, 20), (4, 20)]) == []  # a still: not mid-motion
    assert rs.stuck_from_digests([(1, 10), (2, 20), (4, 20), (5, 30)]) == []  # not consecutive
    assert rs.stuck_from_digests([(2, 20), (3, 20), (4, 30)]) == []


# --------------------------------------------------------------------------- is it alive?


def test_alive_for_this_process_and_dead_for_an_exited_one():
    me = {"state": "running", "pid": os.getpid(), "pid_token": rs.process_token(os.getpid())}
    assert rs.is_alive(me)
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    token = rs.process_token(proc.pid)
    assert token is None
    assert not rs.is_alive({"state": "running", "pid": proc.pid, "pid_token": "x"})
    assert not rs.is_alive({"state": "running", "pid": None})


def test_a_reused_pid_is_not_the_render():
    me = {"state": "running", "pid": os.getpid(), "pid_token": "some-other-process-start"}
    if not rs.process_token(os.getpid()):
        pytest.skip("no process start time on this platform")
    assert not rs.is_alive(me)


def test_describe_marks_a_dead_running_render_stale(tmp_path):
    clock = Clock()
    t, _ = _tracker(tmp_path, clock, seed=2.0)
    t.start()
    t.begin_shot("s01")
    _render(t, clock, [5, 6])
    s = dict(_read(tmp_path), pid=999_999_999, pid_token="gone")
    d = rs.describe(s, now=clock.t + 30)
    assert d["alive"] is False and d["stale"] is True
    assert d["updated_ago_s"] == pytest.approx(30, abs=0.01)
    assert "stopped" in d["summary"] and "--preset preview" in d["summary"]
    live = rs.describe(dict(_read(tmp_path), pid=os.getpid(), pid_token=rs.process_token(os.getpid())), now=clock.t)
    assert live["alive"] is True and live["stale"] is False
    assert "6 of 20 frames" in live["summary"] and "s01" in live["summary"]


def test_describe_done():
    d = rs.describe({"state": "done", "preset": "final", "pid": 1, "started": 0.0, "finished": 690.0,
                     "updated": 690.0, "frames": {"total": 1556, "done": 1556, "rendered": 1556, "reused": 0},
                     "checks": {"errors": 0, "warnings": 2}}, now=700.0)  # fmt: skip
    assert d["alive"] is False and d["stale"] is False
    assert "done" in d["summary"] and "1556 frames" in d["summary"] and "2 warnings" in d["summary"]


def test_fmt_duration():
    assert rs.fmt_duration(None) == "unknown"
    assert rs.fmt_duration(42) == "42 s"
    assert rs.fmt_duration(125) == "2 min 5 s"
    assert rs.fmt_duration(3 * 3600 + 20 * 60 + 9) == "3 h 20 min"


def test_write_atomic_leaves_no_temp_files(tmp_path):
    p = tmp_path / "x" / "s.json"
    assert rs.write_atomic(p, "{}")
    assert rs.write_atomic(p, '{"a": 1}')
    assert json.loads(p.read_text()) == {"a": 1}
    assert [q.name for q in p.parent.iterdir()] == ["s.json"]


# --------------------------------------------------------------------------- the page


def _status(tmp_path, clock, state="running"):
    t, _ = _tracker(tmp_path, clock, seed=2.0)
    t.set_settings({"engine": "eevee", "preset": "preview", "resolution": [1080, 1920], "percent": 50,
                    "samples": 64, "denoise": None, "motion_blur": False, "device": "GPU",
                    "gpu": "NVIDIA GeForce RTX 4090", "backend": None,
                    "vram": [{"name": "RTX 4090", "total_gb": 24.0, "used_gb": 6.5}]})  # fmt: skip
    t.start()
    t.begin_shot("s01")
    _render(t, clock, [5, 6, 7])
    t.set_thumb("s01", 7)
    t.add_findings("s01", [Finding("fireflies", "warning", None, 6, "30 firefly pixels <b>")])
    if state != "running":
        t.finish(state, error="boom" if state == "failed" else None)
    return t.snapshot()


def test_page_shows_progress_settings_shots_and_warnings(tmp_path):
    from bsa.review import pages

    clock = Clock()
    status = rs.describe(_status(tmp_path, clock), now=clock.t)
    out = pages.render_status_page(status, tmp_path / "review" / "render-preview.html")
    html = out.read_text(encoding="utf-8")
    assert "Pendulum" in html and "preview" in html.lower()
    assert "7 of 20" in html and "35%" in html
    assert "RTX 4090" in html and "1080" in html and "50%" in html
    assert 'src="render-status/preview-s01.jpg?f=7"' in html
    assert "s02" in html
    assert "30 firefly pixels &lt;b&gt;" in html and "<b>" not in html.split("30 firefly pixels")[1][:10]
    assert "location.reload" in html  # refreshes itself while running
    assert "Running" in html


def test_page_for_a_finished_render_does_not_refresh(tmp_path):
    from bsa.review import pages

    clock = Clock()
    status = rs.describe(_status(tmp_path, clock, state="done"), now=clock.t)
    html = pages.render_status_page(status, tmp_path / "p.html").read_text(encoding="utf-8")
    assert "Done" in html and "location.reload" not in html


def test_page_for_a_dead_render_says_it_stopped(tmp_path):
    from bsa.review import pages

    clock = Clock()
    status = dict(_status(tmp_path, clock), pid=999_999_999, pid_token="gone")
    html = pages.render_status_page(rs.describe(status, now=clock.t + 5), tmp_path / "p.html").read_text("utf-8")
    assert "Not running" in html and "location.reload" not in html
    assert "render SHORT --preset preview" in html


# --------------------------------------------------------------------------- CLI render-status


def _cli(home: Path, *args):
    env = {**os.environ, "BSA_HOME": str(home)}
    return subprocess.run([sys.executable, str(CLI), *map(str, args)], check=False, capture_output=True, text=True,
                          env=env, cwd=str(home), timeout=120)  # fmt: skip


@pytest.fixture
def short(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    ws = tmp_path / "ws"
    assert _cli(home, "settings", "set", "workspace", ws).returncode == 0
    proc = _cli(home, "new-short", "Pendulum")
    assert proc.returncode == 0, proc.stderr
    return home, Path(json.loads(proc.stdout)["short"])


def test_cli_render_status_before_any_render(short):
    home, sdir = short
    proc = _cli(home, "render-status", sdir, "--preset", "final")
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)
    assert out["state"] == "none" and out["alive"] is False
    assert "no final render" in out["summary"]
    assert Path(out["page"]).is_file()


def test_cli_render_status_reports_a_dead_render_and_rewrites_its_page(short):
    home, sdir = short
    clock = Clock()
    t, _ = _tracker(sdir, clock)
    t.start()
    t.begin_shot("s01")
    _render(t, clock, [5, 6])
    path = sdir / "review" / "render-status-preview.json"
    path.write_text(json.dumps(dict(json.loads(path.read_text()), pid=999_999_999, pid_token="gone")))
    proc = _cli(home, "render-status", sdir)  # no --preset: the most recently updated one
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)
    assert out["preset"] == "preview" and out["state"] == "running"
    assert out["alive"] is False and out["stale"] is True
    assert out["frames"]["done"] == 6
    page = Path(out["page"])
    assert page == sdir / "review" / "render-preview.html"
    assert "Not running" in page.read_text(encoding="utf-8")


# --------------------------------------------------------------------------- docs


def test_docs_mention_the_live_status():
    skill = (ROOT / "skills" / "make-short" / "SKILL.md").read_text(encoding="utf-8")
    render = " ".join((ROOT / "skills" / "make-short" / "playbook" / "08-render.md").read_text("utf-8").split())
    assert "CLI render-status SHORT" in skill
    assert "render-status" in render and "render-preview.html" in render and "render-final.html" in render
    assert "current step" in render and "BLENDER check" in render and "previous run" in render


# --------------------------------------------------------------------------- runs: the preview chain and single steps


CHAIN = list(rs.CHAIN_STEPS)


def _run_obj(tmp_path, clock, steps=None, kind="preview", preset="preview", pages=None):
    pages = [] if pages is None else pages
    return rs.Run(rs.status_path(tmp_path, preset), short={"slug": "pendulum", "title": "Pendulum",
                  "dir": str(tmp_path)}, preset=preset, kind=kind, steps=steps or CHAIN, clock=clock,
                  render_page=pages.append)  # fmt: skip


def _step(status, name):
    return next(s for s in status["run"]["steps"] if s["name"] == name)


def test_chain_steps_are_recorded_with_the_current_step(tmp_path):
    clock = Clock()
    pages = []
    run = _run_obj(tmp_path, clock, pages=pages)
    with run:
        assert rs.active() is run
        run.skip("timeline", "the timing is current")
        with run.step("build") as st:
            clock.tick(30)
            st["summary"] = {"why": "build.py changed"}
        s = _read(tmp_path)
        assert s["version"] == 2 and s["kind"] == "preview" and s["state"] == "running"
        assert s["pid"] == os.getpid() and s["previous"] is None
        assert [x["state"] for x in s["run"]["steps"]] == ["skipped", "done"] + ["pending"] * 6
        assert _step(s, "timeline")["summary"] == {"skipped": "the timing is current"}
        b = _step(s, "build")
        assert b["finished"] - b["started"] == 30 and b["summary"] == {"why": "build.py changed"}
        with run.step("mix"):
            s = _read(tmp_path)
            assert s["run"]["current"] == "mix" and _step(s, "mix")["state"] == "running"
    s = _read(tmp_path)
    assert rs.active() is None
    assert s["state"] == "done" and s["run"]["current"] is None and s["finished"] == clock.t
    assert pages and pages[-1]["state"] == "done"


def test_a_failing_step_fails_the_run_and_leaves_the_rest_pending(tmp_path):
    clock = Clock()
    run = _run_obj(tmp_path, clock)
    with pytest.raises(RuntimeError), run:
        with run.step("timeline"):
            pass
        with run.step("build"):
            raise RuntimeError("source/build.py failed: boom")
    s = _read(tmp_path)
    assert s["state"] == "failed" and "boom" in s["error"]
    assert _step(s, "build")["state"] == "failed" and _step(s, "mix")["state"] == "pending"
    d = rs.describe(s, now=clock.t)
    assert "failed" in d["summary"] and "build" in d["summary"] and "BLENDER preview" in d["summary"]
    assert d["step"]["name"] == "build"


def test_an_interrupted_step_is_stopped(tmp_path):
    clock = Clock()
    run = _run_obj(tmp_path, clock, steps=["check"], kind="check")
    with pytest.raises(KeyboardInterrupt), run, run.step("check"):
        raise KeyboardInterrupt
    s = _read(tmp_path)
    assert s["state"] == "stopped" and _step(s, "check")["state"] == "stopped"


def test_step_progress_is_throttled_and_has_an_eta_from_the_measured_speed(tmp_path, monkeypatch):
    clock = Clock()
    run = _run_obj(tmp_path, clock)
    items = [{"id": "s01", "total": 10, "done": 0, "state": "checking"}]
    with run, run.step("check"):
        run.progress(0, 100, current="shot s01", items=items)
        writes = []
        real = rs.write_atomic

        def counting(path, text, tries=8):
            writes.append(json.loads(text))
            return real(path, text, tries)

        monkeypatch.setattr(rs, "write_atomic", counting)
        for i in range(1, 21):  # 20 frames at 0.25 s: 5 s, so about 2-3 writes, not 20
            clock.tick(0.25)
            items[0]["done"] = min(i, 10)
            run.progress(i, 100, current="shot s01")
        monkeypatch.setattr(rs, "write_atomic", real)
        assert 1 <= len(writes) <= 3
        run.progress(20, 100, current="shot s01", force=True)
        s = _read(tmp_path)
        p = _step(s, "check")["progress"]
        assert p["done"] == 20 and p["total"] == 100 and p["unit"] == "frames" and p["current"] == "shot s01"
        assert p["eta_s"] == pytest.approx(80 * 0.25, rel=0.01) and p["percent"] == 20
        assert p["items"][0]["done"] == 10
        d = rs.describe(s, now=clock.t)
        assert d["alive"] and "step 5 of 8" in d["summary"] and "check" in d["summary"]
        assert "20 of 100 frames" in d["summary"] and "s01" in d["summary"] and "left" in d["summary"]
        assert (d["step"]["name"], d["step"]["index"], d["step"]["of"], d["step"]["state"]) == ("check", 5, 8, "running")
    assert _step(_read(tmp_path), "check")["progress"]["done"] == 20


def test_a_new_run_supersedes_the_last_one_and_keeps_its_outcome(tmp_path):
    clock = Clock()
    t, _ = _tracker(tmp_path, clock)  # an old render (flat status, no run) that finished
    t.start()
    t.begin_shot("s01")
    _render(t, clock, [5, 6])
    t.finish("done")
    clock.tick(36 * 60)
    with _run_obj(tmp_path, clock) as run, run.step("check"):
        s = _read(tmp_path)
        assert s["state"] == "running" and s["render"] is None and "frames" not in s
        prev = s["previous"]
        assert prev["kind"] == "render" and prev["state"] == "done" and prev["finished"] == clock.t - 36 * 60
        assert "preview render done" in prev["summary"]
    clock.tick(5)
    with _run_obj(tmp_path, clock, steps=["edit"], kind="edit") as run, run.step("edit"):
        prev = _read(tmp_path)["previous"]
        assert prev["kind"] == "preview" and prev["state"] == "done" and "previous" not in prev  # one level only


def _starting(tmp_path, clock, kind="render", preset="preview", pages=None):
    return rs.starting(rs.status_path(tmp_path, preset), short={"slug": "pendulum", "title": "Pendulum",
                       "dir": str(tmp_path)}, preset=preset, kind=kind, clock=clock,
                       render_page=(pages if pages is not None else []).append)  # fmt: skip


def test_a_starting_command_never_shows_the_last_run_as_current(tmp_path):
    """The first thing a BLENDER command does (before Blender opens the .blend) is say a new run is starting, so
    render-status and the page never show the last run, done, as if it were this one."""
    clock = Clock()
    with _run_obj(tmp_path, clock, steps=["edit"], kind="edit") as run, run.step("edit"):
        pass
    clock.tick(60)
    pages = []
    assert _starting(tmp_path, clock, kind="render", pages=pages)
    s = _read(tmp_path)
    assert s["state"] == "running" and s["phase"] == "starting" and s["kind"] == "render"
    assert s["pid"] == os.getpid() and s["started"] == clock.t and s["render"] is None
    assert s["previous"]["kind"] == "edit" and s["previous"]["state"] == "done"
    d = rs.describe(s, now=clock.t)
    assert d["alive"] and not d["idle"] and "starting" in d["summary"] and "edit" not in d["summary"]
    assert pages and pages[-1]["phase"] == "starting"
    clock.tick(4)
    with _run_obj(tmp_path, clock, steps=["render"], kind="render") as run, run.step("render"):
        s = _read(tmp_path)
        assert s["phase"] == "render" and s["previous"]["kind"] == "edit"  # the run it supersedes, not "starting"


def test_a_command_that_fails_before_its_run_starts_says_so(tmp_path):
    clock = Clock()
    _starting(tmp_path, clock, kind="frame-check")
    clock.tick(2)
    rs.settle_starting(rs.status_path(tmp_path, "preview"), error="build.py changed: rebuild", clock=clock)
    s = _read(tmp_path)
    assert s["state"] == "failed" and s["error"] == "build.py changed: rebuild" and s["finished"] == clock.t
    d = rs.describe(s, now=clock.t)
    assert d["idle"] and "frame-check failed" in d["summary"] and "rebuild" in d["summary"]


def test_settling_leaves_a_run_that_started_alone(tmp_path):
    clock = Clock()
    _starting(tmp_path, clock, kind="check")
    with _run_obj(tmp_path, clock, steps=["check"], kind="check") as run, run.step("check"):
        pass
    before = _read(tmp_path)
    rs.settle_starting(rs.status_path(tmp_path, "preview"), error="boom", clock=clock)
    assert _read(tmp_path) == before


def test_starting_leaves_a_live_run_of_another_process_alone(tmp_path):
    clock = Clock()
    path = rs.status_path(tmp_path, "preview")
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        doc = {"version": 2, "state": "running", "kind": "render", "preset": "preview", "pid": proc.pid,
               "pid_token": rs.process_token(proc.pid), "run": {"kind": "render", "steps": [], "current": "render"}}  # fmt: skip
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps(doc), encoding="utf-8")
        assert not _starting(tmp_path, clock)
        rs.settle_starting(path, error="x", clock=clock)
        assert json.loads(path.read_text(encoding="utf-8")) == doc
    finally:
        proc.kill()
        proc.wait()


def test_a_starting_page_renders(tmp_path):
    from bsa.review import pages

    clock = Clock()
    _starting(tmp_path, clock, kind="preview")
    page = rs.page_path(tmp_path, "preview")
    pages.render_status_page(rs.describe(_read(tmp_path), now=clock.t), page)
    assert "starting" in page.read_text(encoding="utf-8").lower()


def test_a_dead_running_run_is_superseded_as_stopped(tmp_path):
    clock = Clock()
    path = rs.status_path(tmp_path, "preview")
    with _run_obj(tmp_path, clock) as run, run.step("check"):
        pass
    doc = dict(_read(tmp_path), state="running", pid=999_999_999, pid_token="gone")
    path.write_text(json.dumps(doc), encoding="utf-8")
    with _run_obj(tmp_path, clock, steps=["check"], kind="check"):
        assert _read(tmp_path)["previous"]["state"] == "stopped"


def test_a_live_run_of_another_process_is_left_alone(tmp_path):
    clock = Clock()
    path = rs.status_path(tmp_path, "preview")
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        token = rs.process_token(proc.pid)
        doc = {"version": 2, "state": "running", "kind": "render", "preset": "preview", "pid": proc.pid,
               "pid_token": token, "run": {"kind": "render", "steps": [], "current": "render"}}  # fmt: skip
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps(doc), encoding="utf-8")
        with _run_obj(tmp_path, clock, steps=["check"], kind="check") as run, run.step("check"):
            run.progress(1, 2)
            assert run.inert
        assert json.loads(path.read_text(encoding="utf-8")) == doc
    finally:
        proc.kill()
        proc.wait()


def test_a_render_inside_a_run_writes_the_render_section(tmp_path):
    clock = Clock()
    pages = []
    with _run_obj(tmp_path, clock, pages=pages) as run:
        run.skip("timeline", "current")
        with run.step("render"):
            t, _ = _tracker(tmp_path, clock, seed=2.0, run=run)
            t.start()
            t.begin_shot("s01")
            _render(t, clock, [5, 6])
            s = _read(tmp_path)
            assert s["version"] == 2 and s["state"] == "running" and s["run"]["current"] == "render"
            assert s["render"]["frames"]["done"] == 6 and s["render"]["state"] == "running"
            d = rs.describe(s, now=clock.t)
            assert d["frames"]["done"] == 6 and d["shots"][0]["id"] == "s01"  # lifted for older readers
            assert "step 6 of 8" in d["summary"] and "6 of 20 frames" in d["summary"]
            assert d["stall_after_s"] == s["render"]["stall_after_s"]
            t.finish("done")
        with run.step("frame-check"):
            d = rs.describe(_read(tmp_path), now=clock.t)
            assert d["state"] == "running" and d["step"]["name"] == "frame-check"
            assert d["render"]["state"] == "done"
    d = rs.describe(_read(tmp_path), now=clock.t)
    assert d["state"] == "done" and d["alive"] is False and "preview chain done" in d["summary"]


def test_a_standalone_render_run_reads_like_a_render(tmp_path):
    clock = Clock()
    with _run_obj(tmp_path, clock, steps=["render"], kind="render") as run, run.step("render"):
        t, _ = _tracker(tmp_path, clock, run=run)
        t.start()
        t.begin_shot("s01")
        _render(t, clock, [5])
        t.finish("done")
    d = rs.describe(_read(tmp_path), now=clock.t)
    assert d["state"] == "done" and "preview render done" in d["summary"] and d["frames"]["total"] == 20


def test_describe_a_dead_chain(tmp_path):
    clock = Clock()
    with _run_obj(tmp_path, clock) as run, run.step("check"):
        run.progress(30, 120, current="shot s02")
        doc = dict(_read(tmp_path), pid=999_999_999, pid_token="gone")
    d = rs.describe(doc, now=clock.t + 10)
    assert d["stale"] and not d["alive"]
    assert "gone" in d["summary"] and "check" in d["summary"] and "BLENDER preview" in d["summary"]


def test_chain_page_shows_the_steps_the_current_one_and_the_previous_run(tmp_path):
    from bsa.review import pages

    clock = Clock()
    with _run_obj(tmp_path, clock, steps=["render"], kind="render") as run, run.step("render"):
        pass
    clock.tick(600)
    with _run_obj(tmp_path, clock) as run:
        run.skip("timeline", "the timing is current")
        with run.step("check"):
            run.progress(40, 160, current="shot s02 (scene Clock)",
                         items=[{"id": "s01", "total": 40, "done": 40, "state": "done"},
                                {"id": "s02", "total": 120, "done": 0, "state": "checking"}])  # fmt: skip
            clock.tick(5)
            run.progress(80, 160, current="shot s02 (scene Clock)", force=True)
            html = pages.render_status_page(rs.describe(_read(tmp_path), now=clock.t),
                                            tmp_path / "p.html").read_text("utf-8")  # fmt: skip
    assert "Running" in html and "location.reload" in html
    assert 'class="step-row running current"' in html
    assert "80 of 160 frames" in html and "s02 (scene Clock)" in html
    assert "skipped" in html and "the timing is current" in html
    assert "Previous run" in html and "render done" in html
    assert "Steps" in html
    done = pages.render_status_page(rs.describe(_read(tmp_path), now=clock.t), tmp_path / "q.html").read_text("utf-8")
    assert "location.reload" not in done and "Done" in done


def test_cli_render_status_shows_a_running_step_without_a_render(short):
    home, sdir = short
    clock = Clock()  # liveness comes from the pid: this test process is the run's process
    with _run_obj(sdir, clock) as run, run.step("check"):
        run.progress(30, 120, current="shot s02", force=True)
        proc = _cli(home, "render-status", sdir)
        assert proc.returncode == 0, proc.stderr
        out = json.loads(proc.stdout)
        assert out["state"] == "running" and out["alive"] is True and out["idle"] is False
        assert out["step"]["name"] == "check" and out["step"]["progress"]["done"] == 30
        assert "check" in out["summary"] and "30 of 120 frames" in out["summary"]
    proc = _cli(home, "render-status", sdir)
    out = json.loads(proc.stdout)
    assert out["state"] == "done" and out["alive"] is False and out["idle"] is True
    assert out["last_run"]["kind"] == "preview" and out["last_run"]["state"] == "done"
    assert "done" in out["summary"]


def test_a_run_keeps_one_id_from_starting_to_its_end_and_the_summary_names_it(tmp_path):
    """A reader can tell the last run from the one it just launched: each run has an id and its start time, from
    the "starting" status through the run, and the summary says when it started."""
    import time as _time

    clock = Clock()
    _starting(tmp_path, clock, kind="render")
    first = _read(tmp_path)
    assert first["run_id"] == f"{os.getpid()}-{int(first['started'])}"
    clock.tick(3)
    with pytest.raises(RuntimeError), _run_obj(tmp_path, clock, steps=["render"], kind="render") as run,             run.step("render"):  # fmt: skip
        assert _read(tmp_path)["run_id"] == first["run_id"]
        raise RuntimeError("GPU out of memory")
    s = _read(tmp_path)
    d = rs.describe(s, now=clock.t + 60)
    hhmm = _time.strftime("%H:%M:%S", _time.localtime(s["started"]))
    assert d["run_id"] == first["run_id"] and d["started_at"] == hhmm
    assert f"run started {hhmm}" in d["summary"]
    assert "no new run has written its status yet" in d["note"]
    clock.tick(120)
    _starting(tmp_path, clock, kind="render")
    assert _read(tmp_path)["run_id"] != first["run_id"]
    assert _read(tmp_path)["previous"]["run_id"] == first["run_id"]
