"""The sample short, end to end, driven exactly as Claude drives it: the two command lines as subprocesses (CLI with
Blender's Python, BLENDER commands in background Blender), JSON on stdout or a BSA_RESULT line, one plain line on
stderr on failure. Integration bugs are fixed in the owning modules, never here."""

import html
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SAMPLE = ROOT / "tests" / "e2e" / "pendulum" / "source"
RESULT = "BSA_RESULT "

pytestmark = [pytest.mark.blender, pytest.mark.slow]


class Pipeline:
    def __init__(self, home: Path):
        self.home = home
        self.env = {**os.environ, "BSA_HOME": str(home)}
        self.env.pop("ELEVENLABS_API_KEY", None)
        self.env.pop("BSA_STYLE", None)
        where = self._run(["python", str(ROOT / "bsa_cli.py"), "where"], bootstrap=True)
        assert where["blender"] and where["blender_python"], where["problems"]
        self.blender, self.python = where["blender"], where["blender_python"]

    def _run(self, cmd, timeout=600, bootstrap=False):
        if bootstrap:  # any Python 3.11+ finds Blender; this test's own interpreter will do
            import sys

            cmd = [sys.executable, *cmd[1:]]
        proc = subprocess.run(cmd, check=False, capture_output=True, text=True, encoding="utf-8", errors="replace",
                              env=self.env, cwd=str(self.home), timeout=timeout)  # fmt: skip
        assert proc.returncode == 0, f"{cmd[-4:]} failed:\n{proc.stdout[-4000:]}\n{proc.stderr[-4000:]}"
        return json.loads(proc.stdout)

    def cli(self, *args) -> dict:
        return self._run([self.python, str(ROOT / "bsa_cli.py"), *map(str, args)])

    def blender_cmd(self, *args, blend: Path | None = None, timeout=1800) -> dict:
        cmd = [self.blender, "-b", *([str(blend)] if blend else []), "--factory-startup", "--python-exit-code", "1",
               "--python", str(ROOT / "bsa_blender.py"), "--", *map(str, args)]  # fmt: skip
        proc = subprocess.run(cmd, check=False, capture_output=True, text=True, encoding="utf-8", errors="replace",
                              env=self.env, cwd=str(self.home), timeout=timeout)  # fmt: skip
        assert proc.returncode == 0, f"{args} failed:\n{proc.stdout[-4000:]}\n{proc.stderr[-4000:]}"
        lines = [ln for ln in proc.stdout.splitlines() if ln.startswith(RESULT)]
        assert len(lines) == 1, proc.stdout[-4000:]
        return json.loads(lines[0][len(RESULT) :])

    def build(self, short: Path) -> None:
        cmd = [self.blender, "-b", "--factory-startup", "--python-exit-code", "1",
               "--python", str(short / "source" / "build.py"), "--", str(ROOT), str(short)]  # fmt: skip
        proc = subprocess.run(cmd, check=False, capture_output=True, text=True, encoding="utf-8", errors="replace",
                              env=self.env, cwd=str(self.home), timeout=600)  # fmt: skip
        assert proc.returncode == 0, proc.stdout[-4000:] + proc.stderr[-4000:]
        assert (short / "blend" / "short.blend").is_file()


