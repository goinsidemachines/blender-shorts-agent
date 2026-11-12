"""Heavy-scene warnings: the thresholds and the messages. The scan itself (bsa/blender/scenescan.py) reads the scene;
nothing here needs Blender."""

from __future__ import annotations

BIG_BLEND_MB = 300  # a saved .blend over this is heavy
COPY_LIMIT = 20  # this many objects with a mesh of their own and identical geometry share one shape
FREESTYLE_LONG_FRAMES = 600  # Freestyle over a frame range this long is slow and memory hungry


def boolean_message(obj: str, mod: str, why: str) -> str:
    return (f"{obj}: live Boolean {mod!r} on {why}. It is evaluated again on every frame. Apply it once at build "
            "time (bpy.ops.object.modifier_apply on the frame it is right for), or build the opening into the mesh")


def size_message(mb: float) -> str:
    return (f"the saved .blend is {mb:.0f} MB (over {BIG_BLEND_MB} MB): large scenes are often many copied meshes. "
            "Share one mesh between copies, and expect a slow, memory-hungry render")


def copies_message(count: int, sample: str) -> str:
    return (f"{count} objects each have their own mesh with identical geometry (like {sample!r}): share one mesh "
            "instead, bpy.data.objects.new(name, shared_mesh), so the scene holds one copy")


def freestyle_message(frames: int) -> str:
    return (f"Freestyle is on over {frames} frames: it is slow and memory hungry. Render in chunks "
            "(render.chunk_frames) and keep the memory guard on")


def over_copy_limit(count: int) -> bool:
    return count >= COPY_LIMIT
