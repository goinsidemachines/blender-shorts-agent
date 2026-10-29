"""Local HTML review pages: the only thing the user looks at for each approval step.

Each page is one self-contained file (CSS and JS inline, no network). Images and video are referenced by a path
relative to the page, so the page works from a file:// URL with the short's folder intact.
"""

from __future__ import annotations

import html
import json
import os
import webbrowser
from dataclasses import dataclass
from pathlib import Path
from string import Template
from urllib.parse import urlparse

from bsa.layout import H, W, caption_rect, safe_rect
from bsa.short import Short, label_text
from bsa.timeline import Timeline

_TEMPLATES = Path(__file__).parent / "templates"
CAPTION_LINES = 2  # height of the caption box drawn on the enlarged still


def _esc(text: object) -> str:
    return html.escape(str(text))


def _template(name: str) -> Template:
    return Template((_TEMPLATES / name).read_text(encoding="utf-8"))


def _css() -> str:
    return (_TEMPLATES / "base.css").read_text(encoding="utf-8")


def _rel(target: Path, out: Path) -> str:
    """Relative URL of target as seen from the page at out."""
    try:
        rel = os.path.relpath(Path(target).resolve(), Path(out).resolve().parent)
    except ValueError:  # different drive on Windows
        return Path(target).resolve().as_uri()
    return rel.replace(os.sep, "/")


def _write(out: Path, text: str) -> Path:
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    return out


def _mb(n_bytes: float) -> str:
    return f"{n_bytes / 1_000_000:.1f} MB"


def _secs(s: float) -> str:
    return f"{s:.1f} s"


def _host(url: str) -> str:
    return urlparse(url).netloc or url


def _link(url: str, css: str = "src") -> str:
    """An <a> for http(s) URLs only; anything else (javascript:, data:, plain words) is shown as inert text."""
    parts = urlparse(url.strip())
    if parts.scheme in ("http", "https") and parts.netloc:
        return (
            f'<a href="{_esc(url.strip())}" class="{css}" target="_blank" rel="noopener noreferrer">{_esc(url)}</a>'
        )
    return f'<span class="{css}">{_esc(url)}</span>'


def _lines_of(short: Short, shot_id: str):
    return [ln for ln in short.lines if ln.shot == shot_id]


# --------------------------------------------------------------------------- hard rules


def rules_panel(rows: list[dict] | None, lead: str) -> str:
    """The user's hard rules (bsa.rules.panel_rows), each with how this short keeps it; "" when there are none."""
    if not rows:
        return ""
    by_id = {r["id"]: r for r in rows}
    items = []
    for r in rows:
        scope = "this short only" if r.get("scope") == "this short" else "every short"
        meta = [scope]
        if r.get("part_of") in by_id:
            meta.append(f"part of: {by_id[r['part_of']]['text']}")
        if r.get("kind"):
            meta.append(f"also checked automatically: {r['kind']}")
        if r.get("kept"):
            kept = f'<p class="kept">Kept: {_esc(r["kept"])}</p>'
        else:
            kept = '<p class="kept missing">Not answered yet: Claude must say how this short keeps it.</p>'
        cleared = "".join(f"<li><strong>{_esc(w)}</strong>: {_esc(why)}</li>" for w, why in (r.get("cleared") or {}).items())
        if cleared:
            cleared = f'<p class="where">Words a check flagged, and why they keep the rule:</p><ul class="cleared">{cleared}</ul>'
        items.append(f'<li class="rule panel"><div class="ruletext">{_esc(r.get("text"))}</div>'
                     f'<p class="where">{_esc(" · ".join(meta))}</p>{kept}{cleared}</li>')  # fmt: skip
    return (f'<h2>Your hard rules</h2><p class="lead">{_esc(lead)}</p>'
            f'<ul class="rules">{"".join(items)}</ul>')  # fmt: skip


# --------------------------------------------------------------------------- story


def _assets_table(short: Short, empty: str, assets: list | None = None) -> str:
    assets = short.assets if assets is None else assets
    if not assets:
        return f'<p class="empty">{_esc(empty)}</p>'
    rows = "".join(
        f"<tr><td>{_esc(a.name)}</td><td>{_esc(_host(a.source))}</td><td>{_esc(a.license)}</td>"
        f"<td>{_esc(_mb(a.bytes))}</td></tr>"
        for a in assets
    )
    return (
        '<div class="tablewrap"><table><thead><tr><th>Name</th><th>From</th><th>Licence</th><th>Size</th></tr>'
        f"</thead><tbody>{rows}</tbody></table></div>"
    )


def _story_assets(short: Short, pending: list | None) -> str:
    """Planned downloads (waiting for the user's yes) first, then assets already downloaded (credits)."""
    if not pending:
        return _assets_table(short, "Nothing to download: this short needs no outside files.")
    total = sum(a.bytes for a in pending)
    html_ = (
        f"<p>These will be downloaded after your yes ({_esc(_mb(total))} in total), and not before:</p>"
        + _assets_table(short, "", pending)
    )
    if short.assets:
        html_ += "<p>Already downloaded:</p>" + _assets_table(short, "")
    return html_


