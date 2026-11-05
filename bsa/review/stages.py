"""The review page's stages, in order, and how they map onto jobstate's finer pipeline stages."""

from __future__ import annotations

from collections.abc import Iterable

# Every page stage, for validation. "camera" is optional: a short's own stages are page_stages(camera).
PAGE_STAGES = ("interview", "story", "storyboard", "camera", "engine", "narration", "render", "final", "post")
LABELS = {
    "interview": "Interview",
    "story": "Story",
    "storyboard": "Storyboard",
    "camera": "Camera",
    "engine": "Engine",
    "narration": "Narration",
    "render": "Render",
    "final": "Final",
    "post": "Post",
}
APPROVAL_STAGES = ("story", "storyboard", "camera", "engine")
ACK_STAGES = ("final",)
# A page stage that stands for several jobstate stages; it is done when all of them are. Others cover only themselves.
COVERS = {"render": ("build", "checks", "preview")}


def page_stages(camera: bool) -> tuple[str, ...]:
    """A short's page stages in order: with the camera stage (after the storyboard) only when the short wants it."""
    return tuple(s for s in PAGE_STAGES if camera or s != "camera")


def statuses(done: Iterable[str], page: tuple[str, ...] = page_stages(False)) -> dict[str, str]:
    """Each of the short's page stages (`page`) as "done", "current" (the first not done) or "locked" (after the
    current one)."""
    done = set(done)
    out: dict[str, str] = {}
    current_seen = False
    for stage in page:
        if all(s in done for s in COVERS.get(stage, (stage,))):
            out[stage] = "done"
        elif not current_seen:
            out[stage] = "current"
            current_seen = True
        else:
            out[stage] = "locked"
    return out
