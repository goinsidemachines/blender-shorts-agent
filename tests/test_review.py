"""What the one review page reads through bsa.review.pages (findings, the closing card), and the retired pages are
gone for good."""

import json
import re
from pathlib import Path

from bsa.review import pages

TEMPLATES = Path(pages.__file__).parent / "templates"


def test_no_side_tab_borders():
    side = re.compile(r"border-(left|right|inline-start|inline-end)(-[a-z]+)?\s*:")  # any one-side border property
    for f in TEMPLATES.rglob("*.*"):  # the page parts live in templates/page/
        found = side.search(f.read_text(encoding="utf-8"))
        assert found is None, f"{f.name}: {found.group(0)}"


def test_the_separate_review_pages_are_retired():
    assert sorted(p.name for p in TEMPLATES.iterdir()) == ["page"]
    for gone in ("render_story", "render_storyboard", "render_final", "render_status_page", "open_page"):
        assert not hasattr(pages, gone), gone


def test_load_findings_errors_first_and_preview_checks_dropped_once_final_exist(tmp_path):
    review = tmp_path / "review"
    review.mkdir()
    (review / "checks.json").write_text(json.dumps({"findings": [
        {"check": "faceted", "severity": "warning", "obj": "Bob", "frame": 3, "message": "looks faceted",
         "shot": "s01", "added_later": {"x": 1}},
        "not a finding", {"severity": "error"}]}), encoding="utf-8")  # fmt: skip
    (review / "frame-checks-preview.json").write_text(json.dumps({"findings": [
        {"check": "flicker", "severity": "warning", "message": "preview flicker"}]}), encoding="utf-8")  # fmt: skip
    found, redo_all = pages.load_findings(tmp_path)
    assert [f.check for f in found] == ["faceted", "flicker"] and redo_all == []
    assert found[0].shots == ("s01",) and found[0].frame == 3 and found[0].obj == "Bob"
    (review / "frame-checks-final.json").write_text(json.dumps({"findings": [
        {"check": "black_frame", "severity": "error", "message": "black", "shots": ["s01", "s02"],
         "redo": "BLENDER render ..."}], "redo_all": "BLENDER render SHORT --redo-flagged"}), encoding="utf-8")  # fmt: skip
    found, redo_all = pages.load_findings(tmp_path)
    assert [f.check for f in found] == ["black_frame", "faceted"]  # errors first; the preview's checks left out
    assert found[0].shots == ("s01", "s02") and found[0].redo and redo_all == ["BLENDER render SHORT --redo-flagged"]


def test_closing_info_reads_the_card_and_its_notes(tmp_path):
    from bsa import closing

    assert pages.closing_info(tmp_path) is None
    (tmp_path / closing.CARD_FILE).write_bytes(b"png")
    (tmp_path / closing.INFO_FILE).write_text("{not json", encoding="utf-8")
    assert pages.closing_info(tmp_path) is None
    (tmp_path / closing.INFO_FILE).write_text(json.dumps({"seconds": 2.5, "warnings": ["w"]}), encoding="utf-8")
    assert pages.closing_info(tmp_path) == {"seconds": 2.5, "warnings": ["w"], "png": tmp_path / closing.CARD_FILE}