def _pace_bar(row: dict | None) -> str:
    """A shot's spoken vs silent seconds as a bar, flagged when the shot is mostly silent (bsa.timeline.pacing)."""
    if not row:
        return ""
    total = max(float(row["seconds"]), 1e-6)
    spoken = min(float(row["spoken_s"]) / total, 1.0)
    slow = bool(row.get("too_silent"))
    note = (f"{row['spoken_s']:.1f} s spoken, {row['silent_s']:.1f} s silent of {row['seconds']:.1f} s")
    if slow:
        note += f": mostly silent, try about {row['suggest_s']:.1f} s" if row["spoken_s"] else ": no narration"
    return (f'<div class="pace{" slow" if slow else ""}"><div class="pacebar" role="img" aria-label="{_esc(note)}">'
            f'<span class="spoken" style="width:{100 * spoken:.1f}%"></span></div>'
            f'<p class="pacenote">{_esc(note)}</p></div>')  # fmt: skip


LOOK_NOTES = {
    "fresh": "This short has its own look (short.json \"look\": \"fresh\"): the brand is off for this short, so its "
    "colours, captions and background follow only its own style.",
    "brand": "This short follows the brand look from your settings (palette, font, captions, view).",
}


def render_story(
    short: Short,
    out: Path,
    style_options: list[dict] | None = None,
    pending: list | None = None,
    pacing: list[dict] | None = None,
    rules: list[dict] | None = None,
    look: str | None = None,
) -> Path:
    """`pending`: Assets planned but not downloaded yet (shown as waiting for the user's yes). `pacing`: per-shot
    spoken vs silent seconds (bsa.timeline.pacing), drawn as a bar under each beat. `rules`: the hard rules
    (bsa.rules.panel_rows) with how the short keeps each. `look`: the look the short follows
    (bsa.settings.look_mode), said above the look options."""
    out = Path(out)
    hook = short.lines[0].text if short.lines else ""
    closing = short.lines[-1].text if short.lines else ""
    pace = {row["shot"]: row for row in pacing or []}

    beats = []
    for i, shot in enumerate(short.shots, 1):
        badge = f'<span class="badge">{_esc(shot.badge)}</span>' if shot.badge else ""
        narration = "".join(f"<li>{_esc(ln.text)}</li>" for ln in _lines_of(short, shot.id))
        beats.append(
            f'<li class="beat panel"><div class="num">{i}</div><div>'
            f"<h3>{_esc(shot.title)}{badge}</h3>"
            f'<p class="picture">{_esc(shot.picture)}</p>'
            f'<ul class="narration" style="padding-left:12px">{narration}</ul>{_pace_bar(pace.get(shot.id))}</div></li>'
        )

    facts = []
    for f in short.facts:
        facts.append(f'<li class="panel"><div>{_esc(f.claim)}</div>{_link(f.source)}</li>')
    if not facts:
        facts.append('<li class="empty">No facts are quoted in this short.</li>')

    options = ""
    if style_options:
        items = "".join(
            f'<li class="option panel"><img src="{_esc(_rel(out.parent / "../" / o["thumb"], out))}" '
            f'alt="Look: {_esc(o["name"])}"><h3 style="margin-top:8px">{_esc(o["name"])}</h3>'
            f"<p>{_esc(o.get('description', ''))}</p></li>"
            for o in style_options
        )
        options = f"<h2>Look options</h2><ul class=\"options\">{items}</ul>"
    note = LOOK_NOTES.get(look or "")
    if note:
        options = f'<h2>Look</h2><p class="panel">{_esc(note)}</p>' + options

    text = _template("story.html").substitute(
        page_title=_esc(f"Story — {short.title}"),
        css=_css(),
        title=_esc(short.title),
        lead=_esc(f"{len(short.shots)} shots, about {_secs(short.length_s)}. Read it through and say what to change."),
        hook=_esc(hook),
        beats="\n".join(beats),
        closing=_esc(closing),
        facts="\n".join(facts),
        options=options,
        assets=_story_assets(short, pending),
        rules=rules_panel(rules, "Every short follows these. Check that this story keeps each one."),
    )
    return _write(out, text)


# --------------------------------------------------------------------------- storyboard


def _pct(v: float, whole: float) -> str:
    return f"{100 * v / whole:.3f}"


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


