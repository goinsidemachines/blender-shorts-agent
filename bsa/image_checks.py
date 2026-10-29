"""Checks on rendered frames (PNG): fireflies, black frames, stuck frames and flicker. Pure: stdlib zlib + numpy.

The checks work on a pixel array from any decoder: `read_png` here (pure Python, slow on full-size frames) or, inside
Blender, `bsa.blender.pixels.read_png` (Blender's own image loader), which `frame-check` uses."""

from __future__ import annotations

import struct
import zlib
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import numpy as np

FIREFLY_WARN = 25  # more fireflies than this in one frame -> warning
FIREFLY_ERROR = 200  # more than this -> error
BLACK_MEAN = 0.01  # mean luminance (0..1) under this -> black frame
MEDIAN_FLOOR = 0.02  # neighbourhood medians are floored here so a dim pixel on pure black is not a firefly

_PNG_SIG = b"\x89PNG\r\n\x1a\n"
_LUMA = np.array([0.2126, 0.7152, 0.0722])


@dataclass
class Finding:
    check: str
    severity: Literal["error", "warning"]
    obj: str | None
    frame: int | None
    message: str


@dataclass
class FlickerFinding(Finding):
    box: tuple[float, float, float, float] = (0.0, 0.0, 1.0, 1.0)  # x0, y0, x1, y1 as fractions, top-left is 0, 0
    share: float = 0.0  # the share of the frame that flickers


def read_png(png: Path) -> np.ndarray:
    """Decode an 8-bit, non-interlaced RGB or RGBA PNG to a (height, width, channels) uint8 array."""
    data = Path(png).read_bytes()
    if not data.startswith(_PNG_SIG):
        raise ValueError(f"{png}: not a PNG file")
    pos, header, idat = 8, None, bytearray()
    while pos + 8 <= len(data):
        length, kind = struct.unpack(">I4s", data[pos : pos + 8])
        body = data[pos + 8 : pos + 8 + length]
        pos += 12 + length
        if kind == b"IHDR":
            header = struct.unpack(">IIBBBBB", body)
        elif kind == b"IDAT":
            idat += body
        elif kind == b"IEND":
            break
    if header is None:
        raise ValueError(f"{png}: PNG has no IHDR chunk")
    width, height, depth, color, _, _, interlace = header
    if depth != 8 or color not in (2, 6) or interlace != 0:
        raise ValueError(
            f"{png}: unsupported PNG (bit depth {depth}, colour type {color}, interlace {interlace}); "
            "only 8-bit non-interlaced RGB or RGBA is supported"
        )
    bpp = 3 if color == 2 else 4
    stride = width * bpp
    raw = zlib.decompress(bytes(idat))
    if len(raw) != height * (stride + 1):
        raise ValueError(f"{png}: PNG image data has the wrong length")
    rows = np.frombuffer(raw, dtype=np.uint8).reshape(height, stride + 1)
    out = np.empty((height, stride), dtype=np.uint8)
    prev = np.zeros(stride, dtype=np.uint8)
    for y in range(height):
        kind, line = rows[y, 0], rows[y, 1:]
        if kind == 0:
            cur = line
        elif kind == 1:  # Sub: running sum per channel, mod 256
            cur = np.cumsum(line.reshape(width, bpp), axis=0, dtype=np.uint8).reshape(stride)
        elif kind == 2:  # Up
            cur = line + prev
        elif kind == 3:
            cur = _unfilter_avg(line, prev, bpp)
        elif kind == 4:
            cur = _unfilter_paeth(line, prev, bpp)
        else:
            raise ValueError(f"{png}: unknown PNG filter type {kind} on row {y}")
        out[y] = cur
        prev = out[y]
    return out.reshape(height, width, bpp)


def _unfilter_avg(line: np.ndarray, prev: np.ndarray, bpp: int) -> np.ndarray:
    cur = bytearray(line.tobytes())
    up = prev.tobytes()
    for i in range(bpp):
        cur[i] = (cur[i] + (up[i] >> 1)) & 0xFF
    for i in range(bpp, len(cur)):
        cur[i] = (cur[i] + ((cur[i - bpp] + up[i]) >> 1)) & 0xFF
    return np.frombuffer(bytes(cur), dtype=np.uint8)


