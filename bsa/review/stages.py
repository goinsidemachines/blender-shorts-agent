"""The review page's stages, in order, and how they map onto jobstate's finer pipeline stages."""

from __future__ import annotations

from collections.abc import Iterable

PAGE_STAGES = ("interview", "story", "storyboard", "engine", "narration", "render", "final", "post")
LABELS = {
    "interview": "Interview",
    "story": "Story",
    "storyboard": "Storyboard",
    "engine": "Engine",
    "narration": "Narration",
    "render": "Render",
    "final": "Final",
    "post": "Post",
}
APPROVAL_STAGES = ("story", "storyboard", "engine")
ACK_STAGES = ("final",)
# A page stage that stands for several jobstate stages; it is done when all of them are. Others cover only themselves.
COVERS = {"render": ("build", "checks", "preview")}


def statuses(done: Iterable[str]) -> dict[str, str]:
    """Each page stage as "done", "current" (the first not done) or "locked" (after the current one)."""
    done = set(done)
    out: dict[str, str] = {}
    current_seen = False
    for stage in PAGE_STAGES:
        if all(s in done for s in COVERS.get(stage, (stage,))):
            out[stage] = "done"
        elif not current_seen:
            out[stage] = "current"
            current_seen = True
        else:
            out[stage] = "locked"
    return out