def _closing_html(card: dict | None, out: Path, tag: str = "div") -> str:
    """The closing card on a review page: its picture, how long it holds, the line said over it and its warnings."""
    if not card:
        return ""
    src = _esc(_rel(Path(card["png"]), out))
    if card.get("voice_line"):
        said = f'<p class="line">Said over it: “{_esc(card["voice_line"])}”</p>'
    else:
        said = '<p class="line">Nothing is said over it.</p>'
    made = "your own image" if card.get("image") else "made from your brand and website"
    warns = "".join(f'<li class="finding warning"><span class="sev">Warning</span> <div>{_esc(w)}</div></li>'
                    for w in card.get("warnings") or [])  # fmt: skip
    secs = float(card.get("seconds") or 0)
    return (f'<{tag} class="shot panel closing" id="closing" data-title="Closing card" data-time="the end" '
            f"data-cards='[]'>"
            f'<button class="thumb" type="button" data-src="{src}" aria-label="Enlarge the closing card">'
            f'<img class="still" src="{src}" alt="Closing card" loading="lazy"></button>'
            f'<div class="meta"><h3>Closing card</h3><span class="time">at least {secs:.1f} s</span></div>'
            f'<p class="line">The video ends on this ({made}) after the story\'s last line. It adds no music or '
            f"sound.</p>{said}" + (f'<ul class="findings">{warns}</ul>' if warns else "") + f"</{tag}>")  # fmt: skip


def render_storyboard(short: Short, tl: Timeline, stills_dir: Path, out: Path, rules: list[dict] | None = None,
                      closing: dict | None = None) -> Path:  # fmt: skip
    """The storyboard page; `closing` (closing_info) adds the closing card after the shots."""
    out = Path(out)
    spans = {s.id: s for s in tl.shots}
    total_frames = max(tl.n_frames, 1)
    size = int(short.style.get("caption", {}).get("size", 64)) if isinstance(short.style, dict) else 64

    segments, shots = [], []
    for i, shot in enumerate(short.shots, 1):
        span = spans.get(shot.id)
        if span is None:
            label = "not timed yet"
        else:
            label = f"{_secs((span.f0 - 1) / tl.fps)} to {_secs(span.f1 / tl.fps)}"
            segments.append(
                f'<a class="seg" href="#shot-{_esc(shot.id)}" style="flex:{span.f1 - span.f0 + 1} 1 0" '
                f'title="{_esc(shot.title)}, {label}">{i}</a>'
            )
        line_spans = {s.id: s for s in tl.lines}
        line_html, all_cards = [], []
        for ln in _lines_of(short, shot.id):
            cards = [c.text for c in tl.cards if c.line_id == ln.id]
            all_cards += cards
            chips = "".join(f'<span class="card">{_esc(c)}</span>' for c in cards)
            ls = line_spans.get(ln.id)
            when = f'<span class="time"> {_secs((ls.f0 - 1) / tl.fps)}</span>' if ls else ""
            line_html.append(f'<p class="line">{_esc(ln.text)}{when}<span class="cards">{chips}</span></p>')
        src = _esc(_rel(Path(stills_dir) / f"{shot.id}.jpg", out))
        badge = f'<span class="badge">{_esc(shot.badge)}</span>' if shot.badge else ""
        shots.append(
            f'<li class="shot panel" id="shot-{_esc(shot.id)}" data-title="{_esc(shot.title)}" '
            f"data-time=\"{label}\" data-cards='{_esc(json.dumps(all_cards))}'>"
            f'<button class="thumb" type="button" data-src="{src}" aria-label="Enlarge shot {i}">'
            f'<img class="still" src="{src}" alt="Shot {i}: {_esc(shot.title)}" loading="lazy"></button>'
            f'<div class="meta"><h3>{i}. {_esc(shot.title)}{badge}</h3><span class="time">{label}</span></div>'
            + "".join(line_html)
            + (
                '<p class="line">On screen: '
                + "".join(f'<span class="card">{_esc(label_text(t))}</span> ' for t in shot.labels)
                + "</p>"
                if shot.labels
                else ""
            )
            + "</li>"
        )

    safe = safe_rect()
    cap = caption_rect(CAPTION_LINES, size)
    text = _template("storyboard.html").substitute(
        page_title=_esc(f"Storyboard — {short.title}"),
        css=_css(),
        title=_esc(short.title),
        lead=_esc(f"{len(shots)} shots, {_secs(total_frames / tl.fps)} in total. Check the pictures and the captions."),
        segments="\n".join(segments),
        total_s=f"{total_frames / tl.fps:.1f}",
        shots="\n".join(shots) + _closing_html(closing, out, "li"),
        rules=rules_panel(rules, "Check every picture against these too, the closing card included."),
        cap_px=size,
        safe_l=_pct(safe.x, W),
        safe_t=_pct(safe.y, H),
        safe_w=_pct(safe.w, W),
        safe_h=_pct(safe.h, H),
        cap_l=_pct(cap.x, W),
        cap_t=_pct(cap.y, H),
        cap_w=_pct(cap.w, W),
        cap_h=_pct(cap.h, H),
    )
    return _write(out, text)


# --------------------------------------------------------------------------- final


def _copy_field(key: str, label: str, value: str) -> str:
    return (
        f'<div class="field"><div class="row"><label for="{key}">{_esc(label)}</label>'
        f'<button type="button" data-copy="{key}">Copy</button></div>'
        f'<pre class="copytext" id="{key}">{_esc(value)}</pre></div>'
    )


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