def _unfilter_paeth(line: np.ndarray, prev: np.ndarray, bpp: int) -> np.ndarray:
    cur = bytearray(line.tobytes())
    up = prev.tobytes()
    for i in range(bpp):  # no left neighbour: the predictor is the byte above
        cur[i] = (cur[i] + up[i]) & 0xFF
    for i in range(bpp, len(cur)):
        a, b, c = cur[i - bpp], up[i], up[i - bpp]
        pa, pb, pc = abs(b - c), abs(a - c), abs(a + b - 2 * c)
        if pa <= pb and pa <= pc:
            pred = a
        elif pb <= pc:
            pred = b
        else:
            pred = c
        cur[i] = (cur[i] + pred) & 0xFF
    return np.frombuffer(bytes(cur), dtype=np.uint8)


def luminance(pixels: np.ndarray) -> np.ndarray:
    """Rec. 709 luminance of the RGB channels, 0..1 (alpha ignored), from uint8 (0..255) or float (0..1) pixels."""
    rgb = pixels[..., :3].astype(np.float32)
    if pixels.dtype == np.uint8:
        rgb /= 255.0
    return rgb @ _LUMA.astype(np.float32)


def _fireflies(lum: np.ndarray, threshold: float) -> int:
    """Pixels with lum > threshold * max(median of their 3x3 neighbourhood (edges repeated), MEDIAN_FLOOR).

    Counted without the median, exactly: the median of nine values is under x when at least five of them are, so
    a pixel is a firefly when at least five of its nine neighbourhood values are under lum / threshold (and it is
    over the floor). Nine comparisons cost about a tenth of a full-size median."""
    lum = lum.astype(np.float32, copy=False)
    h, w = lum.shape
    padded = np.pad(lum, 1, mode="edge")
    q = lum / np.float32(threshold)
    below = np.zeros((h, w), np.uint8)
    tmp = np.empty((h, w), bool)
    for dy in range(3):
        for dx in range(3):
            np.less(padded[dy : dy + h, dx : dx + w], q, out=tmp)
            below += tmp
    over = lum > np.float32(threshold) * np.float32(MEDIAN_FLOOR)
    return int(np.count_nonzero((below >= 5) & over))


def fireflies(png: Path, threshold: float = 4.0) -> int:
    """Pixels whose luminance is over `threshold` times the median of their 3x3 neighbourhood."""
    return _fireflies(luminance(read_png(png)), threshold)


def _frame_number(png: Path, index: int) -> int:
    stem = Path(png).stem
    return int(stem) if stem.isdigit() else index + 1


def pixel_findings(pixels: np.ndarray, frame: int | None, lum: np.ndarray | None = None) -> list[Finding]:
    """The checks one frame can fail on its own: black, and too many fireflies (the live render status runs these).
    `lum` is the frame's luminance when the caller has it already."""
    findings: list[Finding] = []
    lum = luminance(pixels) if lum is None else lum
    mean = float(lum.mean())
    if mean < BLACK_MEAN:
        findings.append(Finding("black_frame", "error", None, frame, f"frame is black (mean luminance {mean:.4f})"))
    count = _fireflies(lum, 4.0)
    if count > FIREFLY_ERROR:
        findings.append(Finding("fireflies", "error", None, frame, f"{count} firefly pixels"))
    elif count > FIREFLY_WARN:
        findings.append(Finding("fireflies", "warning", None, frame, f"{count} firefly pixels"))
    return findings


