"""`BLENDER check` saves each shot's findings as it finishes, so a check that was killed (a PC restart) is resumed:
the next run checks only the shots without a result."""

import json
import shutil
import sys
import types
from pathlib import Path

import pytest

import bsa_blender
from bsa import freshness
from bsa.image_checks import Finding
from bsa.timeline import Span, Timeline

SAMPLE = Path(__file__).resolve().parent / "e2e" / "pendulum" / "source" / "short.json"


class _Run:
    def progress(self, *a, **kw):
        pass

    def summarize(self, *a, **kw):
        pass


class _Killed(Exception):
    pass


@pytest.fixture
def check_env(tmp_path, monkeypatch, bsa_home):
    sdir = tmp_path / "pendulum"
    (sdir / "source").mkdir(parents=True)
    shutil.copyfile(SAMPLE, sdir / "source" / "short.json")
    short = bsa_blender._load(sdir)
    spans = [Span(s.id, 1 + 30 * i, 30 * (i + 1)) for i, s in enumerate(short.shots)]
    tl = Timeline(fps=30, n_frames=30 * len(spans), shots=spans, lines=[], cards=[])
    calls: list[str] = []
    state = {"die_after": None}

    def fake_run_checks(scene, timeline, **kw):
        sid = timeline.shots[0].id
        calls.append(sid)
        if state["die_after"] is not None and len(calls) > state["die_after"]:
            raise _Killed(sid)
        return [Finding("faceted", "warning", "Bob", timeline.shots[0].f0, f"looks faceted in {sid}")]

    checks = types.ModuleType("bsa.blender.checks")
    checks.run_checks, checks.OVERLAP_MAX_FINDINGS = fake_run_checks, 50
    camera = types.ModuleType("bsa.blender.camera")
    camera.framing_findings = lambda *a, **k: []
    monkeypatch.setitem(sys.modules, "bsa.blender.checks", checks)
    monkeypatch.setitem(sys.modules, "bsa.blender.camera", camera)
    monkeypatch.setattr(bsa_blender, "_timeline", lambda *_: (tl, "tlhash"))
    monkeypatch.setattr(bsa_blender, "_open_blend", lambda *_: None)
    monkeypatch.setattr(bsa_blender, "_scene", lambda d, s, sid, h: (None, types.SimpleNamespace(name="Scene")))
    monkeypatch.setattr(bsa_blender, "_fingerprints", lambda *a, **k: {s.id: f"fp-{s.id}" for s in short.shots})
    monkeypatch.setattr(bsa_blender, "_parts", lambda *a: {})
    monkeypatch.setattr(bsa_blender, "_objects", lambda *a: {})
    args = types.SimpleNamespace(every=5, full=False, in_chain=False)
    return types.SimpleNamespace(sdir=sdir, short=short, args=args, calls=calls, state=state, tl=tl)


def _check(env):
    return bsa_blender._cmd_check(env.args, env.sdir, env.short, _Run())


def test_a_killed_check_keeps_the_shots_it_finished(check_env):
    check_env.state["die_after"] = 2
    with pytest.raises(_Killed):
        _check(check_env)
    data = json.loads((check_env.sdir / "review" / "checks.json").read_text(encoding="utf-8"))
    ids = [s.id for s in check_env.short.shots]
    assert data["incomplete"] is True and data["unchecked"] == ids[2:]
    assert sorted(data["stamp"]["shots"]) == sorted(ids[:2])
    assert {f["shot"] for f in data["findings"]} == set(ids[:2])


def test_the_rerun_checks_only_the_unfinished_shots(check_env):
    check_env.state["die_after"] = 2
    with pytest.raises(_Killed):
        _check(check_env)
    check_env.calls.clear()
    check_env.state["die_after"] = None
    out = _check(check_env)
    ids = [s.id for s in check_env.short.shots]
    assert check_env.calls == ids[2:]
    assert out["kept"] == ids[:2] and out["checked"] == ids[2:]
    assert {f["shot"] for f in out["findings"]} == set(ids)
    assert "incomplete" not in out and "unchecked" not in out


def test_a_partial_check_does_not_pass_for_a_complete_one(check_env):
    check_env.state["die_after"] = 1
    with pytest.raises(_Killed):
        _check(check_env)
    tl_hash = "tlhash"
    keys = bsa_blender._check_keys(check_env.short, check_env.tl, bsa_blender._fingerprints())
    with pytest.raises(Exception, match="checks"):
        freshness.require_checks(check_env.sdir, tl_hash, keys)


def test_a_kept_shot_after_the_killed_one_is_not_lost(check_env, monkeypatch):
    _check(check_env)  # every shot checked and saved
    ids = [s.id for s in check_env.short.shots]
    changed = {**{i: f"fp-{i}" for i in ids}, ids[0]: "new", ids[1]: "new"}
    monkeypatch.setattr(bsa_blender, "_fingerprints", lambda *a, **k: changed)
    check_env.calls.clear()
    check_env.state["die_after"] = 1  # the first changed shot is saved, the second dies
    with pytest.raises(_Killed):
        _check(check_env)
    data = json.loads((check_env.sdir / "review" / "checks.json").read_text(encoding="utf-8"))
    assert sorted(data["stamp"]["shots"]) == sorted([ids[0], ids[2]])
    assert {f["shot"] for f in data["findings"]} == {ids[0], ids[2]}
