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


def test_corrupt_state_set_aside(tmp_path):
    (tmp_path / "state.json").write_text("{broken", encoding="utf-8")
    assert jobstate.load_state(tmp_path)["stages"] == {}
    kept = list(tmp_path.glob("state.corrupt-*.json"))
    assert len(kept) == 1
    assert kept[0].read_text(encoding="utf-8") == "{broken"
    jobstate.mark_done(tmp_path, "interview")
    assert list(jobstate.load_state(tmp_path)["stages"]) == ["interview"]


def test_non_object_state_set_aside(tmp_path):
    (tmp_path / "state.json").write_text("[1]", encoding="utf-8")
    assert jobstate.load_state(tmp_path)["stages"] == {}
    assert len(list(tmp_path.glob("state.corrupt-*.json"))) == 1


def test_oserror_on_read_propagates_and_keeps_file(tmp_path, monkeypatch):
    import pathlib

    import pytest

    jobstate.mark_done(tmp_path, "interview")
    real = pathlib.Path.read_text

    def locked(self, *a, **k):
        if self.name == "state.json":
            raise PermissionError("locked")
        return real(self, *a, **k)

    monkeypatch.setattr(pathlib.Path, "read_text", locked)
    with pytest.raises(PermissionError):
        jobstate.mark_done(tmp_path, "story")
    monkeypatch.setattr(pathlib.Path, "read_text", real)
    assert list(jobstate.load_state(tmp_path)["stages"]) == ["interview"]
    assert not list(tmp_path.glob("state.corrupt-*.json"))
