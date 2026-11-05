import json
import sys
import threading
from pathlib import Path

import pytest

from bsa import proc as _proc
from bsa.review import notes, stages, store

OVERALL = {"kind": "overall", "id": None, "version": 1}


def test_statuses_map_jobstate_onto_page_stages():
    s = stages.statuses(["interview", "story", "storyboard", "engine", "narration", "build", "checks"])
    assert s["engine"] == "done" and s["render"] == "current" and s["final"] == "locked"
    assert stages.statuses([])["interview"] == "current"
    assert list(stages.statuses([])) == list(stages.page_stages(False))
    assert stages.page_stages(False) == tuple(p for p in stages.PAGE_STAGES if p != "camera")
    with_camera = stages.statuses(["interview", "story", "storyboard", "narration"], stages.page_stages(True))
    assert list(with_camera) == list(stages.PAGE_STAGES) and with_camera["camera"] == "current"
    assert with_camera["engine"] == "locked" and with_camera["narration"] == "done"


def test_stage_constants():
    assert stages.LABELS["engine"] == "Engine"
    assert set(stages.LABELS) == set(stages.PAGE_STAGES)
    assert stages.COVERS["render"] == ("build", "checks", "preview")
    assert stages.APPROVAL_STAGES == ("story", "storyboard", "camera", "engine") and stages.ACK_STAGES == ("final",)


def test_note_round_trip(tmp_path):
    n = notes.add(tmp_path, "story", {"kind": "line", "id": "l02", "version": 1}, "Say 'beat', not 'tempo'", "page")
    assert n["id"] == "n1" and n["status"] == "open" and n["reply"] is None and n["by"] == "page"
    assert [x["status"] for x in notes.send_stage(tmp_path, "story")] == ["sent"]
    done = notes.answer(tmp_path, "n1", "done", "Changed it to 'beat'.")
    assert done["status"] == "done" and notes.load(tmp_path)[0]["reply"] == "Changed it to 'beat'."


def test_frame_note_keeps_its_shot(tmp_path):
    n = notes.add(tmp_path, "final", {"kind": "frame", "id": "12", "shot": "s02", "version": 1}, "Dot here", "page")
    assert n["target"] == {"kind": "frame", "id": "12", "shot": "s02", "version": 1}
    n = notes.add(tmp_path, "final", {"kind": "frame", "id": "95", "shot": None, "version": 1}, "Card", "page")
    assert n["target"] == {"kind": "frame", "id": "95", "shot": None, "version": 1}
    assert "shot" not in notes.add(tmp_path, "final", {"kind": "line", "id": "l01", "shot": "s01"}, "x", "page")["target"]
    with pytest.raises(ValueError, match="shot"):
        notes.add(tmp_path, "final", {"kind": "frame", "id": "3", "shot": ["s01"], "version": 1}, "x", "page")


def test_note_validation(tmp_path):
    with pytest.raises(ValueError, match="4000"):
        notes.add(tmp_path, "story", OVERALL, "x" * 4001, "page")
    with pytest.raises(ValueError):
        notes.add(tmp_path, "nonsense", OVERALL, "hi", "page")
    with pytest.raises(ValueError):
        notes.add(tmp_path, "story", {"kind": "bogus", "id": None, "version": 1}, "hi", "page")
    with pytest.raises(ValueError):
        notes.add(tmp_path, "story", OVERALL, "   ", "page")
    with pytest.raises(ValueError):
        notes.answer(tmp_path, "n9", "done", "x")


