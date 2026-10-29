import tempfile
import time
from pathlib import Path

import bpy
import numpy as np

from bsa import image_checks
from bsa.blender import pixels


def _save(path: Path, w: int, h: int, seed: int, alpha: bool) -> None:
    rng = np.random.default_rng(seed)
    img = bpy.data.images.new("tmp_px", w, h, alpha=alpha)
    img.pixels.foreach_set(rng.random(w * h * 4).astype(np.float32))
    img.filepath_raw = str(path)
    img.file_format = "PNG"
    bpy.context.scene.render.image_settings.color_mode = "RGBA" if alpha else "RGB"
    img.save()
    bpy.data.images.remove(img)


def test_blender_loader_matches_png_decoder():
    tmp = Path(tempfile.mkdtemp())
    for i, alpha in enumerate((False, True)):
        png = tmp / f"{i:04d}.png"
        _save(png, 37, 23, i, alpha)
        ours = pixels.read_png(png)
        theirs = image_checks.read_png(png)
        assert ours.shape[:2] == theirs.shape[:2] == (23, 37), (ours.shape, theirs.shape)
        assert np.array_equal(ours[..., :3], theirs[..., :3])
        assert np.allclose(image_checks.luminance(ours), image_checks.luminance(theirs))
    assert not [im for im in bpy.data.images if im.filepath and str(tmp) in bpy.path.abspath(im.filepath)]


def test_blender_loader_is_faster():
    """Final-size frames: report both decoders' time per frame (the measurement goes in the fix report)."""
    tmp = Path(tempfile.mkdtemp())
    frames = []
    for i in range(1, 7):
        png = tmp / f"{i:04d}.png"
        _save(png, 1080, 1920, i, False)
        frames.append(png)
    t0 = time.perf_counter()
    slow = image_checks.frame_checks(frames)
    t1 = time.perf_counter()
    fast = image_checks.frame_checks(frames, load=pixels.read_png)
    t2 = time.perf_counter()
    per_py, per_bpy = (t1 - t0) / len(frames), (t2 - t1) / len(frames)
    print(f"FRAME_CHECK_TIMING pure-python {per_py:.3f} s/frame, blender loader {per_bpy:.3f} s/frame, "
          f"speed-up {per_py / per_bpy:.1f}x")
    assert [(f.check, f.frame) for f in slow] == [(f.check, f.frame) for f in fast]
    assert per_bpy < per_py


def test_loader_is_thread_safe_and_threads_find_the_same():
    """Blender 5+: read_png decodes with imbuf (no bpy.data), so frame-check can run it in a thread pool."""
    tmp = Path(tempfile.mkdtemp())
    frames = []
    for i in range(1, 9):
        png = tmp / f"{i:04d}.png"
        _save(png, 64, 48, i % 3, i % 2 == 0)  # repeats: stuck frames; RGB and RGBA files
        frames.append(png)
    assert pixels.thread_safe() == (bpy.app.version >= (5, 0, 0))
    n_images = len(bpy.data.images)
    one = image_checks.frame_checks(frames, load=pixels.read_png)
    many = image_checks.frame_checks(frames, load=pixels.read_png, workers=4 if pixels.thread_safe() else 1)
    assert one == many
    assert len(bpy.data.images) == n_images
    for png in frames:
        assert np.array_equal(pixels.read_png(png)[..., :3], image_checks.read_png(png)[..., :3])
