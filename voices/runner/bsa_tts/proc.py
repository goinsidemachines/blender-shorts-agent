"""Start background programs without a console window (Windows).

A console program started from a process that has no console of its own (Blender, a hook, the page server) gets a new
visible console window. CREATE_NO_WINDOW stops that. Elsewhere nothing changes. Output capture, stdin, env, cwd and
timeouts behave exactly as with subprocess; flags the caller already gave (DETACHED_PROCESS ...) are kept.
Use this instead of calling subprocess.run / subprocess.Popen directly.
"""

from __future__ import annotations

import os
import subprocess

NO_WINDOW: dict = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}


def _merge(kw: dict) -> dict:
    flag = NO_WINDOW.get("creationflags", 0)
    if flag:
        kw["creationflags"] = kw.get("creationflags", 0) | flag
    return kw


def run(*args, **kw):
    return subprocess.run(*args, **_merge(kw))  # noqa: PLW1510 (the caller decides check)


def popen(*args, **kw):
    return subprocess.Popen(*args, **_merge(kw))
