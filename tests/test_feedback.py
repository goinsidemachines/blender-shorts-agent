"""Plugin feedback: problems in the plugin itself, noted locally and sent only if the user chooses (bsa.feedback)."""

import json
import os
import subprocess
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

from bsa import feedback, settings

ROOT = Path(__file__).resolve().parent.parent
CLI = ROOT / "bsa_cli.py"


@pytest.fixture
def short(tmp_path):
    sdir = tmp_path / "Shorts" / "03-watch-escapement"
    (sdir / "source").mkdir(parents=True)
    return sdir


# --------------------------------------------------------------------------- add, list, ask once


def test_add_writes_an_entry_with_context(short):
    e = feedback.add(short, "wrong-output", "flicker check flags a still frame", happened="it said frame 12 flickers",
                     expected="no finding", command="BLENDER frame-check 3", error="flicker at frame 12",
                     workaround="ignored it", stage="checks", blender="4.5.1", mode="script")
    data = json.loads((short / "review" / "plugin-feedback.json").read_text(encoding="utf-8"))
    assert data["entries"] == [e]
    assert e["id"] == "f1" and e["severity"] == "wrong-output" and e["summary"].startswith("flicker check")
    for key in ("time", "happened", "expected", "command", "error", "workaround", "stage"):
        assert e[key], key
    env = e["context"]
    assert env["plugin"] == json.loads((ROOT / ".claude-plugin" / "plugin.json").read_text())["version"]
    assert env["blender"] == "4.5.1" and env["mode"] == "script" and env["os"]
    assert e["status"] == "new"
    assert feedback.add(short, "rough-edge", "second")["id"] == "f2"


def test_add_refuses_an_unknown_severity_or_empty_summary(short):
    with pytest.raises(ValueError, match="blocking"):
        feedback.add(short, "minor", "x")
    with pytest.raises(ValueError):
        feedback.add(short, "blocking", "  ")


def test_ask_once_per_short(short):
    assert not feedback.should_ask(short, "ask")  # nothing noted
    feedback.add(short, "blocking", "edit crashed")
    feedback.add(short, "rough-edge", "message unclear")
    assert feedback.counts(feedback.pending(short)) == {"blocking": 1, "rough-edge": 1}
    assert feedback.should_ask(short, "ask")
    assert not feedback.should_ask(short, "never")
    feedback.decline(short)
    assert feedback.pending(short) == []
    assert not feedback.should_ask(short, "ask")
    assert {e["status"] for e in feedback.entries(short)} == {"declined"}
    feedback.add(short, "rough-edge", "noted after the question")
    assert not feedback.should_ask(short, "ask")  # asked once for this short already


def test_report_marks_entries_reported(short):
    feedback.add(short, "blocking", "edit crashed")
    feedback.report(short, mark=True)
    assert feedback.pending(short) == []
    assert {e["status"] for e in feedback.entries(short)} == {"reported"}
    assert not feedback.should_ask(short, "ask")


# --------------------------------------------------------------------------- redaction


def test_redact_paths(short, tmp_path):
    home = tmp_path
    ws = short.parent
    text = (f"failed at {short / 'blend' / 'short.blend'} in {ws} under {home / 'other'} "
            f"and {str(short).replace(os.sep, '/')}/x and {str(home).upper()}")
    out = feedback.redact(text, short_dir=short, workspace=ws, home=home)
    assert str(tmp_path) not in out and str(tmp_path).replace(os.sep, "/") not in out
    assert "03-watch-escapement" not in out
    assert "<short>" in out and "<workspace>" in out and "<home>" in out


