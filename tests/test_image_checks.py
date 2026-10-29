import struct
import zlib

import numpy as np
import pytest

from bsa.image_checks import Finding, fireflies, frame_checks, read_png


def _chunk(kind: bytes, body: bytes) -> bytes:
    return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body))


def _paeth(a, b, c):
    p = a + b - c
    pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
    return np.where((pa <= pb) & (pa <= pc), a, np.where(pb <= pc, b, c))


def _filter_row(kind, row, prev, bpp):
    r, p = row.astype(np.int32), prev.astype(np.int32)
    left = np.concatenate([np.zeros(bpp, np.int32), r[:-bpp]])
    upleft = np.concatenate([np.zeros(bpp, np.int32), p[:-bpp]])
    pred = [np.zeros_like(r), left, p, (left + p) // 2, _paeth(left, p, upleft)][kind]
    return ((r - pred) % 256).astype(np.uint8)


def write_png(path, pixels, filters=(0,), color_type=None, bit_depth=8, interlace=0):
    """Minimal PNG encoder for tests; row y uses filters[y % len(filters)]."""
    h, w, ch = pixels.shape
    color_type = color_type if color_type is not None else (2 if ch == 3 else 6)
    flat = pixels.reshape(h, w * ch)
    raw = bytearray()
    prev = np.zeros(w * ch, np.uint8)
    for y in range(h):
        kind = filters[y % len(filters)]
        raw.append(kind)
        raw += _filter_row(kind, flat[y], prev, ch).tobytes()
        prev = flat[y]
    ihdr = struct.pack(">IIBBBBB", w, h, bit_depth, color_type, 0, 0, interlace)
    path.write_bytes(
        b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", ihdr) + _chunk(b"IDAT", zlib.compress(bytes(raw))) + _chunk(b"IEND", b"")
    )
    return path


def _grey(value=60, size=64, channels=3):
    return np.full((size, size, channels), value, np.uint8)


def _with_white(pixels, n, seed=1):
    """n isolated white pixels on a grid spaced so no two share a 3x3 neighbourhood."""
    out = pixels.copy()
    spots = [(y, x) for y in range(2, out.shape[0] - 2, 4) for x in range(2, out.shape[1] - 2, 4)]
    rng = np.random.default_rng(seed)
    for k in rng.choice(len(spots), n, replace=False):
        out[spots[k][0], spots[k][1], :3] = 255
    return out


def test_read_png_all_filters(tmp_path):
    rng = np.random.default_rng(0)
    for ch in (3, 4):
        pixels = rng.integers(0, 256, (20, 13, ch), dtype=np.uint8)
        png = write_png(tmp_path / f"f{ch}.png", pixels, filters=(0, 1, 2, 3, 4))
        assert np.array_equal(read_png(png), pixels)


def test_unsupported_png_raises(tmp_path):
    with pytest.raises(ValueError, match="unsupported"):
        read_png(write_png(tmp_path / "i.png", _grey(), interlace=1))
    with pytest.raises(ValueError, match="unsupported"):
        read_png(write_png(tmp_path / "g.png", _grey(channels=1), color_type=0))
    (tmp_path / "x.png").write_bytes(b"not a png")
    with pytest.raises(ValueError, match="not a PNG"):
        read_png(tmp_path / "x.png")


def test_fireflies_counted(tmp_path):
    png = write_png(tmp_path / "ff.png", _with_white(_grey(), 30), filters=(4, 1, 3, 2, 0))
    assert fireflies(png) == 30
    assert fireflies(write_png(tmp_path / "rgba.png", _with_white(_grey(channels=4), 30))) == 30
    assert fireflies(write_png(tmp_path / "clean.png", _grey())) == 0


def test_black_frame_error(tmp_path):
    black = write_png(tmp_path / "0001.png", _grey(0))
    found = frame_checks([black, write_png(tmp_path / "0002.png", _grey())])
    assert [(f.check, f.severity, f.frame) for f in found] == [("black_frame", "error", 1)]
    assert isinstance(found[0], Finding)


def test_firefly_thresholds(tmp_path):
    found = frame_checks(
        [
            write_png(tmp_path / "0001.png", _with_white(_grey(), 20)),
            write_png(tmp_path / "0002.png", _with_white(_grey(), 26)),
            write_png(tmp_path / "0003.png", _with_white(_grey(size=128), 201)),
        ]
    )
    assert [(f.check, f.severity, f.frame) for f in found] == [("fireflies", "error", 3), ("fireflies", "warning", 2)]


def test_stuck_frame_inside_motion_warns(tmp_path):
    values = [40, 50, 50, 70, 80, 80, 80, 80]  # frame 3 repeats 2 mid-motion; 5-8 is a deliberate hold
    frames = [write_png(tmp_path / f"{i + 1:04d}.png", _grey(v)) for i, v in enumerate(values)]
    found = frame_checks(frames)
    assert [(f.check, f.severity, f.frame) for f in found] == [("stuck_frame", "warning", 3)]


def test_stuck_frames_only_where_something_keyed_moves_and_not_in_holds(tmp_path):
    values = [40, 50, 50, 70, 80, 80, 90, 95, 95, 99]  # frames 3, 6 and 9 repeat the frame before them
    frames = [write_png(tmp_path / f"{i + 1:04d}.png", _grey(v)) for i, v in enumerate(values)]
    assert [f.frame for f in frame_checks(frames) if f.check == "stuck_frame"] == [3, 6, 9]
    # the heroes' transforms change between 2 and 3 and between 8 and 9, not between 5 and 6 (a scripted freeze)
    moving = {2, 3, 4, 7, 8, 9, 10}
    assert [f.frame for f in frame_checks(frames, moving=moving) if f.check == "stuck_frame"] == [3, 9]
    assert [f.frame for f in frame_checks(frames, moving=moving, held={9, 10}) if f.check == "stuck_frame"] == [3]
    assert [f.frame for f in frame_checks(frames, held=set(range(1, 11))) if f.check == "stuck_frame"] == []


def test_held_frames_from_a_shots_hold():
    from bsa.short import Shot
    from bsa.timeline import Span, held_frames

    span = Span("s01", 101, 190)  # 3 s at 30 fps
    assert held_frames(Shot("s01", "t", "p", "S"), span, 30) == set()
    assert held_frames(Shot("s01", "t", "p", "S", hold=True), span, 30) == set(range(101, 191))
    assert held_frames(Shot("s01", "t", "p", "S", hold=[[1.0, 1.5], [2.9, 9]]), span, 30) == (
        set(range(131, 147)) | set(range(188, 191))
    )


def test_frame_checks_take_pixels_from_any_loader(tmp_path):
    """frame-check decodes with Blender's image loader; the checks' maths is the same whichever decoder ran."""
    frames = [tmp_path / f"{i:04d}.png" for i in range(1, 4)]  # never read from disk: the loader supplies pixels
    black = np.zeros((8, 8, 4), np.uint8)
    lit = np.full((8, 8, 4), 128, np.uint8)
    loaded = {frames[0]: lit, frames[1]: black, frames[2]: lit}
    found = frame_checks(frames, load=loaded.__getitem__)
    assert [(f.check, f.frame) for f in found] == [("black_frame", 2)]


def test_frame_checks_report_progress_without_changing_findings(tmp_path):
    values = [40, 50, 50, 70, 80]
    frames = [write_png(tmp_path / f"{i + 1:04d}.png", _grey(v)) for i, v in enumerate(values)]
    seen = []
    found = frame_checks(frames, progress=lambda done, total: seen.append((done, total)))
    assert seen == [(i, 5) for i in range(1, 6)]
    assert found == frame_checks(frames)


def test_luminance_is_the_same_for_bytes_and_floats():
    from bsa.image_checks import luminance

    px = np.random.default_rng(1).integers(0, 256, (6, 5, 4), dtype=np.uint8)
    assert np.allclose(luminance(px), luminance(px.astype(np.float32) / 255.0), atol=1e-6)


@pytest.mark.blender
def test_blender_png_loader_in_blender(blender_run):
    from pathlib import Path

    result = blender_run(Path(__file__).resolve().parent / "blender" / "test_pixels.py", timeout=600)
    assert result.returncode == 0, result.stdout + result.stderr
    for name in ("test_blender_loader_matches_png_decoder", "test_blender_loader_is_faster",
                 "test_loader_is_thread_safe_and_threads_find_the_same"):
        assert f"PASS {name}" in result.stdout


# --------------------------------------------------------------------------- temporal flicker


def _scene_frames(n=12, h=192, w=108, seed=3):
    """A textured background (smooth blobs) the size of a downscaled preview, float 0..1 RGB."""
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:h, 0:w]
    base = np.full((h, w), 0.35, np.float32)
    for _ in range(12):
        cy, cx, r, a = rng.uniform(0, h), rng.uniform(0, w), rng.uniform(6, 20), rng.uniform(-0.25, 0.25)
        base += a * np.exp(-((yy - cy) ** 2 + (xx - cx) ** 2) / (2 * r * r))
    return np.clip(base, 0, 1)


