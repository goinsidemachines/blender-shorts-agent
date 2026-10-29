"""Machine check: CPU, RAM, GPU and how much video memory is already in use."""

from __future__ import annotations

import json
import os
import platform
import re
import subprocess
from dataclasses import dataclass, field

_TIMEOUT = 10


@dataclass
class Gpu:
    name: str
    vendor: str  # "nvidia", "amd", "intel", "apple" or "unknown"
    vram_gb: float | None = None
    vram_used_gb: float | None = None

    @property
    def free_gb(self) -> float | None:
        """Free video memory in GB, or None when the total or the amount in use is unknown."""
        if self.vram_gb is None or self.vram_used_gb is None:
            return None
        return round(self.vram_gb - self.vram_used_gb, 1)


@dataclass
class Machine:
    os: str
    cpu: str
    cores: int
    ram_gb: float | None
    gpus: list[Gpu] = field(default_factory=list)


def _vendor(name: str) -> str:
    n = name.lower()
    if "nvidia" in n or "geforce" in n or "quadro" in n:
        return "nvidia"
    if "amd" in n or "radeon" in n or "advanced micro" in n or "ati " in n:
        return "amd"
    if "intel" in n:
        return "intel"
    if "apple" in n:
        return "apple"
    return "unknown"


def _out(run, cmd: list[str]) -> str | None:
    """Run a tool and return its stdout, or None on any failure."""
    try:
        r = run(cmd, capture_output=True, text=True, timeout=_TIMEOUT)
    except Exception:  # noqa: BLE001 - detection must never raise, whatever the tool does
        return None
    if getattr(r, "returncode", 1) != 0:
        return None
    out = getattr(r, "stdout", None)
    return out if isinstance(out, str) else None


def _gb(mib: float) -> float:
    return round(mib / 1024, 1)


def _nvidia(run) -> list[Gpu]:
    out = _out(
        run,
        ["nvidia-smi", "--query-gpu=name,memory.total,memory.used", "--format=csv,noheader,nounits"],
    )
    gpus = []
    for line in (out or "").splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) != 3 or not parts[0]:
            continue
        try:
            gpus.append(Gpu(parts[0], "nvidia", _gb(float(parts[1])), _gb(float(parts[2]))))
        except ValueError:
            continue
    return gpus


def _windows_gpus(run) -> list[Gpu]:
    cmd = [
        "powershell",
        "-NoProfile",
        "-Command",
        "Get-CimInstance Win32_VideoController | Select-Object Name,AdapterRAM | ConvertTo-Json",
    ]
    out = _out(run, cmd)
    if not out:
        return []
    try:
        data = json.loads(out)
    except ValueError:
        return []
    if isinstance(data, dict):
        data = [data]
    gpus = []
    for item in data if isinstance(data, list) else []:
        name = item.get("Name") if isinstance(item, dict) else None
        if not name:
            continue
        ram = item.get("AdapterRAM")
        # AdapterRAM is a 32-bit field: it cannot be trusted at or above ~4 GB.
        vram = round(ram / 1024**3, 1) if isinstance(ram, (int, float)) and 0 < ram < 4_290_000_000 else None
        gpus.append(Gpu(name, _vendor(name), vram, None))
    return gpus


def _mac_gpus(run) -> list[Gpu]:
    out = _out(run, ["system_profiler", "SPDisplaysDataType", "-json"])
    if not out:
        return []
    try:
        items = json.loads(out).get("SPDisplaysDataType", [])
    except (ValueError, AttributeError):
        return []
    if not isinstance(items, list):
        return []
    gpus = []
    for item in items:
        name = item.get("sppci_model") or item.get("_name") if isinstance(item, dict) else None
        if name:
            gpus.append(Gpu(name, _vendor(name), None, None))
    return gpus


def _linux_gpus(run) -> list[Gpu]:
    out = _out(run, ["lspci"])
    gpus = []
    for line in (out or "").splitlines():
        if re.search(r"VGA compatible controller|3D controller|Display controller", line):
            name = line.split(": ", 1)[-1].strip()
            gpus.append(Gpu(name, _vendor(name), None, None))
    return gpus


