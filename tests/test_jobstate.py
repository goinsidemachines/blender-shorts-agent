import json
from pathlib import Path

import pytest

from bsa import jobstate


def test_fresh_state(tmp_path):
    assert jobstate.next_stage(tmp_path) == "interview"
    assert jobstate.load_state(tmp_path)["stages"] == {}


def test_next_stage_and_reset(tmp_path):
    for st in ("interview", "story", "storyboard"):
        jobstate.mark_done(tmp_path, st)
    assert jobstate.next_stage(tmp_path) == "engine"
    jobstate.reset_from(tmp_path, "story")
    assert jobstate.next_stage(tmp_path) == "story"
    assert "interview" in jobstate.load_state(tmp_path)["stages"]
    assert "storyboard" not in jobstate.load_state(tmp_path)["stages"]


def test_mark_done_records_time_and_info(tmp_path):
    jobstate.mark_done(tmp_path, "engine", choice="cycles")
    rec = jobstate.load_state(tmp_path)["stages"]["engine"]
    assert rec["choice"] == "cycles"
    assert "T" in rec["at"]
    assert (tmp_path / "state.json").exists()
    assert not list(tmp_path.glob("*.tmp"))


def test_all_done_returns_none(tmp_path):
    for st in jobstate.STAGES:
        jobstate.mark_done(tmp_path, st)
    assert jobstate.next_stage(tmp_path) is None


def test_unknown_stage_rejected(tmp_path):
    import pytest

    with pytest.raises(ValueError):
        jobstate.mark_done(tmp_path, "bogus")
    with pytest.raises(ValueError):
        jobstate.reset_from(tmp_path, "bogus")


def test_approvals_subset():
    assert jobstate.APPROVALS <= set(jobstate.STAGES)


def _two_stages(tmp_path):
    jobstate.mark_done(tmp_path, "interview")
    jobstate.mark_done(tmp_path, "story")
    return (tmp_path / "state.json").read_bytes()


def _tmp_files(folder):
    return [p for p in folder.iterdir() if p.name.endswith(".tmp")]


def test_every_save_keeps_the_previous_good_copy(tmp_path):
    jobstate.mark_done(tmp_path, "interview")
    assert not (tmp_path / "state.json.bak").exists()  # nothing before the first save
    first = (tmp_path / "state.json").read_bytes()
    jobstate.mark_done(tmp_path, "story")
    assert (tmp_path / "state.json.bak").read_bytes() == first
    assert list(jobstate.load_state(tmp_path)["stages"]) == ["interview", "story"]
    assert not _tmp_files(tmp_path)


def test_save_is_atomic_and_synced(tmp_path, monkeypatch):
    """The new state goes to a temp file in the same folder, is fsynced, then replaces state.json."""
    import os

    synced, replaced = [], []
    real_fsync, real_replace = os.fsync, os.replace
    monkeypatch.setattr(jobstate.os, "fsync", lambda fd: synced.append(fd) or real_fsync(fd))
    monkeypatch.setattr(jobstate.os, "replace", lambda a, b: replaced.append((Path(a), Path(b))) or real_replace(a, b))
    jobstate.mark_done(tmp_path, "interview")
    assert synced
    assert [(a.parent, b.name) for a, b in replaced] == [(tmp_path, "state.json")]
    assert replaced[0][0].name != "state.json"


def test_failed_write_leaves_state_json_as_it_was(tmp_path, monkeypatch):
    good = _two_stages(tmp_path)

    def boom(fd):
        raise OSError("disk full")

    monkeypatch.setattr(jobstate.os, "fsync", boom)
    with pytest.raises(OSError, match="disk full"):
        jobstate.mark_done(tmp_path, "storyboard")
    assert (tmp_path / "state.json").read_bytes() == good
    assert not _tmp_files(tmp_path)


@pytest.mark.parametrize(("content", "why"), [
    (b"", "empty"), (b"\0" * 593, "593 zero bytes"), (b'{"stages": {"interview"', "not valid JSON"),
    (b"[1]", "not a JSON object"), (b"\xff\xfe{}", "not valid JSON")])  # fmt: skip
def test_damaged_state_stops_and_names_the_backup(tmp_path, content, why):
    _two_stages(tmp_path)
    (tmp_path / "state.json").write_bytes(content)
    with pytest.raises(jobstate.StateDamaged) as err:
        jobstate.load_state(tmp_path)
    msg = str(err.value)
    assert "state.json was damaged" in msg and why in msg
    assert "state.json.bak" in msg and f"CLI state {tmp_path.name} restore" in msg and "backup from" in msg
    with pytest.raises(jobstate.StateDamaged):
        jobstate.next_stage(tmp_path)
    with pytest.raises(jobstate.StateDamaged):  # never written over, never reset
        jobstate.mark_done(tmp_path, "storyboard")
    assert (tmp_path / "state.json").read_bytes() == content
    assert not list(tmp_path.glob("state.corrupt-*"))


