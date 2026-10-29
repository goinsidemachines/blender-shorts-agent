"""Runs inside Blender: blender -b --factory-startup --python-exit-code 1 --python runner.py -- <test module>

Imports the given test module (a dotted name or a .py path) and runs its test_* functions.
Exits non-zero if any test fails or the module has no test_* functions.
"""

import importlib
import importlib.util
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent


def _load(target):
    if sys.path[0] != str(ROOT):
        sys.path.insert(0, str(ROOT))
    path = Path(target)
    if path.suffix == ".py" and path.exists():
        spec = importlib.util.spec_from_file_location(path.stem, path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    return importlib.import_module(target)


def main():
    args = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    if not args:
        print("runner: no test module given")
        return 2
    module = _load(args[0])
    failures = 0
    names = sorted(n for n in dir(module) if n.startswith("test_") and callable(getattr(module, n)))
    if not names:
        print(f"runner: no test_* functions found in {args[0]}")
        return 3
    for name in names:
        try:
            getattr(module, name)()
            print(f"PASS {name}")
        except Exception:  # noqa: BLE001 - report every failure, keep going
            failures += 1
            print(f"FAIL {name}")
            traceback.print_exc()
    print(f"runner: {len(names) - failures} passed, {failures} failed")
    return 1 if failures else 0


code = main()
if code:
    sys.exit(code)
