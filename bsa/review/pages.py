"""What the one review page (bsa.review.snapshot) reads about checks, findings and the closing card.

The separate HTML review pages that lived here are retired: `CLI page SHORT` shows every stage on one page."""

from __future__ import annotations

import hashlib
import html
import json
from dataclasses import dataclass
from pathlib import Path

CAPTION_LINES = 2  # height of the caption box drawn on the enlarged still


def _esc(text: object) -> str:
    return html.escape(str(text))


LOOK_NOTES = {
    "fresh": "This short has its own look (short.json \"look\": \"fresh\"): the brand is off for this short, so its "
    "colours, captions and background follow only its own style.",
    "brand": "This short follows the brand look from your settings (palette, font, captions, view).",
}


def closing_info(stills_dir: Path) -> dict | None:
    """The closing card BLENDER stills, closing or edit last made (stills/closing.png and closing.json: what made it
    and its warnings), or None when there is none."""
    from bsa import closing

    png, info = Path(stills_dir) / closing.CARD_FILE, Path(stills_dir) / closing.INFO_FILE
    if not png.is_file() or not info.is_file():
        return None
    try:
        data = json.loads(info.read_text(encoding="utf-8"))
    except ValueError:
        return None
    return {**data, "png": png} if isinstance(data, dict) else None


@dataclass(frozen=True)
class ReviewFinding:
    """A finding as the review pages show it, read from review/checks.json or a frame-check JSON. Those rows grow
    keys over time (shot, scene, shots, redo, redo_frames, a flicker's box...): the loader takes the ones shown here
    and ignores the rest, so a newer check never breaks the pages."""

    check: str
    severity: str
    message: str
    obj: str | None = None
    frame: int | None = None
    shots: tuple[str, ...] = ()
    scene: str | None = None
    redo: str | None = None  # the command that renders the flagged frames again (frame checks)

    @classmethod
    def from_row(cls, row) -> ReviewFinding | None:
        """One JSON row; None for a row that is not a finding (not a mapping, or no check name)."""
        if not isinstance(row, dict) or not row.get("check"):
            return None
        shots = row.get("shots")
        if not isinstance(shots, list) or not shots:
            shots = [row["shot"]] if row.get("shot") else []
        frame = row.get("frame")
        return cls(check=str(row["check"]), severity=str(row.get("severity") or "warning"),
                   message=str(row.get("message") or ""), obj=row.get("obj") or None,
                   frame=frame if isinstance(frame, int) else None, shots=tuple(str(s) for s in shots),
                   scene=row.get("scene") or None, redo=row.get("redo") or None)  # fmt: skip


def finding_id(f: ReviewFinding) -> str:
    """A finding's id, stable across rebuilds: the first 10 hex characters of sha1(check|message|frame|shots)."""
    frame = "" if f.frame is None else str(f.frame)
    raw = f"{f.check}|{f.message}|{frame}|{','.join(f.shots)}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:10]


def where_text(f: ReviewFinding) -> str:
    """Where a finding is, in words: "shot s02 · frame 12"."""
    shots = f.shots
    parts = [
        f"shot{'s' if len(shots) > 1 else ''} {', '.join(shots)}" if shots else "",
        f"scene {f.scene}" if f.scene else "",
        str(f.obj) if f.obj else "",
        f"frame {f.frame}" if f.frame is not None else "",
    ]
    return " · ".join(x for x in parts if x)


def resolve_finding(sdir: Path, fid: str) -> dict | None:
    """The finding whose id is `fid`, from review/checks.json then every review/frame-checks-*.json (all runs, side
    runs too): {check, severity, message, where, redo}; None when no check reports it any more."""
    review = Path(sdir) / "review"
    for path in [review / "checks.json", *sorted(review.glob("frame-checks-*.json"), key=lambda p: p.name)]:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(data, dict):
            continue
        for row in data.get("findings") or []:
            f = ReviewFinding.from_row(row)
            if f is not None and finding_id(f) == fid:
                return {"check": f.check, "severity": f.severity, "message": f.message, "where": where_text(f),
                        "redo": f.redo}
    return None


FINDINGS_FILES = ("checks.json", "frame-checks-final.json", "frame-checks-preview.json")


def load_findings(short_dir: Path) -> tuple[list[ReviewFinding], list[str]]:
    """(every finding in review/checks.json and the frame checks, errors first; the frame checks' "redo all"
    commands). The preview's frame checks are left out once the final's exist."""
    review = Path(short_dir) / "review"
    out, redo_all = [], []
    for name in FINDINGS_FILES:
        path = review / name
        if name == "frame-checks-preview.json" and (review / "frame-checks-final.json").exists():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(data, dict):
            continue
        out += [f for f in map(ReviewFinding.from_row, data.get("findings") or []) if f is not None]
        if data.get("redo_all"):
            redo_all.append(str(data["redo_all"]))
    return sorted(out, key=lambda f: f.severity != "error"), redo_all