def _where(f) -> str:
    shots = tuple(getattr(f, "shots", ()) or ())
    scene = getattr(f, "scene", None)
    parts = [
        f"shot{'s' if len(shots) > 1 else ''} {', '.join(shots)}" if shots else "",
        f"scene {scene}" if scene else "",
        str(f.obj) if f.obj else "",
        f"frame {f.frame}" if f.frame is not None else "",
    ]
    return " · ".join(_esc(x) for x in parts if x)


def _findings_html(findings: list, checks_run: bool = True, redo_all=()) -> str:
    if not findings and not checks_run:
        return '<div class="summary meh">Scene checks not run yet.</div>'
    if not findings:
        return '<div class="summary ok">All checks passed. No problems found.</div>'
    errors = [f for f in findings if f.severity == "error"]
    warnings = [f for f in findings if f.severity != "error"]
    parts = []
    if errors:
        parts.append(f"{len(errors)} problem{'s' if len(errors) != 1 else ''} to fix")
    if warnings:
        parts.append(f"{len(warnings)} warning{'s' if len(warnings) != 1 else ''}")
    cls = "bad" if errors else "meh"
    items = []
    for f in [*errors, *warnings]:
        where = _where(f)
        redo = getattr(f, "redo", None)
        kind = "Problem" if f.severity == "error" else "Warning"
        items.append(
            f'<li class="finding {"error" if f.severity == "error" else "warning"}">'
            f'<span class="sev">{kind}</span> <strong>{_esc(f.check.replace("_", " "))}</strong>'
            f"<div>{_esc(f.message)}</div>" + (f'<div class="where">{where}</div>' if where else "")
            + (f'<div class="where">Render again: <code>{_esc(redo)}</code></div>' if redo else "") + "</li>"
        )
    more = "".join(f'<p class="where">Render every flagged frame again: <code>{_esc(c)}</code></p>' for c in redo_all)
    return (f'<div class="summary {cls}">{", ".join(parts)}</div><ul class="findings">{"".join(items)}</ul>'
            f"{more}")  # fmt: skip


def render_final(short: Short, findings: list, mp4: Path, out: Path, checks_run: bool = True, redo_all=(),
                 rules: list[dict] | None = None, closing: dict | None = None) -> Path:  # fmt: skip
    """The final page. `findings` are ReviewFinding (load_findings) or Finding objects. `checks_run` False (no
    review/checks.json) says so instead of "All checks passed". `redo_all`: the frame checks' commands that render
    every flagged frame again."""
    out, mp4 = Path(out), Path(mp4)
    post = short.post or {}
    tags = " ".join(post.get("hashtags", []))
    info = f"{_esc(mp4.name)}"
    if mp4.exists():
        info += f" · {_mb(mp4.stat().st_size)}"
    player = (
        f'<video controls preload="metadata" playsinline src="{_esc(_rel(mp4, out))}">'
        "Your browser cannot play this video.</video>"
    )
    post_html = (
        _copy_field("post-title", "Title", post.get("title", ""))
        + _copy_field("post-desc", "Description", post.get("description", ""))
        + _copy_field("post-tags", "Hashtags", tags)
    )
    credits = _assets_table(short, "No outside assets were used.")
    text = _template("final.html").substitute(
        page_title=_esc(f"Final video — {short.title}"),
        css=_css(),
        title=_esc(short.title),
        player=player,
        fileinfo=info,
        post=post_html,
        checks=_findings_html(findings, checks_run, redo_all),
        closing=f'<h2>Closing card</h2><div class="strip">{_closing_html(closing, out)}</div>' if closing else "",
        rules=rules_panel(rules, "How this short keeps each of your rules."),
        credits=credits,
    )
    return _write(out, text)


# --------------------------------------------------------------------------- live render status

RELOAD_MS = 4000  # the status page reloads itself this often while the render runs


def _clock(epoch) -> str:
    import time

    if not isinstance(epoch, (int, float)):
        return "-"
    return time.strftime("%H:%M:%S", time.localtime(epoch))


def _bar(reused: int, fresh: int, total: int) -> str:
    if total <= 0:
        return ""
    return (f'<span class="reused" style="width:{100 * reused / total:.3f}%" title="reused from disk"></span>'
            f'<span class="fresh" style="width:{100 * fresh / total:.3f}%" title="rendered this run"></span>')  # fmt: skip


def _stat(key: str, value: str, sub: str = "") -> str:
    sub_html = f'<span class="s">{_esc(sub)}</span>' if sub else ""
    return f'<li><span class="k">{_esc(key)}</span><span class="v">{_esc(value)}</span>{sub_html}</li>'


def _on_off(v) -> str | None:
    return None if v is None else ("on" if v else "off")


