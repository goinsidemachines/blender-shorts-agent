"""Decode rendered PNG frames with Blender's own image loader (much faster than the pure-Python decoder)."""

from __future__ import annotations

from pathlib import Path

import bpy
import numpy as np


def _imbuf():
    """Blender's imbuf module when it can hand pixels over (Blender 5.0 and later), else None."""
    try:
        import imbuf
        import imbuf.types
    except ImportError:
        return None
    return imbuf if hasattr(imbuf.types.ImBuf, "with_buffer") else None


def thread_safe() -> bool:
    """read_png may be called from several threads at once: it decodes with imbuf, which never touches bpy.data."""
    return _imbuf() is not None


def read_png(png: Path) -> np.ndarray:
    """A PNG as a (height, width, 4) uint8 array, top row first, like bsa.image_checks.read_png (plus alpha): the
    file's stored bytes, no colour management, so the frame checks' maths is unchanged.

    With imbuf (Blender 5.0+) the bytes come straight from the decoded buffer, without bpy.data, so it is safe in
    threads; otherwise through an image datablock (Image.pixels of an 8-bit image are the bytes / 255), removed
    afterwards."""
    imbuf = _imbuf()
    if imbuf is not None:
        ib = imbuf.load(str(png))
        try:
            with ib.with_buffer() as buf:
                px = np.array(buf)
        finally:
            ib.free()
        if px.dtype == np.uint8 and px.ndim == 3 and px.shape[2] == 4:  # an 8-bit frame: its bytes as stored
            return px[::-1]
    img = bpy.data.images.load(str(png), check_existing=False)
    try:
        w, h = img.size
        channels = img.channels
        buf = np.empty(w * h * channels, np.float32)
        img.pixels.foreach_get(buf)
    finally:
        bpy.data.images.remove(img)
    px = buf.reshape(h, w, channels)[::-1]
    if channels == 3:
        px = np.concatenate([px, np.ones((h, w, 1), np.float32)], axis=2)
    return np.rint(px * 255.0).astype(np.uint8)
