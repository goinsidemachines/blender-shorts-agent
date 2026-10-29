"""Find Blender, its version and its bundled Python. Stdlib only."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

MIN_BLENDER = (4, 5)

PROGRAM_FILES = os.environ.get("ProgramFiles", r"C:\Program Files")

_OTHER_CANDIDATES = [
    Path("/Applications/Blender.app/Contents/MacOS/Blender"),
    Path("/usr/bin/blender"),
    Path("/snap/bin/blender"),
]

_SHIM_SUFFIXES = {".cmd", ".bat"}

_HOW_TO_SET = (
    "Blender was not found. Install Blender 4.5 or newer from https://www.blender.org/download/, "
    'or point to it: set "blender_exe" in ~/.blender-shorts-agent/settings.json '
    "or set the BLENDER_EXE environment variable to the full path of the Blender executable."
)


class BlenderNotFound(RuntimeError):
    """Raised when no Blender executable can be located."""


def _is_file(value) -> Path | None:
    if not value:
        return None
    path = Path(value)
    return path if path.is_file() else None


def _windows_installs() -> list[Path]:
    base = Path(PROGRAM_FILES) / "Blender Foundation"
    if not base.is_dir():
        return []
    found = []
    for folder in base.glob("Blender *"):
        exe = folder / "blender.exe"
        match = re.search(r"(\d+(?:\.\d+)*)\s*$", folder.name)
        if match and exe.is_file():
            found.append((tuple(int(p) for p in match.group(1).split(".")), exe))
    return [exe for _, exe in sorted(found, reverse=True)]


def find_blender(settings: dict | None = None) -> Path:
    """Locate the Blender executable: settings, BLENDER_EXE, PATH, then standard install folders."""
    exe = _is_file((settings or {}).get("blender_exe")) or _is_file(os.environ.get("BLENDER_EXE"))
    if exe:
        return exe
    on_path = _is_file(shutil.which("blender"))
    # Batch shims (.cmd/.bat) mangle quoted arguments with spaces, so prefer a real executable.
    if on_path and on_path.suffix.lower() not in _SHIM_SUFFIXES:
        return on_path
    for candidate in _windows_installs() + list(_OTHER_CANDIDATES):
        if candidate.is_file():
            return candidate
    if on_path:
        return on_path
    raise BlenderNotFound(_HOW_TO_SET)


def parse_version(text: str) -> tuple[int, int]:
    match = re.search(r"Blender\s+(\d+)\.(\d+)", text)
    if not match:
        raise ValueError(f"Could not read a Blender version from: {text[:80]!r}")
    return int(match.group(1)), int(match.group(2))


def blender_version(exe: Path) -> tuple[int, int]:
    result = subprocess.run([str(exe), "--version"], check=False, capture_output=True, text=True, timeout=60)
    return parse_version(result.stdout)


def blender_python(exe: Path) -> Path:
    """Blender's bundled Python: <dir>/<major.minor>/python/bin/python(.exe), else ask Blender."""
    exe = Path(exe)
    major, minor = blender_version(exe)
    bin_dir = exe.parent / f"{major}.{minor}" / "python" / "bin"
    if bin_dir.is_dir():
        names = ["python.exe", "python3", "python"] + sorted(p.name for p in bin_dir.glob("python3.*"))
        for name in names:
            if (bin_dir / name).is_file():
                return bin_dir / name
    return _python_from_blender(exe)


def _python_from_blender(exe: Path) -> Path:
    code = "import sys; print('BSA_PYTHON=' + sys.executable)"
    result = subprocess.run(
        [str(exe), "-b", "--factory-startup", "--python-expr", code],
        check=False, capture_output=True, text=True, timeout=120,
    )  # fmt: skip
    match = re.search(r"^BSA_PYTHON=(.+)$", result.stdout, re.MULTILINE)
    if match:
        path = Path(match.group(1).strip())
        if path.is_file():
            return path
    raise FileNotFoundError(f"Could not find the Python bundled with Blender at {exe}")


def check_blender(exe: Path) -> list[str]:
    """Return a list of problems with this Blender (empty if it is usable)."""
    problems = []
    try:
        version = blender_version(exe)
    except (ValueError, OSError, subprocess.SubprocessError) as err:
        return [f"Could not run Blender at {exe}: {err}"]
    if version < MIN_BLENDER:
        problems.append(
            f"Blender {version[0]}.{version[1]} is too old: this needs Blender {MIN_BLENDER[0]}.{MIN_BLENDER[1]} or newer."
        )
    try:
        blender_python(exe)
    except (FileNotFoundError, OSError, subprocess.SubprocessError) as err:
        problems.append(f"Could not find Blender's bundled Python: {err}")
    return problems
