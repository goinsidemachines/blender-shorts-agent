"""Rendering in chunks (bsa.chunks): planning, the setting, resuming after a crash, and the parent's loop over child
Blender processes."""

import json
import types
from pathlib import Path

import pytest

import bsa_blender
from bsa import chunks, settings


def _png(folder: Path, frames, size=10):
    folder.mkdir(parents=True, exist_ok=True)
    for f in frames:
        (folder / f"{f:04d}.png").write_bytes(b"x" * size)


# --------------------------------------------------------------------------- planning (pure)


def test_chunk_count_and_when_chunks_are_wanted():
    assert chunks.chunk_count(600, 250) == 3 and chunks.chunk_count(250, 250) == 1 and chunks.chunk_count(251, 250) == 2
    assert chunks.chunk_count(0, 250) == 0 and chunks.chunk_count(100, 0) == 0
    assert chunks.wanted(251, 250) and not chunks.wanted(250, 250) and not chunks.wanted(5000, 0)


def test_take_covers_the_first_budget_missing_frames():
    assert chunks.take([5, 6, 7, 20, 21], 3) == (5, 7, 3)
    assert chunks.take([5, 6, 9, 10], 3) == (5, 9, 3)  # frames there inside the range are skipped by the render
    assert chunks.take([5, 6], 10) == (5, 6, 2)
    with pytest.raises(ValueError):
        chunks.take([], 3)


def test_the_chunks_cover_every_missing_frame_once():
    todo = [f for f in range(1, 101) if f % 7]  # some frames already there
    seen, left = [], list(todo)
    while left:
        a, b, n = chunks.take(left, 30)
        got = [f for f in left if a <= f <= b]
        assert len(got) == n and n <= 30
        seen += got
        left = [f for f in left if f > b]
    assert seen == todo


def test_missing_ignores_empty_placeholders_and_finished_frames(tmp_path):
    _png(tmp_path, [1, 2, 4])
    (tmp_path / "0003.png").write_bytes(b"")  # a placeholder an interrupted render left
    assert chunks.missing(tmp_path, 1, 6) == [3, 5, 6]


def test_merge_shots_sums_what_each_child_rendered():
    first = [{"shot": "s01", "frames": 100, "rendered": 100, "kept": 0, "why": "not rendered yet", "folder": "f"},
             {"shot": "s02", "frames": 100, "rendered": 40, "kept": 0, "why": "not rendered yet", "partial": True}]
    second = [{"shot": "s02", "frames": 100, "rendered": 60, "kept": 40, "why": "resuming", "damaged": "5"},
              {"shot": "s03", "frames": 10, "rendered": 0, "kept": 10, "why": "unchanged"}]
    rows = chunks.merge_shots([first, second])
    assert [r["shot"] for r in rows] == ["s01", "s02", "s03"]
    assert rows[1]["rendered"] == 100 and rows[1]["why"] == "not rendered yet" and rows[1]["damaged"] == "5"
    assert "partial" not in rows[1]


# --------------------------------------------------------------------------- the setting


def test_chunk_frames_setting():
    assert settings.check_value("render.chunk_frames", "300") == 300
    assert settings.check_value("render.chunk_frames", 0) == 0
    assert settings.check_value("render.chunk_frames", "off") == 0
    for bad in (-5, "many", 2.5, True, None):
        with pytest.raises(ValueError, match="render.chunk_frames"):
            settings.check_value("render.chunk_frames", bad)


def test_chunk_size_defaults_and_resolves():
    assert chunks.resolve({}) == chunks.DEFAULT == 250
    assert chunks.resolve({"render": {"chunk_frames": 100}}) == 100
    assert chunks.resolve({"render": {"chunk_frames": 0}}) == 0
    assert chunks.resolve({"render": {"chunk_frames": "junk"}}) == chunks.DEFAULT


# --------------------------------------------------------------------------- the parent's loop


class _Run:
    def __init__(self):
        self.renders = []

    def set_render(self, status):
        self.renders.append(status)


def _loop(results, *, started_frames=None, size=250):
    """Run bsa_blender._render_chunks with child processes stood in by `results` (one dict, or an Exception, per
    child), each called with the chunk's number."""
    calls = []

    def spawn(args, sdir, number, size_, run, started, reasons=None):
        calls.append(number)
        res = results[len(calls) - 1]
        if isinstance(res, Exception):
            raise res
        return res

    args = types.SimpleNamespace(preset="final")
    out = bsa_blender._render_chunks(args, Path("/x"), _Run(), size=size, fresh=started_frames or 600, spawn=spawn)
    return out, calls


