"""The pure parts of bsa.blender.controls (no Blender): channel slots and names."""

import pytest

from bsa.blender import controls


def test_empties_needed_for_n_channels():
    assert [controls.n_controls(n) for n in (0, 1, 3, 4, 6, 7)] == [0, 1, 1, 2, 2, 3]


def test_slots_fill_x_y_z_then_the_next_empty():
    ch = ["a", "b", "c", "d", "e"]
    assert [controls.slot(ch, n) for n in ch] == [(0, 0), (0, 1), (0, 2), (1, 0), (1, 1)]


def test_unknown_and_duplicate_channels_are_refused():
    with pytest.raises(KeyError, match="no channel 'z'"):
        controls.slot(["a"], "z")
    with pytest.raises(ValueError, match="unique"):
        controls.slot(["a", "a"], "a")


def test_names():
    assert controls.control_name("Wave", 0, 1) == "Wave control"
    assert controls.control_name("Wave", 1, 2) == "Wave control 2"
    assert [controls.socket_name(i) for i in range(2)] == ["Control 1", "Control 2"]