def test_damaged_state_without_a_backup_says_so(tmp_path):
    (tmp_path / "state.json").write_bytes(b"\0\0\0")
    with pytest.raises(jobstate.StateDamaged, match="no backup") as err:
        jobstate.load_state(tmp_path)
    assert "restore" not in str(err.value)
    with pytest.raises(jobstate.StateDamaged, match="no backup"):
        jobstate.restore(tmp_path)


def test_missing_state_with_a_backup_is_damage_too(tmp_path):
    _two_stages(tmp_path)
    (tmp_path / "state.json").unlink()
    with pytest.raises(jobstate.StateDamaged, match="missing"):
        jobstate.load_state(tmp_path)
    assert list(jobstate.restore(tmp_path)["stages"]) == ["interview"]


def test_restore_puts_the_backup_back_and_keeps_the_damaged_file(tmp_path):
    _two_stages(tmp_path)
    (tmp_path / "state.json").write_bytes(b"\0" * 40)
    state = jobstate.restore(tmp_path)
    assert list(state["stages"]) == ["interview"]  # the copy before the last save
    assert list(jobstate.load_state(tmp_path)["stages"]) == ["interview"]
    kept = list(tmp_path.glob("state.damaged-*.json"))
    assert len(kept) == 1 and kept[0].read_bytes() == b"\0" * 40
    jobstate.mark_done(tmp_path, "story")
    assert jobstate.next_stage(tmp_path) == "storyboard"


def test_restore_refuses_when_state_is_fine(tmp_path):
    good = _two_stages(tmp_path)
    with pytest.raises(jobstate.StateDamaged, match="not damaged"):
        jobstate.restore(tmp_path)
    assert (tmp_path / "state.json").read_bytes() == good


def test_a_damaged_backup_is_never_offered(tmp_path):
    _two_stages(tmp_path)
    (tmp_path / "state.json.bak").write_bytes(b"\0" * 10)
    (tmp_path / "state.json").write_bytes(b"")
    with pytest.raises(jobstate.StateDamaged, match="no backup"):
        jobstate.load_state(tmp_path)


def test_oserror_on_read_propagates_and_keeps_file(tmp_path, monkeypatch):
    import pathlib

    import pytest

    jobstate.mark_done(tmp_path, "interview")
    real = pathlib.Path.read_bytes

    def locked(self, *a, **k):
        if self.name == "state.json":
            raise PermissionError("locked")
        return real(self, *a, **k)

    monkeypatch.setattr(pathlib.Path, "read_bytes", locked)
    with pytest.raises(PermissionError):
        jobstate.mark_done(tmp_path, "story")
    monkeypatch.setattr(pathlib.Path, "read_bytes", real)
    assert list(jobstate.load_state(tmp_path)["stages"]) == ["interview"]
    assert [p.name for p in tmp_path.iterdir()] == ["state.json"]


PENDULUM = Path(__file__).resolve().parent / "e2e" / "pendulum" / "source" / "short.json"


def _short_with_camera(folder: Path, camera: dict | None) -> Path:
    data = json.loads(PENDULUM.read_text(encoding="utf-8"))
    if camera is not None:
        data["camera"] = camera
    (folder / "source").mkdir(parents=True, exist_ok=True)
    (folder / "source" / "short.json").write_text(json.dumps(data), encoding="utf-8")
    return folder


@pytest.mark.parametrize("camera", [None, {"style": "cuts"}, {"approve": False}])
def test_a_plain_short_has_no_camera_stage(tmp_path, camera):
    sdir = _short_with_camera(tmp_path, camera)
    assert "camera" not in jobstate.wanted_stages(sdir)
    for st in ("interview", "story", "storyboard"):
        jobstate.mark_done(sdir, st)
    assert jobstate.next_stage(sdir) == "engine"


@pytest.mark.parametrize("camera", [{"approve": True}, {"style": "one-take", "seconds": 13}])
def test_the_camera_stage_is_wanted_only_when_asked_or_one_take(tmp_path, camera):
    sdir = _short_with_camera(tmp_path, camera)
    wanted = jobstate.wanted_stages(sdir)
    assert wanted[wanted.index("storyboard") + 1 : wanted.index("storyboard") + 3] == ["camera", "engine"]
    for st in ("interview", "story", "storyboard"):
        jobstate.mark_done(sdir, st)
    assert jobstate.next_stage(sdir) == "camera"
    jobstate.mark_done(sdir, "camera")
    assert jobstate.next_stage(sdir) == "engine"


def test_an_unreadable_short_has_no_camera_stage(tmp_path):
    assert jobstate.wanted_stages(tmp_path) == [s for s in jobstate.STAGES if s != "camera"]
    (tmp_path / "source").mkdir()
    (tmp_path / "source" / "short.json").write_text("{not json", encoding="utf-8")
    assert "camera" not in jobstate.wanted_stages(tmp_path)
    assert jobstate.STAGES.index("camera") == jobstate.STAGES.index("storyboard") + 1 and "camera" in jobstate.APPROVALS