def _rgb(lum):
    return np.repeat(np.clip(lum, 0, 1)[..., None], 3, axis=2).astype(np.float32)


def _run(frames_px, start=1):
    paths = [f"{start + i:04d}.png" for i in range(len(frames_px))]
    loaded = dict(zip(paths, frames_px, strict=True))
    from pathlib import Path

    return frame_checks([Path(p) for p in paths], load=lambda p: loaded[p.name])


def _median_fireflies(lum, threshold=4.0):
    """The firefly rule as first written (a full 3x3 median): the fast count must match it exactly."""
    from bsa.image_checks import MEDIAN_FLOOR

    padded = np.pad(lum, 1, mode="edge")
    h, w = lum.shape
    stack = np.stack([padded[dy : dy + h, dx : dx + w] for dy in range(3) for dx in range(3)])
    return int(np.count_nonzero(lum > threshold * np.maximum(np.median(stack, axis=0), MEDIAN_FLOOR)))


def test_fast_firefly_count_matches_the_median_rule_exactly():
    from bsa.image_checks import _fireflies

    rng = np.random.default_rng(7)
    for trial in range(60):
        h, w = (int(n) for n in rng.integers(1, 40, 2))
        lum = (rng.random((h, w)) ** rng.uniform(0.3, 8)).astype(np.float32)
        lum[rng.random((h, w)) < 0.05] = 1.0
        lum[rng.random((h, w)) < 0.1] = 0.0
        if trial % 3 == 0:
            lum = (np.round(lum * 20) / 20).astype(np.float32)  # ties with the neighbourhood
        for t in (4.0, 2.0):
            assert _fireflies(lum, t) == _median_fireflies(lum, t), (trial, t)