def test_withdraw_and_answer_rules(tmp_path):
    notes.add(tmp_path, "story", OVERALL, "one", "page")
    notes.add(tmp_path, "story", OVERALL, "two", "chat")
    notes.send(tmp_path, ["n2"])
    notes.withdraw(tmp_path, "n1")
    assert [n["id"] for n in notes.load(tmp_path)] == ["n2"]
    with pytest.raises(ValueError):
        notes.withdraw(tmp_path, "n2")  # already sent
    with pytest.raises(ValueError):
        notes.answer(tmp_path, "n2", "open", "x")
    with pytest.raises(ValueError):
        notes.answer(tmp_path, "n2", "done", "two\nlines")
    with pytest.raises(ValueError):
        notes.answer(tmp_path, "n2", "declined", "y" * 301)
    assert notes.answer(tmp_path, "n2", "declined", "Not doing that.")["status"] == "declined"
    notes.add(tmp_path, "story", OVERALL, "three", "page")
    assert notes.load(tmp_path)[-1]["id"] == "n3"  # ids never reused


def test_damaged_notes_file_is_never_overwritten(tmp_path):
    p = notes.notes_path(tmp_path)
    p.parent.mkdir()
    p.write_bytes(b"")
    with pytest.raises(store.StoreDamaged, match="notes.json"):
        notes.add(tmp_path, "story", OVERALL, "hi", "page")
    assert p.read_bytes() == b""
    p.write_bytes(b"{not json")
    with pytest.raises(store.StoreDamaged, match="not valid JSON"):
        store.read_json(p, [])


def test_read_json_default_when_missing(tmp_path):
    assert store.read_json(tmp_path / "nope.json", {"a": 1}) == {"a": 1}


def test_update_json_returns_new_value(tmp_path):
    p = tmp_path / "x" / "v.json"
    p.parent.mkdir()
    assert store.update_json(p, [], lambda d: [*d, 1]) == [1]
    assert json.loads(p.read_text()) == [1]
    assert not Path(str(p) + ".lock").exists()


def test_stale_lock_is_broken(tmp_path):
    p = tmp_path / "v.json"
    lock = Path(str(p) + ".lock")
    lock.write_text(json.dumps({"pid": 2**30, "at": 0}))
    old = lock.stat().st_mtime - 60
    import os

    os.utime(lock, (old, old))
    assert store.update_json(p, [], lambda d: [*d, 1]) == [1]


def test_live_lock_times_out(tmp_path):
    p = tmp_path / "v.json"
    lock = Path(str(p) + ".lock")
    import os
    import time

    lock.write_text(json.dumps({"pid": os.getpid(), "at": time.time()}))
    with pytest.raises(TimeoutError), store.locked(p, timeout_s=0.3):
        pass


