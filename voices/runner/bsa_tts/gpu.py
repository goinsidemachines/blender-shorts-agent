"""Free video memory via nvidia-smi (no torch needed in the runner environment)."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from shutil import which

_run = subprocess.run
_TIMEOUT = 15


@dataclass
class GpuState:
    available: bool
    total_mb: int = 0
    used_mb: int = 0
    name: str = ""

    @property
    def free_mb(self) -> int:
        return max(0, self.total_mb - self.used_mb)


def query() -> GpuState:
    """The NVIDIA GPU with the most free memory, or `available=False` when there is no NVIDIA GPU or driver."""
    smi = which("nvidia-smi")
    if not smi:
        return GpuState(available=False)
    try:
        out = _run(
            [smi, "--query-gpu=memory.total,memory.used", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=_TIMEOUT,
        )  # fmt: skip
    except (OSError, subprocess.SubprocessError):
        return GpuState(available=False)
    if out.returncode != 0:
        return GpuState(available=False)
    best = None
    for row in (out.stdout or "").splitlines():
        try:
            total, used = (int(float(x)) for x in row.split(","))
        except ValueError:
            continue
        state = GpuState(available=True, total_mb=total, used_mb=used)
        if best is None or state.free_mb > best.free_mb:
            best = state
    return best or GpuState(available=False)