def frame_checks(frames: list[Path], load: Callable[[Path], np.ndarray] = read_png,
                 progress: Callable[[int, int], object] | None = None, moving: set[int] | None = None,
                 held: set[int] | None = None, workers: int = 1) -> list[Finding]:  # fmt: skip
    """Fireflies, black frames, stuck frames (a frame identical to the previous one while the frames either side of
    the pair change, i.e. a duplicated frame inside motion) and flicker (`Flicker`), errors first. Give it one shot's
    frames at a time: flicker is never looked for across a cut. `load` decodes one frame to a (height, width,
    channels) array (uint8, or float 0..1). `progress(frames done, frames)` is called after each frame when given.

    `moving`: the frames at which something keyed (a hero, the camera) is placed differently than at the frame
    before; when given, a stuck frame is reported only there (elsewhere nothing was meant to change), and more than
    FLICKER_SPOTS_LISTED small flicker spots at those frames are one finding with their count (`collapse_spots`).
    `held`: frames of a deliberate hold (the shot's `hold`), where no stuck frame is reported.

    `workers` > 1 decodes frames and runs each frame's own checks (black, fireflies) in that many threads, a few
    frames ahead, while this thread compares frames in order (stuck, flicker): the findings and the progress calls
    are the same as with one. `load` must then be safe to call from several threads (numpy and Blender's imbuf
    release the GIL while they work; bpy.data is not safe)."""
    findings: list[Finding] = []
    history: list[tuple[int, np.ndarray]] = []  # (frame number, pixels) of the last two frames
    flicker = Flicker()

    def prepare(i: int, png: Path):
        pixels = load(png)
        frame = _frame_number(png, i)
        lum = luminance(pixels)
        return pixels, frame, lum, pixel_findings(pixels, frame, lum)

    for i, (pixels, frame, lum, own) in enumerate(_prepared(frames, prepare, workers)):
        findings += own
        findings += flicker.add(frame, lum, black=any(f.check == "black_frame" for f in own))
        history.append((frame, pixels))
        history = history[-4:]
        findings += [f for f in _stuck(history) if (moving is None or f.frame in moving) and f.frame not in (held or ())]
        if progress is not None:
            progress(i + 1, len(frames))
    return sorted(collapse_spots(findings, moving), key=lambda f: f.severity != "error")


def _prepared(frames: list[Path], prepare: Callable, workers: int):
    """prepare(i, frame) for each frame, in order; with workers > 1 run in a thread pool at most 2 x workers frames
    ahead (a final-size frame and its luminance are about 16 MB, so the look-ahead is bounded)."""
    if workers <= 1 or len(frames) < 2:
        for i, png in enumerate(frames):
            yield prepare(i, png)
        return
    from collections import deque
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="frame-check") as pool:
        ahead: deque = deque()
        todo = iter(enumerate(frames))
        try:
            for i, png in todo:
                ahead.append(pool.submit(prepare, i, png))
                if len(ahead) >= 2 * workers:
                    break
            while ahead:
                result = ahead.popleft().result()
                nxt = next(todo, None)
                if nxt is not None:
                    ahead.append(pool.submit(prepare, *nxt))
                yield result
        finally:
            for fut in ahead:
                fut.cancel()


def default_workers() -> int:
    """Threads for frame-check: the cores but one (the work is numpy and image decoding), between 1 and 8."""
    import os

    return max(1, min(8, (os.cpu_count() or 2) - 1))


def _stuck(history: list[tuple[int, np.ndarray]]) -> list[Finding]:
    """With the last four consecutive frames a, b, c, d: b == c while a != b and c != d -> c is stuck."""
    if len(history) < 4:
        return []
    numbers = [n for n, _ in history]
    if numbers != list(range(numbers[0], numbers[0] + 4)):
        return []
    a, b, c, d = (p for _, p in history)
    if np.array_equal(b, c) and not np.array_equal(a, b) and not np.array_equal(c, d):
        return [Finding("stuck_frame", "warning", None, numbers[2], "frame identical to the previous one mid-motion")]
    return []


# --------------------------------------------------------------------------- temporal flicker

FLICKER_JUMP = 0.08  # |L(t) - L(t-1)| over this (block-mean luminance, 0..1) ...
FLICKER_BACK = 0.03  # ... while |L(t+1) - L(t-1)| is under this: the area changed for one frame and came back
FLICKER_CELLS = 135  # frames are averaged down to about this many cells across, so render noise averages out
FLICKER_MIN_FACTOR = 2  # and at least 2 x 2 pixels a cell
FLICKER_MIN_CELLS = 6  # a cluster needs at least this many cells ...
FLICKER_MIN_FRACTION = 0.0005  # ... and this share of the frame (specks are the firefly check's business)
FLICKER_MIN_SIDE = 3  # ... and this many cells each way: thin moving edges (rods, strings) alias, they don't flicker
# ... and a solid 3 x 3 block of cells somewhere in it: a thin line (a string, a turning spoke), however long or
# crooked, is aliasing, not a surface that flickers
FLICKER_SMALL = 0.005  # a flicker under this share of the frame, on a frame where something moves, is a small spot
FLICKER_SPOTS_LISTED = 3  # a shot with more small spots than this gets one finding for all of them, with a count
FLICKER_REACH = 0.12  # an area found again shifted up to this share of the frame width is a moving object
FLICKER_MATCH = 0.35  # the shifted area counts as found when it differs this much less than the area in place
FLICKER_MAX_PER_FRAME = 3  # clusters reported per frame (the largest)
FLICKER_FLOOD_LIMIT = 5000  # more flagged cells than this: one cluster (a flash), not walked cell by cell