def test_script_mode_end_to_end(tmp_path):
    home = tmp_path / "bsa_home"
    home.mkdir()
    p = Pipeline(home)
    workspace = tmp_path / "My Shorts é"
    p.cli("settings", "set", "workspace", workspace)
    p.cli("settings", "set", "narrator.provider", "none")
    p.cli("settings", "set", "render.engine", "eevee")
    p.cli("settings", "set", "handle", "example_user")  # an end card: the timeline holds the last shot for it

    short = Path(p.cli("new-short", "Pendulum")["short"])
    assert short.parent == workspace
    shutil.copyfile(SAMPLE / "short.json", short / "source" / "short.json")
    shutil.copyfile(SAMPLE / "build.py", short / "source" / "build.py")
    blend = short / "blend" / "short.blend"
    findings = []

    assert p.cli("validate", "pendulum")["ok"]
    p.cli("state", "pendulum", "done", "interview")
    p.cli("state", "pendulum", "done", "story")
    timeline = p.cli("timeline", "pendulum")
    p.build(short)
    stills = p.blender_cmd("stills", "pendulum", blend=blend)["stills"]
    assert len(stills) == 3 and all(Path(s).stat().st_size > 0 for s in stills)
    board = Path(p.cli("review", "storyboard", "pendulum")["page"])
    assert board == short / "review" / "storyboard.html" and board.is_file()
    p.cli("state", "pendulum", "done", "storyboard")
    p.cli("state", "pendulum", "done", "engine", "--info", json.dumps({"engine": "eevee"}))

    narration = p.cli("narrate", "pendulum")
    assert narration["status"] == "captions-only"
    timeline = narration["timeline"]
    p.build(short)  # rebuilt to the final timing
    mix = p.cli("mix", "pendulum")
    assert mix["n_frames"] == timeline["n_frames"]
    p.cli("state", "pendulum", "done", "build")

    check = p.blender_cmd("check", "pendulum", blend=blend)
    findings += check["findings"]
    render = p.blender_cmd("render", "pendulum", "--preset", "preview", blend=blend)
    assert render["engine"] == "eevee"
    assert render["anchors"]["labels"] == 1  # render placed the near label's object for the edit
    status = p.cli("render-status", "pendulum", "--preset", "preview")  # the live status, read after the render
    assert status["state"] == "done" and status["alive"] is False and status["stale"] is False
    assert status["frames"]["done"] == status["frames"]["total"] == timeline["n_frames"]
    assert status["settings"]["engine"] == "eevee" and status["settings"]["percent"] == 50
    assert status["checks"]["frames_checked"] >= 1 and status["checks"]["errors"] == 0
    assert all((short / "review" / sh["thumb"]).is_file() for sh in status["shots"] if sh["thumb"])
    assert Path(status["page"]) == short / "review" / "render-preview.html"
    assert "Done" in Path(status["page"]).read_text(encoding="utf-8")
    assert status["kind"] == "render" and status["previous"]["kind"] == "check"  # the check's run, superseded
    assert status["previous"]["state"] == "done"
    again = p.blender_cmd("render", "pendulum", "--preset", "preview", blend=blend)  # resumed: nothing to render
    assert again["status"] == status["status_file"]
    status = p.cli("render-status", "pendulum")
    assert status["frames"]["reused"] == timeline["n_frames"] and status["frames"]["rendered"] == 0
    anchors = json.loads((short / "source" / "anchors.json").read_text(encoding="utf-8"))
    points = anchors["shots"]["s02"]["Pendulum C bob"]["points"]
    s02 = next(s for s in timeline["shots"] if s["id"] == "s02")
    assert len(points) == s02["f1"] - s02["f0"] + 1 and all(p is not None for p in points)
    last = timeline["shots"][-1]  # the end card keeps off the last shot's heroes (the clock wheel)
    boxes = anchors["heroes"][last["id"]]["boxes"]
    assert render["anchors"]["heroes"] == len(boxes) >= 1
    assert all(len(b) == last["f1"] - last["f0"] + 1 for b in boxes.values())
    findings += p.blender_cmd("frame-check", "pendulum", "--preset", "preview")["findings"]
    edit = p.blender_cmd("edit", "pendulum", "--preset", "preview")
    status = p.cli("render-status", "pendulum", "--preset", "preview")  # the edit's own one-step run
    assert status["kind"] == "edit" and status["state"] == "done" and status["idle"] is True
    assert status["previous"]["kind"] == "frame-check" and status["previous"]["state"] == "done"
    (edit_step,) = status["run"]["steps"]
    assert edit_step["progress"]["done"] == edit_step["progress"]["total"] == timeline["n_frames"]

    assert "warnings" not in edit  # build.py is older than the scene file
    probe = p.blender_cmd("probe", edit["mp4"])
    assert [f for f in findings if f["severity"] == "error"] == []
    assert (probe["width"], probe["height"]) == (1080, 1920)
    assert probe["fps"] == pytest.approx(30.0)
    assert probe["frames"] == timeline["n_frames"]
    assert probe["has_audio"] is True
    assert (short / "review" / "storyboard.html").is_file()
    assert p.cli("state", "pendulum")["next"] == "final"

    # ---- only what changed renders again
    cache = short / "blend" / "short.fingerprints.json"
    before = json.loads(cache.read_text(encoding="utf-8"))["shots"]
    p.build(short)  # the same build.py again: a new .blend, but no shot's content changed
    same = p.blender_cmd("render", "pendulum", "--preset", "preview", blend=blend)
    assert json.loads(cache.read_text(encoding="utf-8"))["shots"] == before
    assert [(s["rendered"], s["why"]) for s in same["shots"]] == [(0, "unchanged")] * 3, same["shots"]
    check = p.blender_cmd("check", "pendulum", blend=blend)
    assert check["checked"] == [] and sorted(check["kept"]) == ["s01", "s02", "s03"]

    tweak = tmp_path / "tweak.py"  # move only shot s03's camera (the Clock scene)
    tweak.write_text("import bpy\nbpy.data.objects['Clock camera'].data.lens = 55.0\nbpy.ops.wm.save_mainfile()\n",
                     encoding="utf-8")  # fmt: skip
    proc = subprocess.run([p.blender, "-b", str(blend), "--factory-startup", "--python-exit-code", "1", "--python",
                           str(tweak)], check=False, capture_output=True, text=True, timeout=300)  # fmt: skip
    assert proc.returncode == 0, proc.stderr[-2000:]
    changed = p.blender_cmd("render", "pendulum", "--preset", "preview", blend=blend)
    by = {s["shot"]: s for s in changed["shots"]}
    assert by["s01"]["rendered"] == by["s02"]["rendered"] == 0
    assert by["s03"]["rendered"] == by["s03"]["frames"] and by["s03"]["why"] == "fingerprint changed"
    status = p.cli("render-status", "pendulum", "--preset", "preview")
    assert [sh["reason"] for sh in status["shots"]] == [None, None, "fingerprint changed"]
    assert "rendered again: fingerprint changed" in Path(status["page"]).read_text(encoding="utf-8")
    check = p.blender_cmd("check", "pendulum", blend=blend)
    assert check["checked"] == ["s03"] and sorted(check["kept"]) == ["s01", "s02"]

    # ---- frame-level redo
    s01 = next(s for s in timeline["shots"] if s["id"] == "s01")
    folder = short / "frames" / "preview" / "s01"
    times = {png.name: png.stat().st_mtime_ns for png in folder.glob("*.png")}
    a = s01["f0"] + 2
    redo = p.blender_cmd("render", "pendulum", "--preset", "preview", "--shot", "s01", "--frames", f"{a}-{a + 1}",
                         blend=blend)  # fmt: skip
    assert redo["redo"] == {"s01": f"{a}-{a + 1}"} and [s["shot"] for s in redo["shots"]] == ["s01"]
    now = {png.name: png.stat().st_mtime_ns for png in folder.glob("*.png")}
    assert sorted(n for n in now if now[n] != times[n]) == [f"{a:04d}.png", f"{a + 1:04d}.png"]
    s02 = next(s for s in timeline["shots"] if s["id"] == "s02")
    flagged = {"errors": 0, "warnings": 1, "findings": [{"check": "flicker", "severity": "warning", "obj": None,
               "frame": s02["f0"] + 5, "message": "flicker", "shot": "s02",
               "redo_frames": [s02["f0"] + 4, s02["f0"] + 5, s02["f0"] + 6]}]}  # fmt: skip
    (short / "review" / "frame-checks-preview.json").write_text(json.dumps(flagged), encoding="utf-8")
    redo = p.blender_cmd("render", "pendulum", "--preset", "preview", "--redo-flagged", blend=blend)
    assert redo["redo"] == {"s02": f"{s02['f0'] + 4}-{s02['f0'] + 6}"}
    frame_check = p.blender_cmd("frame-check", "pendulum", "--preset", "preview")
    for f in frame_check["findings"]:
        assert f["shot"] and (f["frame"] is None or f["redo"].startswith("BLENDER render"))
    cache.unlink()  # the edit fingerprints the .blend itself when no cache is current, then edits as before
    edit = p.blender_cmd("edit", "pendulum", "--preset", "preview")
    assert p.blender_cmd("probe", edit["mp4"])["frames"] == timeline["n_frames"]

    # ---- the post text, sound levels and a mix leave renders and checks current (issue #12)
    spec_path = short / "source" / "short.json"
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    spec["post"] = {"title": "Why a pendulum keeps time", "description": "Sources: x. Made in Blender.",
                    "hashtags": ["#physics", "#shorts"]}  # fmt: skip
    for sfx in spec.get("sfx", []):
        sfx["level_db"] -= 3
    spec["shots"][0]["picture"] += " Reworded."
    spec_path.write_text(json.dumps(spec, indent=2, ensure_ascii=False), encoding="utf-8")
    p.cli("mix", "pendulum")
    check = p.blender_cmd("check", "pendulum", blend=blend)
    assert check["checked"] == [] and sorted(check["kept"]) == ["s01", "s02", "s03"]
    same = p.blender_cmd("render", "pendulum", "--preset", "preview", blend=blend)
    assert [s["rendered"] for s in same["shots"]] == [0, 0, 0], same["shots"]
    p.blender_cmd("edit", "pendulum", "--preset", "preview")

    # ---- review final reads what check and frame-check wrote (issue #13)
    page = Path(p.cli("review", "final", "pendulum")["page"]).read_text(encoding="utf-8")
    for f in json.loads((short / "review" / "checks.json").read_text(encoding="utf-8"))["findings"]:
        assert f"shot{'s' if len(f['shots']) > 1 else ''} {', '.join(f['shots'])}" in page
    for f in frame_check["findings"]:
        if f.get("redo"):
            assert html.escape(f["redo"]) in page

    # ---- a refusal still prints its BSA_RESULT, with the error
    cmd = [p.blender, "-b", "--factory-startup", "--python-exit-code", "1", "--python", str(ROOT / "bsa_blender.py"),
           "--", "edit", "pendulum", "--preset", "final"]  # fmt: skip
    proc = subprocess.run(cmd, check=False, capture_output=True, text=True, encoding="utf-8", errors="replace",
                          env=p.env, cwd=str(p.home), timeout=600)  # fmt: skip
    assert proc.returncode != 0
    (line,) = [ln for ln in proc.stdout.splitlines() if ln.startswith(RESULT)]
    result = json.loads(line[len(RESULT) :])
    assert result["ok"] is False and "final" in result["error"] and result["error"] in proc.stderr