def _status_settings(st: dict) -> str:
    s = st.get("settings") or {}
    rows = []

    def row(k, v):
        if v not in (None, "", []):
            rows.append(f"<tr><th>{_esc(k)}</th><td>{_esc(v)}</td></tr>")

    row("Engine", {"cycles": "Cycles", "eevee": "EEVEE"}.get(st.get("engine") or "", st.get("engine")))
    row("Preset", st.get("preset"))
    res, pct = s.get("resolution"), s.get("percent")
    if res:
        scale = (pct or 100) / 100
        row("Resolution", f"{res[0]} x {res[1]} at {pct or 100}% = {round(res[0] * scale)} x {round(res[1] * scale)} px")
    row("Frame rate", f"{s['fps']} fps" if s.get("fps") else None)
    row("Samples", s.get("samples"))
    if s.get("adaptive_threshold") is not None:
        row("Adaptive sampling", f"noise threshold {s['adaptive_threshold']}")
    row("Denoise", _on_off(s.get("denoise")))
    row("Motion blur", _on_off(s.get("motion_blur")))
    device = s.get("device")
    if device and s.get("backend"):
        device = f"{device} ({s['backend']})"
    row("Device", device)
    row("GPU", ", ".join(s["gpus"]) if s.get("gpus") else s.get("gpu"))
    for g in s.get("vram") or []:
        if g.get("total_gb") is not None and g.get("used_gb") is not None:
            free = round(g["total_gb"] - g["used_gb"], 1)
            row(f"Video memory ({g.get('name', 'GPU')})",
                f"{g['used_gb']} GB used, {free} GB free of {g['total_gb']} GB (read {_clock(g.get('at'))})")  # fmt: skip
    row("Output", s.get("output"))
    stamp = st.get("stamp") or {}
    if isinstance(stamp.get("blend"), (int, float)):
        row("Scene file", f"blend/short.blend, saved {_clock(stamp['blend'] / 1e9)}")
    row("Timeline", stamp.get("timeline"))
    row("Blender", s.get("blender"))
    row("Process", f"pid {st.get('pid')}" if st.get("pid") else None)
    return "".join(rows) or '<tr><td class="empty">Not known yet.</td></tr>'


def _status_checks(st: dict) -> str:
    c = st.get("checks") or {}
    n = c.get("frames_checked", 0)
    budget = 100 * (c.get("budget") or 0.02)
    head = (f"{n} frame{'s' if n != 1 else ''} checked for black frames and fireflies so far (a sample of the fresh "
            f"frames, kept under {budget:.0f}% of the render time); every fresh frame is checked for stuck frames. "
            "BLENDER frame-check checks every frame afterwards.")  # fmt: skip
    if c.get("live") is False:
        head = "Black-frame and firefly checks are not available live on this Blender. " + head
    errors, warnings = c.get("errors", 0), c.get("warnings", 0)
    if not errors and not warnings:
        return f'<div class="panel"><p style="margin:0">{_esc(head)} Nothing found.</p></div>'
    items = []
    for f in c.get("findings") or []:
        err = f.get("severity") == "error"
        where = " · ".join(x for x in (f"shot {f['shot']}" if f.get("shot") else "",
                                         f"frame {f['frame']}" if f.get("frame") is not None else "") if x)  # fmt: skip
        items.append(f'<li class="finding {"error" if err else "warning"}"><span class="sev">'
                     f'{"Problem" if err else "Warning"}</span><strong>'
                     f'{_esc(str(f.get("check", "")).replace("_", " "))}</strong>'
                     f'<div>{_esc(f.get("message", ""))}</div><div class="where">{_esc(where)}</div></li>')  # fmt: skip
    more = errors + warnings - len(items)
    extra = f'<p class="where">And {more} more.</p>' if more > 0 else ""
    return (f'<p class="lead" style="margin-bottom:10px">{_esc(head)}</p><ul class="findings">{"".join(items)}</ul>'
            f"{extra}")  # fmt: skip


def _shot_reason(sh: dict) -> str:
    """Why a shot renders in this run, or that it is kept: "kept (unchanged)", "rendered again: fingerprint
    changed", "resuming from the frames on disk", "redoing frames 120-140"."""
    if "reason" not in sh:
        return ""
    why = sh["reason"]
    if why is None:
        return "kept (unchanged)" if sh.get("reused") == sh.get("total") else ""
    if why == "resuming":
        return "resuming from the frames on disk"
    if why.startswith("redo"):
        return why
    return f"rendered again: {why}"