def test_concurrent_writers_lose_nothing(tmp_path):  # Review Focus 2
    def writer(k):
        for i in range(25):
            notes.add(tmp_path, "story", OVERALL, f"{k}-{i}", "page")

    threads = [threading.Thread(target=writer, args=(k,)) for k in range(4)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    got = notes.load(tmp_path)
    assert len(got) == 100 and len({n["id"] for n in got}) == 100


def test_concurrent_processes_lose_nothing(tmp_path):
    code = (
        "import sys\n"
        "from pathlib import Path\n"
        "from bsa.review import notes\n"
        "for i in range(25):\n"
        "    notes.add(Path(sys.argv[1]), 'story', {'kind': 'overall', 'id': None, 'version': 1}, f'{sys.argv[2]}-{i}', 'page')\n"
    )
    root = str(Path(__file__).resolve().parent.parent)
    procs = [_proc.popen([sys.executable, "-c", code, str(tmp_path), str(k)], cwd=root) for k in range(2)]
    assert [p.wait(timeout=60) for p in procs] == [0, 0]
    got = notes.load(tmp_path)
    assert len(got) == 50 and len({n["id"] for n in got}) == 50


def test_a_lock_whose_pid_was_reused_is_broken(tmp_path, monkeypatch):  # a reboot gave its pid to another program
    import os
    import time

    from bsa import render_status

    # Patched so the rule is exercised on every OS (macOS has no /proc, so the real token is "").
    monkeypatch.setattr(render_status, "process_token", lambda pid: "this-process-now")
    p = tmp_path / "v.json"
    lock = Path(str(p) + ".lock")
    lock.write_text(json.dumps({"pid": os.getpid(), "process_token": "another-process", "at": time.time()}))
    assert store.update_json(p, [], lambda d: [*d, 1]) == [1]


@pytest.mark.skipif(sys.platform not in ("win32", "linux"), reason="needs a real process token (Windows or /proc)")
def test_a_lock_whose_pid_was_reused_is_broken_with_the_real_token(tmp_path):
    import os
    import time

    p = tmp_path / "v.json"
    lock = Path(str(p) + ".lock")
    lock.write_text(json.dumps({"pid": os.getpid(), "process_token": "another-process", "at": time.time()}))
    assert store.update_json(p, [], lambda d: [*d, 1]) == [1]


def test_release_leaves_a_lock_that_is_no_longer_its_own(tmp_path):
    p = tmp_path / "v.json"
    lock = Path(str(p) + ".lock")
    with store.locked(p):  # A holds the lock ...
        lock.write_text(json.dumps({"pid": 1, "process_token": "b", "at": 1}))  # ... and B's replaces it
        b_body = lock.read_bytes()
    assert lock.read_bytes() == b_body  # A's release left B's lock in place


def test_release_removes_its_own_lock(tmp_path):
    p = tmp_path / "v.json"
    with store.locked(p):
        assert Path(str(p) + ".lock").exists()
    assert not Path(str(p) + ".lock").exists()


def test_a_lock_older_than_a_minute_is_broken_even_with_a_live_pid(tmp_path):
    import os

    p = tmp_path / "v.json"
    lock = Path(str(p) + ".lock")
    lock.write_text(json.dumps({"pid": os.getpid(), "at": 0}))
    old = lock.stat().st_mtime - 61
    os.utime(lock, (old, old))
    assert store.update_json(p, [], lambda d: [*d, 1]) == [1]


def test_the_lock_names_its_process_token(tmp_path):
    import os

    from bsa import render_status

    p = tmp_path / "v.json"
    with store.locked(p):
        held = json.loads(Path(str(p) + ".lock").read_text())
    assert held["pid"] == os.getpid() and held["process_token"] == (render_status.process_token(os.getpid()) or "")


def test_read_json_retries_a_file_being_replaced(tmp_path, monkeypatch):  # Windows: a reader racing os.replace
    path = tmp_path / "notes.json"
    path.write_text("[]", "utf-8")
    real, calls = Path.read_bytes, []

    def flaky(self):
        calls.append(self)
        if len(calls) < 3:
            raise PermissionError(13, "Access is denied", str(self))
        return real(self)

    monkeypatch.setattr(store, "_RETRY_DENIED", True)
    monkeypatch.setattr(Path, "read_bytes", flaky)
    monkeypatch.setattr(store.time, "sleep", lambda s: None)
    assert store.read_json(path, None, list) == [] and len(calls) == 3
    calls.clear()
    monkeypatch.setattr(Path, "read_bytes", lambda self: (_ for _ in ()).throw(PermissionError(13, "denied")))
    with pytest.raises(PermissionError):
        store.read_json(path, None)


def test_look_note_kind_is_accepted(tmp_path):
    assert "look" in notes.KINDS
    n = notes.add(tmp_path, "storyboard", {"kind": "look", "id": "style", "version": 2}, "Use the pencil style", "page")
    assert n["target"] == {"kind": "look", "id": "style", "version": 2} and n["text"] == "Use the pencil style"
    n = notes.add(tmp_path, "storyboard", {"kind": "look", "id": "set", "version": 2}, "Use the graph set", "page")
    assert n["target"]["id"] == "set"


def test_copied_is_a_note_kind(tmp_path):
    assert "copied" in notes.KINDS
    n = notes.add(tmp_path, "interview", {"kind": "copied", "id": "style", "version": 1}, "Use another set", "page")
    assert n["target"] == {"kind": "copied", "id": "style", "version": 1}