def test_redact_keys_and_tokens():
    text = ("key sk-abcdefghijklmnopqrstuvwxyz123456 api_key=hunter2secret token: abc123 "
            "Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.abc hex 0123456789abcdef0123456789abcdef01 "
            "b64 QWxhZGRpbjpvcGVuIHNlc2FtZQ9876543210abcdXYZ ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789")
    out = feedback.redact(text)
    for secret in ("sk-abcdef", "hunter2secret", "abc123", "eyJhbGci", "0123456789abcdef", "QWxhZGRp", "ghp_ABCD"):
        assert secret not in out, secret
    assert "<redacted>" in out
    # plain words, ids and placeholders survive
    assert feedback.redact("frame_check_found_a_flicker_in_the_last_shot <short>") == \
        "frame_check_found_a_flicker_in_the_last_shot <short>"


def test_redact_removes_setting_values():
    out = feedback.redact("card said @myhandle and example.com, rule No people at all", scrub=["myhandle", "example.com",
                          "No people at all"])
    assert "myhandle" not in out and "example.com" not in out and "No people" not in out


def test_report_has_no_settings_values(short, bsa_home, monkeypatch):
    settings.set_value("handle", "secrethandle")
    settings.set_value("website", "mysite.example")
    settings.set_value("narrator.command", "python C:/tools/tts.py --voice v")
    feedback.add(short, "rough-edge", "end card showed @secrethandle on mysite.example",
                 command="python C:/tools/tts.py --voice v")
    rep = feedback.report(short, scrub=feedback.settings_scrub(settings.load_settings()))
    for word in ("secrethandle", "mysite.example", "tts.py"):
        assert word not in rep["body"] and word not in rep["url"]


# --------------------------------------------------------------------------- report and URL


def test_report_body_and_prefilled_url(short):
    feedback.add(short, "blocking", "edit crashed on a long path", happened=f"crash in {short}", stage="final",
                 error="OSError: path too long")
    rep = feedback.report(short)
    assert rep["title"].startswith("Plugin feedback")
    body = rep["body"]
    assert "edit crashed on a long path" in body and "blocking" in body and "Environment" in body
    assert str(short) not in body and "<short>" in body
    url = urlparse(rep["url"])
    assert f"{url.scheme}://{url.netloc}{url.path}" == f"{feedback.REPO}/issues/new"
    q = parse_qs(url.query)
    assert q["template"] == ["plugin_feedback.yml"] and q["labels"] == ["plugin-feedback"]
    assert q["title"] == [rep["title"]]
    assert "edit crashed on a long path" in q["entries"][0]
    assert not rep["shortened"]


def test_long_report_is_shortened_in_the_url(short):
    for i in range(40):
        feedback.add(short, "rough-edge", f"problem {i}", happened="x " * 300, error="y " * 100)
    rep = feedback.report(short)
    assert len(rep["url"]) <= feedback.URL_LIMIT
    assert rep["shortened"]
    q = parse_qs(urlparse(rep["url"]).query)
    assert "paste" in q["entries"][0].lower()
    assert "problem 39" in rep["body"]  # the full text is still on the page


def test_report_page_has_copy_and_github_button(short):
    feedback.add(short, "blocking", "edit crashed", happened="<script>alert(1)</script>")
    rep = feedback.report(short)
    page = feedback.render_page(rep, short / "review" / "plugin-feedback.html")
    html = page.read_text(encoding="utf-8")
    assert "Copy" in html and "Open on GitHub" in html
    assert "<script>alert(1)</script>" not in html
    assert "Submit" in html  # the user submits on GitHub themselves


def test_feedback_module_never_touches_the_network():
    src = (ROOT / "bsa" / "feedback.py").read_text(encoding="utf-8")
    for word in ("urllib.request", "http.client", "socket", "requests", "webbrowser"):
        assert word not in src, word


# --------------------------------------------------------------------------- setting


def test_feedback_setting(bsa_home):
    assert settings.load_settings()["feedback"] == "ask"
    assert settings.check_value("feedback", "Never") == "never"
    assert settings.check_value("feedback", "off") == "off"
    with pytest.raises(ValueError):
        settings.check_value("feedback", "sometimes")
    assert "feedback" not in settings.missing(settings.load_settings())  # never a first-run question


# --------------------------------------------------------------------------- CLI