def test_firefly_count_on_a_final_size_frame_is_quick():
    import time

    from bsa.image_checks import _fireflies

    rng = np.random.default_rng(3)
    lum = (rng.random((1920, 1080)) * 0.3 + 0.6).astype(np.float32)
    _fireflies(lum, 4.0)
    t0 = time.perf_counter()
    for _ in range(3):
        _fireflies(lum, 4.0)
    per = (time.perf_counter() - t0) / 3
    assert per < 0.08, f"{per * 1000:.0f} ms a frame (the 3x3 median took about 200 ms)"


def test_frame_checks_with_workers_find_the_same_in_the_same_order(tmp_path):
    rng = np.random.default_rng(5)
    base = _scene_frames()
    frames = [_rgb(base + rng.normal(0, 0.01, base.shape)) for _ in range(12)]
    frames[4] = frames[3].copy()  # stuck
    frames[7][100:130, 20:50] = 1.0  # flicker
    frames[9] = np.zeros_like(frames[9])  # black
    lit = frames[10].copy()
    lit[2:-2:4, 2:-2:4] = 0.02  # a dark grid ...
    lit[4:-2:8, 4:-2:8] = 1.0  # ... with bright specks on it: fireflies
    frames[10] = lit
    paths = [tmp_path / f"{i + 1:04d}.png" for i in range(len(frames))]
    loaded = dict(zip(paths, frames, strict=True))
    one, many = [], []
    serial = frame_checks(paths, load=loaded.__getitem__, progress=lambda d, n: one.append(d))
    threaded = frame_checks(paths, load=loaded.__getitem__, progress=lambda d, n: many.append(d), workers=4)
    assert {f.check for f in serial} >= {"stuck_frame", "black_frame"}
    assert threaded == serial
    assert one == many == list(range(1, 13))


