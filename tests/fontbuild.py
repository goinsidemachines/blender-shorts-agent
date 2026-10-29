"""Builds tiny TrueType/OpenType fonts (only the tables bsa.fonts reads: cmap, name, OS/2, fvar) for tests."""

from __future__ import annotations

import struct


def _cmap4(cps: list[int]) -> bytes:
    """A format 4 subtable mapping each BMP codepoint to its own glyph (one segment per codepoint, plus 0xFFFF)."""
    bmp = sorted(c for c in set(cps) if c < 0xFFFF)
    segs = [(c, c, (i + 1 - c) & 0xFFFF) for i, c in enumerate(bmp)] + [(0xFFFF, 0xFFFF, 1)]
    n = len(segs)
    body = struct.pack(f">{n}H", *(e for _, e, _ in segs)) + b"\0\0"
    body += struct.pack(f">{n}H", *(s for s, _, _ in segs))
    body += struct.pack(f">{n}H", *(d for _, _, d in segs))
    body += struct.pack(f">{n}H", *([0] * n))
    head = struct.pack(">7H", 4, 14 + len(body), 0, 2 * n, 0, 0, 0)
    return head + body


def _cmap12(cps: list[int]) -> bytes:
    groups = [(c, c, i + 1) for i, c in enumerate(sorted(set(cps)))]
    body = b"".join(struct.pack(">3I", *g) for g in groups)
    return struct.pack(">HHIII", 12, 0, 16 + len(body), 0, len(groups)) + body


def _cmap(cps: list[int], fmt: int) -> bytes:
    sub = _cmap4(cps) if fmt == 4 else _cmap12(cps)
    platform, encoding = (3, 1) if fmt == 4 else (3, 10)
    return struct.pack(">HHHHI", 0, 1, platform, encoding, 12) + sub


def _name(names: dict[int, str]) -> bytes:
    recs, data = [], b""
    for nid, text in sorted(names.items()):
        raw = text.encode("utf-16-be")
        recs.append(struct.pack(">6H", 3, 1, 0x409, nid, len(raw), len(data)))
        data += raw
    head = struct.pack(">3H", 0, len(recs), 6 + 12 * len(recs))
    return head + b"".join(recs) + data


def _os2(weight: int) -> bytes:
    return struct.pack(">HhH", 4, 500, weight) + b"\0" * 90


def _fixed(v: float) -> int:
    return round(v * 65536) & 0xFFFFFFFF


def _fvar(axes: list[tuple], instances: list[tuple]) -> bytes:
    """axes: (tag, min, default, max); instances: (subfamily name id, [coords])."""
    n = len(axes)
    out = struct.pack(">8H", 1, 0, 16, 2, n, 20, len(instances), 4 + 4 * n)
    for tag, lo, default, hi in axes:
        out += tag.encode("ascii") + struct.pack(">3IHH", _fixed(lo), _fixed(default), _fixed(hi), 0, 256)
    for name_id, coords in instances:
        out += struct.pack(">HH", name_id, 0) + b"".join(struct.pack(">I", _fixed(c)) for c in coords)
    return out


def build_font(text: str = "", family: str = "Testa", subfamily: str = "Regular", weight: int = 400,
               cmap_format: int = 4, axes=None, instances=None, extra_names=None) -> bytes:
    """A font whose cmap covers exactly the characters of `text` (plus space)."""
    cps = sorted({ord(c) for c in text} | {0x20})
    names = {1: family, 2: subfamily, **(extra_names or {})}
    tables = {"cmap": _cmap(cps, cmap_format), "name": _name(names), "OS/2": _os2(weight)}
    if axes:
        tables["fvar"] = _fvar(axes, instances or [])
    tags = sorted(tables)
    offset = 12 + 16 * len(tags)
    head, body = struct.pack(">IHHHH", 0x00010000, len(tags), 0, 0, 0), b""
    for tag in tags:
        data = tables[tag] + b"\0" * (-len(tables[tag]) % 4)
        head += tag.encode("ascii") + struct.pack(">III", 0, offset + len(body), len(tables[tag]))
        body += data
    return head + body


def build_variable(text: str = "", family: str = "Varia", default_weight: float = 300) -> bytes:
    """A variable font with a wght axis 300-700 whose default instance is `default_weight` (named Light at 300)."""
    return build_font(text, family=family, subfamily="Light", weight=int(default_weight), axes=[
        ("wght", 300, default_weight, 700)], instances=[(256, [300]), (257, [400]), (258, [700])],
        extra_names={256: "Light", 257: "Regular", 258: "Bold"})  # fmt: skip