def run(home: Path, *args) -> subprocess.CompletedProcess:
    env = {**os.environ, "BSA_HOME": str(home)}
    return subprocess.run([sys.executable, str(CLI), *map(str, args)], check=False, capture_output=True, text=True,
                          env=env, cwd=str(home), timeout=120)  # fmt: skip


def ok(proc) -> dict:
    assert proc.returncode == 0, proc.stdout + proc.stderr
    return json.loads(proc.stdout)


@pytest.fixture
def cli_short(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    ok(run(home, "settings", "set", "workspace", tmp_path / "ws"))
    sdir = Path(ok(run(home, "new-short", "Pendulum"))["short"])
    return home, sdir


def test_cli_add_list_report_decline(cli_short):
    home, sdir = cli_short
    out = ok(run(home, "feedback", "add", sdir, "--severity", "wrong-output", "--summary", "check is wrong",
                 "--happened", f"it flagged {sdir}", "--error", "bad", "--command", "CLI validate 1"))
    assert out["noted"] and out["entry"]["id"] == "f1"
    assert out["entry"]["stage"] == "interview"  # the stage the short is at, when not given
    lst = ok(run(home, "feedback", "list", sdir))
    assert lst["pending"] == 1 and lst["ask"] and lst["setting"] == "ask"
    assert lst["by_severity"] == {"wrong-output": 1}
    rep = ok(run(home, "feedback", "report", sdir))
    page = Path(rep["page"])
    assert page == sdir / "review" / "plugin-feedback.html" and page.is_file()
    assert rep["url"].startswith(feedback.REPO)
    assert not ok(run(home, "feedback", "list", sdir))["ask"]
    ok(run(home, "feedback", "add", sdir, "--severity", "rough-edge", "--summary", "later"))
    assert ok(run(home, "feedback", "decline", sdir))["declined"] == 1


def test_cli_setting_off_and_never(cli_short):
    home, sdir = cli_short
    ok(run(home, "settings", "set", "feedback", "off"))
    out = ok(run(home, "feedback", "add", sdir, "--severity", "blocking", "--summary", "x"))
    assert not out["noted"]
    assert not (sdir / "review" / "plugin-feedback.json").exists()
    ok(run(home, "settings", "set", "feedback", "never"))
    assert ok(run(home, "feedback", "add", sdir, "--severity", "blocking", "--summary", "x"))["noted"]
    lst = ok(run(home, "feedback", "list", sdir))
    assert lst["pending"] == 1 and not lst["ask"]
    bad = run(home, "feedback", "add", sdir, "--severity", "huge", "--summary", "x")
    assert bad.returncode != 0


# --------------------------------------------------------------------------- docs and issue form


def _flat(path: Path) -> str:
    return " ".join(path.read_text(encoding="utf-8").split())


def test_skill_says_when_to_note_feedback():
    text = _flat(ROOT / "skills" / "make-short" / "SKILL.md")
    assert "feedback add" in text and "never stop the short" in text
    assert "your own scene" in text or "the user's own" in text


def test_post_chapter_asks_once_and_never_submits():
    text = _flat(ROOT / "skills" / "make-short" / "playbook" / "09-post.md")
    for words in ("feedback list", "feedback report SHORT --open", "feedback decline", "Submit", "Never submit"):
        assert words in text, words


def test_first_run_mentions_the_setting_without_asking():
    text = _flat(ROOT / "skills" / "make-short" / "playbook" / "00-first-run.md")
    assert "settings set feedback never|off" in text


def test_readme_says_nothing_is_sent():
    text = _flat(ROOT / "README.md")
    assert "prefilled GitHub issue" in text


def test_issue_form_matches_the_report():
    form = (ROOT / ".github" / "ISSUE_TEMPLATE" / feedback.TEMPLATE).read_text(encoding="utf-8")
    assert "labels: [plugin-feedback]" in form
    for field in ("id: entries", "id: environment"):
        assert field in form, field
    for words in ("blocking", "wrong-output", "rough-edge", "Workaround", "Error line"):
        assert words in form, words