def _status_shots(st: dict) -> str:
    out = []
    for sh in st.get("shots") or []:
        total = sh.get("total") or 0
        if sh.get("thumb"):
            img = (f'<img src="{_esc(sh["thumb"])}?f={_esc(sh.get("thumb_frame"))}" '
                   f'alt="Shot {_esc(sh["id"])}, frame {_esc(sh.get("thumb_frame"))}">')  # fmt: skip
        else:
            img = '<div class="ph">no frame yet</div>'
        state = {"pending": "waiting", "rendering": "rendering", "done": "done"}.get(sh.get("state"), sh.get("state"))
        cur = ""
        if sh.get("state") == "rendering" and sh.get("current_frame"):
            cur = f" · now frame {sh['current_frame']}"
        flags = ""
        if sh.get("errors"):
            flags += f'<p class="flag err">{sh["errors"]} problem{"s" if sh["errors"] != 1 else ""}</p>'
        if sh.get("warnings"):
            flags += f'<p class="flag">{sh["warnings"]} warning{"s" if sh["warnings"] != 1 else ""}</p>'
        reused, done = sh.get("reused", 0), sh.get("done", 0)
        why = _shot_reason(sh)
        why = f'<p class="why">{_esc(why)}</p>' if why else ""
        out.append(
            f'<li class="shot panel {_esc(sh.get("state", ""))}">{img}'
            f'<h3>{_esc(sh["id"])} <span class="state">{_esc(state)}</span></h3>'
            f'<div class="bar">{_bar(reused, done - reused, total)}</div>'
            f"<p>{done} of {total} frames{cur}</p>"
            f'<p>{sh.get("rendered", 0)} rendered · {reused} reused · {sh.get("stale", 0)} stale replaced · '
            f'frames {sh.get("f0")}-{sh.get("f1")}</p>{why}{flags}</li>'
        )
    return "".join(out) or '<li class="empty">No shots.</li>'


def _render_body(st: dict, running: bool) -> str:
    """The render's panels: the overall bar and numbers, the shots, the live frame checks and the settings."""
    from bsa.render_status import fmt_duration

    fr = st.get("frames") or {}
    total, done, reused = fr.get("total", 0), fr.get("done", 0), fr.get("reused", 0)
    pct = 100.0 * done / total if total else (100.0 if st.get("state") == "done" else 0.0)
    eta = st.get("eta_s")
    eta_sub = f"finishes about {_clock(st.get('eta_at'))} ({st.get('eta_source')})" if running and eta is not None else ""
    avg, recent = st.get("avg_s_per_frame"), st.get("recent_s_per_frame")
    stats = "".join([
        _stat("Frames", f"{done} of {total}", f"{pct:.0f}% · {fr.get('to_render', 0)} to render this run"),
        _stat("Rendered this run", str(fr.get("rendered", 0)),
              f"{reused} reused from disk · {fr.get('stale', 0)} stale replaced"),
        _stat("Elapsed", fmt_duration(st.get("elapsed_s")), f"started {_clock(st.get('started'))}"),
        _stat("Time left", fmt_duration(eta) if running else "-", eta_sub),
        _stat("Per frame", f"{avg:.2f} s" if avg else "-", f"last frames {recent:.2f} s" if recent else ""),
        _stat("Status overhead", f"{st.get('overhead_pct') or 0:.1f}%", "status page and live checks"),
    ])  # fmt: skip
    return (f'<section class="panel"><div class="bar big" role="progressbar" aria-valuemin="0" aria-valuemax="100" '
            f'aria-valuenow="{pct:.0f}" aria-label="Frames done">{_bar(reused, done - reused, total)}</div>'
            f'<ul class="stats">{stats}</ul></section>'
            f'<h2>Shots</h2><ul class="shots">{_status_shots(st)}</ul>'
            f"<h2>Frame checks</h2>{_status_checks(st)}"
            f'<h2>Render settings</h2><div class="panel tablewrap"><table><tbody>{_status_settings(st)}</tbody></table>'
            "</div>")  # fmt: skip


def _page(st: dict, out: Path, *, page_title: str, step: str, title: str, pill: str, pill_class: str, lead: str,
          notice: str, body: str, running: bool, foot: str) -> Path:  # fmt: skip
    from bsa.render_status import write_atomic

    stall = st.get("stall_after_s")
    text = _template("render_status.html").substitute(
        page_title=_esc(page_title),
        refresh=f'<noscript><meta http-equiv="refresh" content="{RELOAD_MS // 1000}"></noscript>' if running else "",
        css=_css(),
        step=_esc(step),
        title=_esc(title),
        pill=_esc(pill),
        pill_class=pill_class,
        lead=_esc(lead),
        notice=notice,
        body=body,
        updated=_esc(_clock(st.get("updated"))),
        updated_epoch=_esc(st.get("updated") or 0),
        stall_after=_esc(stall if stall is not None else "Infinity"),  # a step that can't report progress: no alarm
        foot=_esc(foot if running else ""),
        running_js="true" if running else "false",
        reload_js=f"setTimeout(function () {{ location.reload(); }}, {RELOAD_MS});" if running else "",
    )
    write_atomic(out, text)  # rewritten every few seconds: a reload must never read half a file
    return out


