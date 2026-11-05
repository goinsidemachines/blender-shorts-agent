"""Guides on a copy of a frame: the caption zone, the top cards' zone (with a kit) and the framing band outlined, so
framing is judged against the same zones the edit draws in and the checks measure. Pure: stdlib + numpy."""

from __future__ import annotations

import os
import struct
import zlib
from pathlib import Path

import numpy as np

from bsa import layout
from bsa.layout import H, Rect, W

COLOURS = {"caption": (255, 214, 0), "top_cards": (0, 200, 255), "framing": (255, 0, 200)}
LINE_PX = 3  # at the full 1080x1920 size; scaled with the frame, at least 1 px
SUFFIX = "-guides"


def rects(short, shot, kit=None, font=None) -> dict[str, Rect]:
    """{"caption": the caption band, "top_cards": the rows the shot's static top cards take (only with a kit, when
    it has any), "framing": the band frame_objects fits heroes in (under those cards with a kit)}."""
    out = {"caption": layout.caption_band()}
    if kit is None:
        out["framing"] = layout.target_area()
        return out
    from bsa import kitlayout

    band = kitlayout.top_band(short, shot, kit, font)
    if band is not None:
        out["top_cards"] = band
    out["framing"] = layout.target_area(top=band)
    return out


def draw(pixels: np.ndarray, boxes: dict[str, Rect]) -> np.ndarray:
    """A copy of the (H, W, 3|4) uint8 frame with each rect outlined, inside its edges, in its COLOURS colour (alpha
    opaque); coordinates are scaled from the 1080x1920 frame to this one's size."""
    out = np.array(pixels, dtype=np.uint8, copy=True)
    h, w = out.shape[:2]
    sx, sy = w / W, h / H
    t = max(1, round(LINE_PX * min(sx, sy)))
    for name, r in boxes.items():
        colour = list(COLOURS[name]) + [255] * (out.shape[2] - 3)
        x0, x1 = max(0, round(r.x * sx)), min(w, round(r.right * sx))
        y0, y1 = max(0, round(r.y * sy)), min(h, round(r.bottom * sy))
        if x1 <= x0 or y1 <= y0:
            continue
        out[y0 : min(y0 + t, y1), x0:x1] = colour
        out[max(y1 - t, y0) : y1, x0:x1] = colour
        out[y0:y1, x0 : min(x0 + t, x1)] = colour
        out[y0:y1, max(x1 - t, x0) : x1] = colour
    return out


def _chunk(kind: bytes, body: bytes) -> bytes:
    return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body))


def write_png(path: Path, pixels: np.ndarray) -> Path:
    """An 8-bit RGB or RGBA PNG of the (H, W, 3|4) uint8 array (filter 0), written to `<name>.part` and moved in
    place, so a reader never sees half a file."""
    path = Path(path)
    a = np.ascontiguousarray(pixels, dtype=np.uint8)
    if a.ndim != 3 or a.shape[2] not in (3, 4):
        raise ValueError(f"write_png wants an (H, W, 3|4) array, got shape {a.shape}")
    h, w, c = a.shape
    raw = np.concatenate([np.zeros((h, 1), np.uint8), a.reshape(h, w * c)], axis=1).tobytes()
    data = (b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2 if c == 3 else 6, 0, 0, 0))
            + _chunk(b"IDAT", zlib.compress(raw, 6)) + _chunk(b"IEND", b""))  # fmt: skip
    path.parent.mkdir(parents=True, exist_ok=True)
    part = path.with_name(path.name + ".part")
    part.write_bytes(data)
    os.replace(part, path)
    return path


def guides_path(frame_path: Path) -> Path:
    """`<stem>-guides.png` beside the frame."""
    frame_path = Path(frame_path)
    return frame_path.with_name(f"{frame_path.stem}{SUFFIX}.png")
