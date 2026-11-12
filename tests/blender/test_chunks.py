"""Two render chunks into one folder, in real Blender: the second renders only the frames the first left."""

import tempfile
import time
from pathlib import Path

from bsa import chunks
from bsa.blender import render
from tests.blender.test_scene_render import _tiny_scene


def test_two_chunks_render_one_folder_once_each():
    sc = _tiny_scene(preset="preview")
    out = Path(tempfile.mkdtemp()) / "s01"
    n_frames, size = 6, 4
    first, last, n = chunks.take(chunks.missing(out, 1, n_frames), size)
    assert (first, last, n) == (1, 4, 4)
    render.render_range(sc, first, last, out, "preview", percent=5)
    pngs = {p.name: p.stat().st_mtime_ns for p in out.glob("*.png")}
    assert sorted(pngs) == ["0001.png", "0002.png", "0003.png", "0004.png"]
    time.sleep(0.05)
    todo = chunks.missing(out, 1, n_frames)  # what a fresh Blender plans from the folder
    assert todo == [5, 6]
    first, last, n = chunks.take(todo, size)
    render.render_range(sc, first, last, out, "preview", percent=5)
    after = {p.name: p.stat().st_mtime_ns for p in out.glob("*.png")}
    assert sorted(after) == [f"{f:04d}.png" for f in range(1, 7)]
    assert all(after[k] == v for k, v in pngs.items())  # the first chunk's frames were not rendered again
    assert chunks.missing(out, 1, n_frames) == []