def render_status_page(status: dict, out: Path) -> Path:
    """The live status page from a status (bsa.render_status.describe's output, with `alive` and `stale`): the
    preview chain or a single step (its steps, the current one with its progress, the render's panels once it
    renders, the previous run's outcome), or a render-only status. While it runs the page reloads itself every few
    seconds (a file:// page cannot fetch the JSON, so the writer rewrites this page with the data in it); once it is
    done, failed, stopped or dead it stays still."""
    if "run" in status:
        return _run_status_page(status, Path(out))
    out = Path(out)
    st = status
    preset = st.get("preset") or "?"
    state = st.get("state") or "none"
    short = st.get("short") or {}
    running = state == "running" and not st.get("stale")
    fr = st.get("frames") or {}
    total, done = fr.get("total", 0), fr.get("done", 0)
    again = f"BLENDER render SHORT --preset {preset}"
    notice = ""
    if st.get("stale"):
        pill, pill_class = "Not running", "bad"
        notice = (f'<div class="notice bad">Not running: the render&#x27;s Blender process (pid {_esc(st.get("pid"))})'
                  f" is gone, at {done} of {total} frames. Run <code>{_esc(again)}</code> again: it resumes from the "
                  "frames on disk.</div>")  # fmt: skip
    elif state == "running":
        pill, pill_class = "Running", "running"
    elif state == "done":
        pill, pill_class = "Done", "done"
    elif state in ("failed", "stopped"):
        pill, pill_class = state.capitalize(), "bad" if state == "failed" else "meh"
        why = f": {_esc(st['error'])}." if st.get("error") else "."
        notice = (f'<div class="notice {pill_class}">The render {state} at {done} of {total} frames{why} Run '
                  f"<code>{_esc(again)}</code> again to resume from the frames on disk.</div>")  # fmt: skip
    else:
        pill, pill_class = "Not started", "meh"
        notice = f'<div class="notice meh">No {_esc(preset)} render has started for this short yet.</div>'
    lead = _render_lead(st, running, state)
    title = short.get("title") or short.get("slug") or "Render"
    return _page(st, out, page_title=f"{preset.capitalize()} render — {title}", step=f"{preset} render", title=title,
                 pill=pill, pill_class=pill_class, lead=lead, notice=notice, body=_render_body(st, running),
                 running=running, foot="This page refreshes itself every few seconds while the render runs.")  # fmt: skip


def _render_lead(st: dict, running: bool, state: str) -> str:
    cur = st.get("current") or {}
    if running and st.get("phase") == "anchors":
        return "Every frame is rendered; placing the labels' anchors."
    if running and cur.get("shot"):
        return f"Rendering shot {cur['shot']}" + (f", frame {cur['frame']}." if cur.get("frame") else ".")
    if state == "done":
        return "Every frame is on disk."
    return "Live status of the render. This page updates itself while the render runs."


STEP_WORDS = {"pending": "waiting", "running": "running", "done": "done", "skipped": "skipped", "failed": "failed",
              "stopped": "stopped"}  # fmt: skip


def _summary_text(summary) -> str:
    if not summary:
        return ""
    if not isinstance(summary, dict):
        return str(summary)
    if "skipped" in summary:
        return str(summary["skipped"])
    parts = []
    for k, v in summary.items():
        if v in (None, [], {}, ""):
            continue
        if isinstance(v, list):
            v = ", ".join(x if isinstance(x, str) else " ".join(f"{a} {b}" for a, b in x.items())
                          if isinstance(x, dict) else str(x) for x in v)  # fmt: skip
        parts.append(f"{k.replace('_', ' ')}: {v}")
    return " · ".join(parts)


def _progress_line(p: dict | None) -> str:
    from bsa.render_status import fmt_duration

    if not p or not p.get("total"):
        return ""
    line = f"{p.get('done', 0)} of {p['total']} {p.get('unit', 'frames')} ({p.get('percent', 0):.0f}%)"
    if p.get("current"):
        line += f", {p['current']}"
    if p.get("eta_s") is not None:
        line += f", about {fmt_duration(p['eta_s'])} left"
    return line


def _status_steps(st: dict, now: float) -> str:
    from bsa.render_status import fmt_duration

    run = st.get("run") or {}
    current = (st.get("step") or {}).get("name")
    rows = []
    for i, s in enumerate(run.get("steps") or [], 1):
        state = s.get("state") or "pending"
        cls = f"step-row {state}" + (" current" if s["name"] == current else "")
        started, finished = s.get("started"), s.get("finished")
        took = ""
        if isinstance(started, (int, float)):
            end = finished if isinstance(finished, (int, float)) else now
            took = fmt_duration(end - started)
        bits = [b for b in (_progress_line(s.get("progress")) if state == "running" else "",
                            _summary_text(s.get("summary")), s.get("error") or "",
                            f"took {took}" if took and state != "running" else (f"for {took}" if took else ""))
                if b]  # fmt: skip
        if state == "running" and s["name"] == "render" and st.get("render"):
            fr = st["render"].get("frames") or {}
            bits.insert(0, f"{fr.get('done', 0)} of {fr.get('total', 0)} frames (details below)")
        rows.append(f'<li class="{cls}"><span class="n">{i}</span><span class="name">{_esc(s["name"])}</span>'
                    f'<span class="st">{_esc(STEP_WORDS.get(state, state))}</span>'
                    f'<span class="sum">{_esc(" · ".join(bits))}</span></li>')  # fmt: skip
    return f'<h2>Steps</h2><ol class="steps">{"".join(rows)}</ol>'