def test_frame_checks_with_workers_pass_a_loader_error_on(tmp_path):
    paths = [tmp_path / f"{i:04d}.png" for i in range(1, 6)]

    def load(p):
        if p.stem == "0003":
            raise ValueError("bad png")
        return np.full((8, 8, 4), 128, np.uint8)

    with pytest.raises(ValueError, match="bad png"):
        frame_checks(paths, load=load, workers=3)


def test_a_patch_that_toggles_for_one_frame_is_flicker():
    from bsa.image_checks import FlickerFinding

    base = _scene_frames()
    frames = [base.copy() for _ in range(12)]
    frames[6][40:60, 70:95] += 0.3  # one frame only, upper right: z-fighting / a denoiser blotch
    found = [f for f in _run([_rgb(f) for f in frames], start=100) if f.check == "flicker"]
    assert len(found) == 1
    f = found[0]
    assert isinstance(f, FlickerFinding) and f.severity == "warning" and f.frame == 106
    x0, y0, x1, y1 = f.box
    assert 0.55 < x0 < 0.7 and 0.85 < x1 <= 0.95 and 0.15 < y0 < 0.25 and 0.27 < y1 < 0.35  # fractions, top-left 0,0
    assert "frame 106" in f.message and "upper right" in f.message


def test_a_pan_is_not_flicker():
    big = _scene_frames(h=192, w=300)
    frames = [_rgb(big[:, 4 * i : 4 * i + 108]) for i in range(40)]  # camera pans 4 px a frame
    assert [f for f in _run(frames) if f.check == "flicker"] == []


def test_a_fast_object_crossing_the_frame_is_not_flicker():
    base = _scene_frames()
    frames = []
    for i in range(10):
        f = base.copy()
        x = 5 + 9 * i  # a small bright ball moving 9 px a frame
        f[90:102, x : x + 12] = 0.95
        frames.append(_rgb(f))
    assert [f for f in _run(frames) if f.check == "flicker"] == []


def test_thin_lines_moving_faster_than_their_width_are_not_flicker():
    # Bevel shadows on floor slabs in a push-in: 2 px dark lines sliding 7 px a frame cover each pixel for one frame.
    base = _scene_frames()
    frames = []
    for i in range(16):
        f = base.copy()
        for y0 in range(20, 180, 24):
            y = y0 + (7 * i) % 24
            f[y : y + 2, :] -= 0.3
        frames.append(_rgb(f))
    assert [f for f in _run(frames) if f.check == "flicker"] == []


def test_render_noise_is_not_flicker():
    rng = np.random.default_rng(7)
    base = _scene_frames()
    frames = [_rgb(base + rng.normal(0, 0.06, base.shape)) for _ in range(20)]
    assert [f for f in _run(frames) if f.check == "flicker"] == []


def test_a_tiny_speck_is_below_the_cluster_size():
    base = _scene_frames()
    frames = [base.copy() for _ in range(6)]
    frames[3][50:52, 50:52] += 0.5  # 2x2 px: a firefly's business, not flicker
    assert [f for f in _run([_rgb(f) for f in frames]) if f.check == "flicker"] == []


def test_flicker_is_not_compared_across_a_gap_in_frame_numbers():
    base = _scene_frames()
    other = base[::-1].copy()
    frames = [_rgb(base), _rgb(other), _rgb(base)]
    paths = ["0001.png", "0005.png", "0006.png"]  # not consecutive: no t-1, t, t+1
    from pathlib import Path

    loaded = dict(zip(paths, frames, strict=True))
    assert [f for f in frame_checks([Path(p) for p in paths], load=lambda p: loaded[p.name]) if f.check == "flicker"] == []


