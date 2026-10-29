"""source/events.json: build.py writes the simulation's sound events, and the mix plays a clip of each one's kind."""

import json

import pytest

from bsa.audio import events
from bsa.short import Sfx
from bsa.timeline import Span, Timeline


def _tl():
    return Timeline(30, 120, [Span("s01", 1, 60), Span("s02", 61, 120)], [], [])


def test_events_become_sfx_in_their_shot():
    got = events.to_sfx([{"t": 0.5, "kind": "click"}, {"frame": 91, "kind": "thud", "gain": 0.5},
                         {"shot": "s02", "t": 0.2, "kind": "click", "dur_s": 0.2, "level_db": -24}], _tl())  # fmt: skip
    assert got[0] == Sfx("click", "s01", 0.5, events.DUR_S, events.LEVEL_DB)
    assert got[1] == Sfx("click", "s02", 0.2, 0.2, -24)  # in time order
    assert got[2].shot == "s02" and got[2].at_s == pytest.approx(1.0) and got[2].kind == "thud"
    assert got[2].level_db == pytest.approx(events.LEVEL_DB - 6.02, abs=0.01)


def test_close_events_merge_and_quiet_ones_drop():
    got = events.to_sfx([{"t": 1.0, "kind": "click"}, {"t": 1.005, "kind": "click"}, {"t": 1.005, "kind": "thud"},
                         {"t": 1.5, "kind": "click", "gain": 0.01}], _tl())  # fmt: skip
    assert [(s.kind, s.at_s) for s in got] == [("click", 1.0), ("thud", 1.005)]


def test_bad_events_are_refused_with_the_index():
    with pytest.raises(ValueError, match=r"events\[0\].*kind"):
        events.to_sfx([{"t": 1.0, "kind": "boing"}], _tl())
    with pytest.raises(ValueError, match=r"events\[0\].*t.*frame"):
        events.to_sfx([{"kind": "click"}], _tl())
    with pytest.raises(ValueError, match=r"events\[0\].*outside"):
        events.to_sfx([{"t": 9.0, "kind": "click"}], _tl())


def test_load_reads_a_list_or_a_dict_with_events(tmp_path):
    p = tmp_path / "events.json"
    assert events.load(p) == []
    p.write_text(json.dumps([{"t": 1, "kind": "click"}]), encoding="utf-8")
    assert events.load(p) == [{"t": 1, "kind": "click"}]
    p.write_text(json.dumps({"events": [{"frame": 3, "kind": "thud"}]}), encoding="utf-8")
    assert events.load(p) == [{"frame": 3, "kind": "thud"}]