def _status_now(st: dict) -> str:
    """The current step's progress panel (a step that counts frames: check, frame-check, edit)."""
    from bsa.render_status import fmt_duration

    step = st.get("step") or {}
    p = step.get("progress")
    if not p or step.get("name") == "render" or not p.get("total"):
        return ""
    running = step.get("state") == "running" and st.get("alive")
    done, total = p.get("done", 0), p["total"]
    per = p.get("s_per_unit")
    unit = p.get("unit", "frames")
    stats = "".join([
        _stat(unit.capitalize(), f"{done} of {total}", f"{p.get('percent', 0):.0f}%"),
        _stat("Now on", p.get("current") or "-"),
        _stat("Elapsed", fmt_duration(p.get("elapsed_s"))),
        _stat("Time left", fmt_duration(p.get("eta_s")) if running and p.get("eta_s") is not None else "-",
              f"finishes about {_clock(p.get('eta_at'))} (measured)" if running and p.get("eta_at") else ""),
        _stat(f"Per {unit.rstrip('s')}", f"{per:.3f} s" if per else "-"),
    ])  # fmt: skip
    items = ""
    if p.get("items"):
        lis = "".join(f'<li class="{_esc(it.get("state", ""))}">{_esc(it.get("id"))}: {it.get("done", 0)} of '
                      f'{it.get("total", 0)} · {_esc(it.get("state", ""))}</li>' for it in p["items"])  # fmt: skip
        items = f'<ul class="items">{lis}</ul>'
    return (f'<section class="panel now"><h2>Now: {_esc(step.get("name"))}</h2>'
            f'<div class="bar big" role="progressbar" aria-valuemin="0" aria-valuemax="100" '
            f'aria-valuenow="{p.get("percent", 0):.0f}" aria-label="{_esc(unit)} done">'
            f'<span class="fresh" style="width:{100 * done / total:.3f}%"></span></div>'
            f'<ul class="stats">{stats}</ul>{items}</section>')  # fmt: skip


def _run_status_page(st: dict, out: Path) -> Path:
    import time

    from bsa.render_status import again_command, run_label

    preset = st.get("preset") or "?"
    state = st.get("state") or "none"
    kind = st.get("kind") or "?"
    short = st.get("short") or {}
    running = state == "running" and not st.get("stale")
    now = st.get("updated") or time.time()
    step = st.get("step") or {}
    again = again_command(kind, preset)
    label = run_label(kind, preset)
    notice = ""
    if st.get("stale"):
        pill, pill_class = "Not running", "bad"
        at = f" during {_esc(step['name'])}" if step.get("name") else ""
        notice = (f'<div class="notice bad">Not running: its Blender process (pid {_esc(st.get("pid"))}) is gone{at}.'
                  f" Run <code>{_esc(again)}</code> again.</div>")  # fmt: skip
    elif state == "running":
        pill, pill_class = "Running", "running"
    elif state == "done":
        pill, pill_class = "Done", "done"
    else:
        pill, pill_class = state.capitalize(), "bad" if state == "failed" else "meh"
        at = f" at {_esc(step['name'])}" if step.get("name") else ""
        why = f": {_esc(st['error'])}" if st.get("error") else ""
        notice = (f'<div class="notice {pill_class}">{_esc(label.capitalize())} {state}{at}{why}. Run '
                  f"<code>{_esc(again)}</code> again.</div>")  # fmt: skip
    lead = str(st.get("summary", ""))
    lead = lead[:1].upper() + lead[1:]
    prev = st.get("previous")
    prev_html = ""
    if prev:
        prev_html = (f'<p class="prev">Previous run (superseded by this one): '
                     f'{_esc(prev.get("summary") or prev.get("state"))}, ended {_esc(_clock(prev.get("finished")))}.'
                     "</p>")  # fmt: skip
    render = st.get("render")
    body = prev_html + _status_now(st) + _status_steps(st, now)
    if render:
        r_running = running and render.get("state") == "running"
        body += f"<h2>Render</h2>{_render_body(dict(render, stale=False), r_running)}"
    title = short.get("title") or short.get("slug") or "Status"
    return _page(st, out, page_title=f"{label.capitalize()} — {title}", step=label, title=title, pill=pill,
                 pill_class=pill_class, lead=lead, notice=notice, body=body, running=running,
                 foot="This page refreshes itself every few seconds while it runs.")  # fmt: skip


def open_page(path: Path) -> None:
    webbrowser.open(Path(path).absolute().as_uri())