def test_the_parent_starts_children_until_none_has_more_to_do():
    a = {"more": True, "fresh_frames": 250, "render_seconds": 50.0,
         "shots": [{"shot": "s01", "frames": 400, "rendered": 250, "kept": 0, "why": "not rendered yet",
                    "partial": True}], "warnings": ["w1"]}
    b = {"more": True, "fresh_frames": 250, "render_seconds": 55.0,
         "shots": [{"shot": "s01", "frames": 400, "rendered": 150, "kept": 250, "why": "resuming"},
                   {"shot": "s02", "frames": 200, "rendered": 100, "kept": 0, "why": "not rendered yet",
                    "partial": True}], "warnings": ["w1"]}
    c = {"fresh_frames": 100, "render_seconds": 20.0, "anchors": {"s01": 1},
         "shots": [{"shot": "s02", "frames": 200, "rendered": 100, "kept": 100, "why": "resuming"}]}
    out, calls = _loop([a, b, c])
    assert calls == [1, 2, 3]
    assert [r["rendered"] for r in out["shots"]] == [400, 200]
    assert out["fresh_frames"] == 600 and out["render_seconds"] == 125.0 and out["anchors"] == {"s01": 1}
    assert out["warnings"] == ["w1"] and out["chunks"] == 3


def test_a_chunk_that_renders_nothing_but_wants_more_is_an_error():
    stuck = {"more": True, "fresh_frames": 0, "render_seconds": 0.0, "shots": []}
    with pytest.raises(bsa_blender.CliError, match="no progress"):
        _loop([stuck])


def test_a_failed_chunk_stops_the_render_and_says_how_to_resume():
    ok = {"more": True, "fresh_frames": 250, "render_seconds": 10.0, "shots": []}
    with pytest.raises(bsa_blender.CliError, match=r"chunk 2 failed: out of memory.*run it again.*resumes"):
        _loop([ok, bsa_blender.CliError("out of memory")])


# --------------------------------------------------------------------------- a child's work and resuming


def test_a_chunk_resumes_from_the_frames_on_disk(tmp_path):
    """After a crash the frames that are there and not empty are not rendered again: the next chunk starts at the
    first missing frame."""
    folder = tmp_path / "s01"
    _png(folder, range(1, 251))  # the first chunk finished before the crash
    (folder / "0251.png").write_bytes(b"")  # the frame the crash cut off
    todo = chunks.missing(folder, 1, 400)
    assert todo[0] == 251 and len(todo) == 150
    assert chunks.take(todo, 250) == (251, 400, 150)


def _real_png() -> bytes:
    import struct
    import zlib

    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    row = zlib.compress(b"\x00\x00\x00\x00\x00")
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", row) + chunk(b"IEND", b""))


def test_a_cut_off_frame_is_dropped_before_the_chunk_plans(tmp_path):
    """The resume check is the render's own: a kept frame that is not a whole PNG is deleted, so it is missing and the
    next chunk renders it."""
    from bsa import freshness
    from bsa.timeline import Span

    span = Span("s01", 1, 4)
    stamp = freshness.frames_stamp(span, "eevee", "fp", "final")
    folder = tmp_path / "s01"
    folder.mkdir()
    (folder / ".render.json").write_text(json.dumps(stamp), encoding="utf-8")
    good = _real_png()
    for f in (1, 2, 4):
        (folder / f"{f:04d}.png").write_bytes(good)
    (folder / "0003.png").write_bytes(good[:20])  # cut off by a crash: not empty, not a PNG
    dropped = bsa_blender._drop_damaged(tmp_path, [types.SimpleNamespace(id="s01")], {"s01": span},
                                        {"s01": stamp}, None)
    assert dropped == {"s01": [3]}
    assert chunks.missing(folder, 1, 4) == [3]


def test_the_status_keeps_the_reason_a_shot_started_with_across_chunks():
    """Chunk 2 sees s03 half rendered and calls it "resuming"; the run's status keeps "fingerprint changed"."""
    reasons: dict = {}
    first = {"shots": [{"id": "s01", "reason": None}, {"id": "s03", "reason": "fingerprint changed"}]}
    second = {"shots": [{"id": "s01", "reason": None}, {"id": "s03", "reason": "resuming"},
                        {"id": "s04", "reason": "resuming"}]}
    bsa_blender._keep_reasons(first, reasons)
    bsa_blender._keep_reasons(second, reasons)
    assert [s["reason"] for s in second["shots"]] == [None, "fingerprint changed", "resuming"]  # s04 first seen now
    assert reasons == {"s01": None, "s03": "fingerprint changed", "s04": "resuming"}


def test_a_child_process_status_goes_through_the_kept_reasons(tmp_path):
    import sys

    class Run:
        def __init__(self):
            self.renders = []

        def set_render(self, status):
            self.renders.append(status)

    code = ("import json\nprint('BSA_RENDER ' + json.dumps({'shots': [{'id': 's03', 'reason': 'resuming'}]}), "
            "flush=True)\nprint('BSA_RESULT ' + json.dumps({'more': False}))\n")
    orig = bsa_blender._chunk_command
    bsa_blender._chunk_command = lambda a, d, s: [sys.executable, "-c", code]
    try:
        run_ = Run()
        bsa_blender._spawn_chunk(types.SimpleNamespace(), tmp_path, 2, 10, run_, 1.0, {"s03": "fingerprint changed"})
    finally:
        bsa_blender._chunk_command = orig
    assert run_.renders[0]["shots"][0]["reason"] == "fingerprint changed"