def test_flicker_on_300_preview_frames_is_quick():
    import time

    rng = np.random.default_rng(2)
    base = _scene_frames(h=960, w=540)
    frames = [_rgb(base + rng.normal(0, 0.01, base.shape)) for _ in range(8)]
    from bsa.image_checks import Flicker

    fl = Flicker()
    t0 = time.perf_counter()
    for i in range(300):
        fl.add(i + 1, frames[i % 8])
    per = (time.perf_counter() - t0) / 300
    assert per < 0.03, f"{per * 1000:.1f} ms a frame"


def test_flicker_on_a_flat_surface_is_found():
    frames = [np.full((192, 108), 0.4, np.float32) for _ in range(5)]
    frames[2][100:130, 20:50] = 0.7  # coplanar faces: the other one wins for a frame
    found = [f for f in _run([_rgb(f) for f in frames]) if f.check == "flicker"]
    assert [f.frame for f in found] == [3]


def _spokes(angle, h=192, w=108, n=8, width=1.2):
    """Dark spokes from the frame centre, turned by `angle`: thin lines that sweep, as a turning wheel's do."""
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    dy, dx = yy - h / 2, xx - w / 2
    r = np.hypot(dx, dy)
    out = np.zeros((h, w), bool)
    for k in range(n):
        a = angle + k * 2 * np.pi / n
        dist = np.abs(dx * np.sin(a) - dy * np.cos(a))  # distance from the spoke's line
        out |= (dist < width) & (r < 45) & (dx * np.cos(a) + dy * np.sin(a) > 0)
    return out


def test_fast_turning_thin_spokes_are_not_flicker():
    base = _scene_frames()
    frames = []
    for i in range(16):
        f = base.copy()
        f[_spokes(0.3 * i)] -= 0.35
        frames.append(_rgb(f))
    assert [f for f in _run(frames) if f.check == "flicker"] == []


def test_many_small_flicker_spots_while_things_move_are_one_finding():
    from pathlib import Path

    from bsa.image_checks import FlickerFinding

    base = _scene_frames()
    frames = [base.copy() for _ in range(20)]
    for i in range(2, 18, 2):  # a small spot, somewhere else each time, for one frame
        y, x = 20 + 9 * i, 10 + 4 * i
        frames[i][y : y + 8, x : x + 8] += 0.4
    px = [_rgb(f) for f in frames]
    paths = [Path(f"{i + 1:04d}.png") for i in range(20)]
    loaded = dict(zip(paths, px, strict=True))
    moving = set(range(1, 21))
    found = [f for f in frame_checks(paths, load=loaded.__getitem__, moving=moving) if f.check == "flicker"]
    assert len(found) == 1, found
    f = found[0]
    assert isinstance(f, FlickerFinding) and f.frame is None
    assert "8 small flicker spots" in f.message and "3, 5, 7" in f.message, f.message
    # without motion there, each spot is its own finding (z-fighting on a still view)
    still = [f for f in frame_checks(paths, load=loaded.__getitem__, moving=set()) if f.check == "flicker"]
    assert len(still) == 8 and all(f.frame is not None for f in still)


def test_a_big_flicker_while_things_move_is_still_its_own_finding():
    from pathlib import Path

    base = _scene_frames()
    frames = [base.copy() for _ in range(20)]
    for i in range(2, 18, 2):
        y, x = 20 + 9 * i, 10 + 4 * i
        frames[i][y : y + 8, x : x + 8] += 0.4
    frames[9][100:150, 20:80] += 0.3  # a big blotch: 14% of the frame
    paths = [Path(f"{i + 1:04d}.png") for i in range(20)]
    loaded = dict(zip(paths, [_rgb(f) for f in frames], strict=True))
    found = [f for f in frame_checks(paths, load=loaded.__getitem__, moving=set(range(1, 21))) if f.check == "flicker"]
    assert [f.frame for f in found if f.frame is not None] == [10], found
    assert len(found) == 2