def _downscale(lum: np.ndarray, factor: int) -> np.ndarray:
    h, w = (lum.shape[0] // factor) * factor, (lum.shape[1] // factor) * factor
    return lum[:h, :w].reshape(h // factor, factor, w // factor, factor).mean(axis=(1, 3), dtype=np.float32)


def _clusters(mask: np.ndarray) -> list[tuple[int, int, int, int, int]]:
    """4-connected clusters of a boolean mask as (cells, y0, x0, y1, x1) (ends exclusive), largest first."""
    ys, xs = np.nonzero(mask)
    if len(ys) > FLICKER_FLOOD_LIMIT:
        return [(len(ys), int(ys.min()), int(xs.min()), int(ys.max()) + 1, int(xs.max()) + 1)]
    left = set(zip(ys.tolist(), xs.tolist(), strict=True))
    out = []
    while left:
        start = left.pop()
        stack, n = [start], 0
        y0 = y1 = start[0]
        x0 = x1 = start[1]
        while stack:
            y, x = stack.pop()
            n += 1
            y0, y1, x0, x1 = min(y0, y), max(y1, y), min(x0, x), max(x1, x)
            for nb in ((y - 1, x), (y + 1, x), (y, x - 1), (y, x + 1)):
                if nb in left:
                    left.remove(nb)
                    stack.append(nb)
        out.append((n, y0, x0, y1 + 1, x1 + 1))
    return sorted(out, reverse=True)


def _moved(cur: np.ndarray, others: tuple[np.ndarray, ...], box: tuple[int, int, int, int], reach: int) -> bool:
    """The area `box` of frame t (with a margin of what is around it) appears shifted in t-1 or t+1: an object that
    moves also covers each spot for one frame only, and that is motion, not flicker."""
    h, w = cur.shape
    m = 2
    y0, x0, y1, x1 = max(0, box[0] - m), max(0, box[1] - m), min(h, box[2] + m), min(w, box[3] + m)
    patch = cur[y0:y1, x0:x1]
    for other in others:
        here = float(np.abs(other[y0:y1, x0:x1] - patch).mean())
        enough = FLICKER_MATCH * here
        for dy in range(max(-reach, -y0), min(reach, h - y1) + 1):
            for dx in range(max(-reach, -x0), min(reach, w - x1) + 1):
                if (dy or dx) and float(np.abs(other[y0 + dy : y1 + dy, x0 + dx : x1 + dx] - patch).mean()) < enough:
                    return True
    return False


def _where(box: tuple[float, float, float, float]) -> str:
    cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
    row = "upper" if cy < 1 / 3 else "lower" if cy > 2 / 3 else "middle"
    col = "left" if cx < 1 / 3 else "right" if cx > 2 / 3 else "centre"
    return "centre" if (row, col) == ("middle", "centre") else f"{row} {col}"


class Flicker:
    """Flicker inside one shot: an area whose luminance jumps at frame t and is back at t+1 (A-B-A), in a cluster
    bigger than noise. Z-fighting and denoiser blotches do this; camera moves and pans do not, and a moving object
    (which also covers each spot for one frame) is told apart by finding the area's look shifted in the frame
    before or after. Feed frames in order with `add` (luminance, or pixels); the finding for frame t comes out when
    t+1 is added. A frame that does not follow the last one starts over: never compare across a cut or a gap."""

    def __init__(self):
        self.recent: list[tuple[int, np.ndarray]] = []  # (frame, block-mean luminance) of the last two frames
        self.black: set[int] = set()  # black frames: reported as black, not again as flicker

    def add(self, frame: int, lum: np.ndarray, black: bool = False) -> list[FlickerFinding]:
        if black:
            self.black.add(frame)
        if lum.ndim == 3:
            lum = luminance(lum)
        factor = max(FLICKER_MIN_FACTOR, round(lum.shape[1] / FLICKER_CELLS))
        small = _downscale(lum.astype(np.float32, copy=False), factor)
        if self.recent and (self.recent[-1][0] != frame - 1 or self.recent[-1][1].shape != small.shape):
            self.recent = []
        self.recent = [*self.recent[-2:], (frame, small)]
        if len(self.recent) < 3:
            return []
        (_, a), (t, b), (_, c) = self.recent
        return [] if t in self.black else self._look(t, a, b, c)

    @staticmethod
    def _look(frame: int, a: np.ndarray, b: np.ndarray, c: np.ndarray) -> list[FlickerFinding]:
        mask = (np.abs(b - a) > FLICKER_JUMP) & (np.abs(c - a) < FLICKER_BACK)
        h, w = mask.shape
        least = max(FLICKER_MIN_CELLS, FLICKER_MIN_FRACTION * h * w)
        if mask.sum() < least:
            return []
        reach = max(4, round(FLICKER_REACH * w))
        out = []
        for n, y0, x0, y1, x1 in _clusters(mask):
            if n < least or len(out) >= FLICKER_MAX_PER_FRAME:
                break
            if min(y1 - y0, x1 - x0) < FLICKER_MIN_SIDE or not _solid(mask[y0:y1, x0:x1]):
                continue
            if _moved(b, (a, c), (y0, x0, y1, x1), reach):
                continue
            box = (round(x0 / w, 3), round(y0 / h, 3), round(x1 / w, 3), round(y1 / h, 3))
            out.append(FlickerFinding(
                "flicker", "warning", None, frame,
                f"flicker at frame {frame}, {_where(box)} of the frame (x {box[0]:.2f}-{box[2]:.2f}, y {box[1]:.2f}-"
                f"{box[3]:.2f} from the top left, {100 * n / (h * w):.1f}% of it): an area changes for this one frame "
                "and comes back (z-fighting, or denoiser flicker)", box, round(n / (h * w), 5)))  # fmt: skip
        return out


def _solid(mask: np.ndarray) -> bool:
    """Has the mask a cell whose eight neighbours are all set (a solid 3 x 3 block)? A thin line has none."""
    if mask.shape[0] < 3 or mask.shape[1] < 3:
        return False
    core = np.ones((mask.shape[0] - 2, mask.shape[1] - 2), bool)
    for dy in range(3):
        for dx in range(3):
            core &= mask[dy : dy + mask.shape[0] - 2, dx : dx + mask.shape[1] - 2]
    return bool(core.any())


def collapse_spots(findings: list[Finding], moving: set[int] | None) -> list[Finding]:
    """One shot's findings with its small flicker spots (each under FLICKER_SMALL of the frame, at frames where
    something moves) made into one finding with their count, when there are more than FLICKER_SPOTS_LISTED: thin or
    fast moving parts (strings, spokes, edges) alias in spots like these, a few pixels wide, all through the motion;
    real z-fighting covers a surface. Without `moving` nothing is collapsed (where things move is not known)."""
    if not moving:
        return findings
    spots = [f for f in findings if isinstance(f, FlickerFinding) and f.frame in moving
             and f.share < FLICKER_SMALL]  # fmt: skip
    if len(spots) <= FLICKER_SPOTS_LISTED:
        return findings
    big = max(spots, key=lambda f: f.share)
    frames = sorted({f.frame for f in spots})
    shown = ", ".join(str(n) for n in frames[:12]) + (f" and {len(frames) - 12} more" if len(frames) > 12 else "")
    one = FlickerFinding(
        "flicker", "warning", None, None,
        f"{len(spots)} small flicker spots in this shot (each under {100 * FLICKER_SMALL:.1f}% of the frame), all at "
        f"frames where something moves: frames {shown}. Usually thin or fast parts (strings, spokes, edges) "
        f"aliasing as they move, not z-fighting; the largest is at frame {big.frame}, {_where(big.box)} of the "
        "frame. Look at it: a still surface that flickers there is z-fighting", big.box,
        round(sum(f.share for f in spots), 5))  # fmt: skip
    first = findings.index(spots[0])
    rest = [f for f in findings if not any(f is s for s in spots)]
    return [*rest[:first], one, *rest[first:]]