def _ram_gb() -> float | None:
    try:
        if os.name == "nt":
            import ctypes

            class _Mem(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("sullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]

            m = _Mem()
            m.dwLength = ctypes.sizeof(_Mem)
            if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m)):
                return None
            return round(m.ullTotalPhys / 1024**3, 1)
        return round(os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 1024**3, 1)
    except (OSError, ValueError, AttributeError):
        return None


def detect(run=subprocess.run) -> Machine:
    system = platform.system()
    gpus = _nvidia(run)
    if system == "Windows":
        others = _windows_gpus(run)
    elif system == "Darwin":
        others = _mac_gpus(run)
    else:
        others = _linux_gpus(run)
    # nvidia-smi has the real numbers; skip the same cards reported again by the OS tools.
    gpus += [g for g in others if not (gpus and g.vendor == "nvidia")]
    return Machine(
        os=system or "unknown",
        cpu=platform.processor() or platform.machine() or "unknown",
        cores=os.cpu_count() or 1,
        ram_gb=_ram_gb(),
        gpus=gpus,
    )


def _base(path: str) -> str:
    return re.split(r"[\\/]", path.strip())[-1].lstrip(".")


def gpu_holders(run=subprocess.run, me: int | None = None) -> list[tuple[str, float | None]]:
    """(process name, MiB of video memory) for the processes using the GPU, biggest first, never the caller (`me`,
    default this process). When nvidia-smi can't give memory per process (Windows), the compute ("C") processes from
    its table with None for the memory; [] when it can't say at all."""
    me = os.getpid() if me is None else me
    out = _out(run, ["nvidia-smi", "--query-compute-apps=pid,process_name,used_memory", "--format=csv,noheader,nounits"])
    sized = []
    for line in (out or "").splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) != 3 or not parts[0].isdigit() or int(parts[0]) == me or parts[1].startswith("["):
            continue
        try:
            sized.append((float(parts[2]), _base(parts[1])))
        except ValueError:
            continue
    if sized:
        return [(name, mib) for mib, name in sorted(sized, reverse=True)]
    names = []
    for line in (_out(run, ["nvidia-smi"]) or "").splitlines():
        m = re.match(r"^\|\s*\d+\s+\S+\s+\S+\s+(\d+)\s+C\s+(.+?)\s+(?:\d+MiB|N/A)\s*\|$", line)
        if m and int(m.group(1)) != me:
            names.append(_base(m.group(2)))
    return [(name, None) for name in dict.fromkeys(names)]


def vram_warning(m: Machine, need_gb: float, run=subprocess.run, me: int | None = None) -> str | None:
    """A plain sentence when the best GPU has less free video memory than `need_gb`, else None. Names the programs
    holding the memory when nvidia-smi can say (it never stops them)."""
    known = [g for g in m.gpus if g.free_gb is not None]
    if not known:
        return None
    best = max(known, key=lambda g: g.free_gb)
    free = best.free_gb
    if free >= need_gb:
        return None
    holders = gpu_holders(run, me) if best.vendor == "nvidia" else []
    sizes = [mib for _, mib in holders if mib is not None]
    if holders and (len(holders) == 1 or (sizes and holders[0][1] >= sum(sizes) / 2)):
        who = f"{holders[0][0]} is using most of the video memory"
    elif holders:
        who = f"{', '.join(name for name, _ in holders[:3])} are using most of the video memory"
    else:
        who = "other programs are using it"
    return (
        f"This needs about {need_gb:.1f} GB of free video memory, but {best.name} has only {free:.1f} GB free: "
        f"{best.vram_used_gb:.1f} GB is in use and {who}. "
        "Closing other apps that use the GPU (browsers, games, other renders) would help."
    )


def describe(m: Machine) -> str:
    ram = f"{m.ram_gb:g} GB RAM" if m.ram_gb is not None else "unknown RAM"
    if m.gpus:
        parts = []
        for g in m.gpus:
            parts.append(f"{g.name} ({g.vram_gb:.1f} GB)" if g.vram_gb is not None else g.name)
        gpu = ", ".join(parts)
    else:
        gpu = "no GPU detected"
    return f"{m.os}, {m.cpu}, {m.cores} cores, {ram}, {gpu}"
