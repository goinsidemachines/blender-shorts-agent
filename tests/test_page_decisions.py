import json
import os
import threading

import pytest

from bsa import jobstate
from bsa.review import decisions, versions
from tests import test_cli
from tests.test_cli import ok, run

home, workspace, pendulum = test_cli.home, test_cli.workspace, test_cli.pendulum  # the CLI tests' fixtures


def test_versions_count_up_only_on_visible_change(tmp_path):
    a, b = {"lines": ["one"]}, {"lines": ["one."]}

    def v(c):
        return versions.version_of(tmp_path, "story", versions.fingerprint(c, tmp_path))

    assert (v(a), v(a), v(b), v(a)) == (1, 1, 2, 1)


def test_file_bytes_are_part_of_the_fingerprint(tmp_path):
    (tmp_path / "stills").mkdir()
    f = tmp_path / "stills" / "s01.png"
    f.write_bytes(b"a")
    c = {"still": {"file": "stills/s01.png"}}
    one = versions.fingerprint(c, tmp_path)
    f.write_bytes(b"bb")
    assert versions.fingerprint(c, tmp_path) != one


def test_missing_file_is_part_of_the_fingerprint(tmp_path):
    c = {"still": {"file": "stills/s01.png"}}
    gone = versions.fingerprint(c, tmp_path)
    (tmp_path / "stills").mkdir()
    (tmp_path / "stills" / "s01.png").write_bytes(b"a")
    assert versions.fingerprint(c, tmp_path) != gone


def test_same_size_rewrite_is_noticed(tmp_path):
    f = tmp_path / "x.png"
    f.write_bytes(b"a")
    c = {"f": [{"file": "x.png"}]}
    one = versions.fingerprint(c, tmp_path)
    f.write_bytes(b"b")
    st = f.stat()
    os.utime(f, ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000))  # a real edit later moves the mtime
    assert versions.fingerprint(c, tmp_path) != one


def test_record_and_refusals(tmp_path):
    d = decisions.record(tmp_path, "story", 2, "approve", "page", current=2)
    assert d == {**d, "stage": "story", "version": 2, "decision": "approve", "by": "page", "applied": False}
    assert d["id"] == "d1" and d["superseded"] is False and d["choice"] is None
    with pytest.raises(decisions.AlreadyAnswered, match="already approved on the page"):
        decisions.record(tmp_path, "story", 2, "approve", "chat", current=2)
    with pytest.raises(decisions.StaleVersion, match="version 1 of the Story.*now version 2"):
        decisions.record(tmp_path, "story", 1, "approve", "page", current=2)
    with pytest.raises(decisions.DecisionRefused):
        decisions.record(tmp_path, "engine", 1, "approve", "page", current=1)  # no choice
    with pytest.raises(decisions.DecisionRefused):
        decisions.record(tmp_path, "story", 2, "approve", "settings", current=2)


def test_engine_approve_needs_a_known_engine(tmp_path):
    with pytest.raises(decisions.DecisionRefused):
        decisions.record(tmp_path, "engine", 1, "approve", "page", current=1, choice={"engine": "luxcore"})
    d = decisions.record(tmp_path, "engine", 1, "approve", "settings", current=1, choice={"engine": "cycles"})
    assert d["choice"] == {"engine": "cycles"}


def test_bad_stage_decision_or_author(tmp_path):
    for args in (("nope", 1, "approve", "page"), ("story", 1, "maybe", "page"), ("story", 1, "approve", "bot")):
        with pytest.raises(decisions.DecisionRefused):
            decisions.record(tmp_path, *args, current=1)
    assert decisions.load(tmp_path) == []


def test_changes_then_approve_same_version_is_allowed(tmp_path):
    decisions.record(tmp_path, "storyboard", 1, "changes", "page", current=1)
    assert decisions.record(tmp_path, "storyboard", 1, "approve", "chat", current=1)["by"] == "chat"


def test_supersede_frees_the_version(tmp_path):
    decisions.record(tmp_path, "story", 1, "approve", "page", current=1)
    assert decisions.supersede(tmp_path, ["story", "storyboard"]) == 1
    assert decisions.approved(tmp_path, "story", 1) is None
    decisions.record(tmp_path, "story", 1, "approve", "page", current=1)
    assert decisions.supersede(tmp_path, ["storyboard"]) == 0


def test_latest_approved_and_applied(tmp_path):
    assert decisions.latest(tmp_path, "story") is None
    decisions.record(tmp_path, "story", 1, "changes", "page", current=1, now=0)
    d2 = decisions.record(tmp_path, "story", 1, "approve", "page", current=1, now=60)
    assert decisions.latest(tmp_path, "story")["id"] == d2["id"]
    assert decisions.approved(tmp_path, "story", 1)["id"] == d2["id"]
    assert decisions.approved(tmp_path, "story", 2) is None
    assert [d["id"] for d in decisions.unapplied(tmp_path)] == ["d1", "d2"]
    assert decisions.mark_applied(tmp_path, "d2")["applied"] is True
    assert [d["id"] for d in decisions.unapplied(tmp_path)] == ["d1"]
    with pytest.raises(ValueError):
        decisions.mark_applied(tmp_path, "d9")


def test_unapplied_skips_superseded(tmp_path):
    decisions.record(tmp_path, "story", 1, "approve", "page", current=1)
    decisions.supersede(tmp_path, ["story"])
    assert decisions.unapplied(tmp_path) == []


def test_concurrent_approvals_record_one(tmp_path):  # Review Focus 1
    results = []

    def go(by):
        try:
            results.append(decisions.record(tmp_path, "story", 1, "approve", by, current=1))
        except decisions.AlreadyAnswered as e:
            results.append(e)

    ts = [threading.Thread(target=go, args=(b,)) for b in ("page", "chat") * 4]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert sum(isinstance(r, dict) for r in results) == 1
    assert len([d for d in decisions.load(tmp_path) if d["decision"] == "approve"]) == 1


def _cli_one_take(home, short, one_take):
    if one_take:
        path = short / "source" / "short.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        data["camera"] = {"style": "one-take", "seconds": 20}
        for shot, at in zip(data["shots"], (0, 6, 13), strict=True):
            shot["at"] = at
        path.write_text(json.dumps(data), encoding="utf-8")
    ok(run(home, "state", short.name, "done", "interview"))
    for stage in ("story", "storyboard"):
        ok(run(home, "page", "decide", short.name, stage, "approve", "--by", "chat"))


def test_approving_the_camera_marks_it_done(home, pendulum):
    _cli_one_take(home, pendulum, True)
    assert ok(run(home, "state", "pendulum"))["next"] == "camera"
    out = ok(run(home, "page", "decide", "pendulum", "camera", "approve", "--by", "chat"))
    assert out["decision"]["applied"] is True and out["state"]["next"] == "engine"
    assert jobstate.load_state(pendulum)["stages"]["camera"]["version"] == 1


def test_the_camera_of_a_short_without_the_stage_is_refused(home, pendulum):
    _cli_one_take(home, pendulum, False)
    proc = run(home, "page", "decide", "pendulum", "camera", "approve", "--by", "chat")
    assert proc.returncode == 1 and "not a stage of this short" in proc.stderr and "Traceback" not in proc.stderr
    proc = run(home, "state", "pendulum", "done", "camera")
    assert proc.returncode == 1 and "not a stage of this short" in proc.stderr
    assert "camera" not in jobstate.load_state(pendulum)["stages"]
