"""`voice use-local`: an existing model folder and Python environment count as installed, nothing is downloaded."""

import hashlib
import json
import os
import sys
import venv
from pathlib import Path

import pytest

from bsa import proc as _proc
from bsa.narrate import install, local
from bsa.narrate.contract import NarrationError

FILES = {"model.bin": b"weights" * 100, "config.json": b'{"a": 1}'}


@pytest.fixture
def fake(tmp_path, monkeypatch, bsa_home):
    """A registry with one tiny model, an engine project with two pins, and a plugin voices folder to copy from."""
    plugin = tmp_path / "plugin_voices"
    (plugin / "runner").mkdir(parents=True)
    (plugin / "runner" / "pyproject.toml").write_text("", encoding="utf-8")
    (plugin / "README.md").write_text("x", encoding="utf-8")
    (plugin / "engines" / "eng").mkdir(parents=True)
    (plugin / "engines" / "eng" / "pyproject.toml").write_text(
        '[project]\nname = "e"\ndependencies = ["torch==2.11.0", "fakepkg==1.0", "other[x]>=1"]\n', encoding="utf-8"
    )
    reg = tmp_path / "registry.json"
    reg.write_text(
        json.dumps(
            {
                "models": {
                    "tiny": {
                        "kind": "asr",
                        "engine": "e",
                        "env": "eng",
                        "label": "t",
                        "licence": "MIT",
                        "weights": {
                            "repo": "o/tiny",
                            "revision": "abc",
                            "bytes": 1,
                            "files": {
                                n: {"bytes": len(b), "sha256": hashlib.sha256(b).hexdigest()} for n, b in FILES.items()
                            },
                        },
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(install, "REGISTRY", reg)
    monkeypatch.setattr(install, "PLUGIN_VOICES", plugin)
    monkeypatch.setattr(install, "uv_path", lambda: None)  # no uv: the runner step is skipped
    src = tmp_path / "mine"
    src.mkdir()
    for n, b in FILES.items():
        (src / n).write_bytes(b)
    return src


def make_env(root: Path, torch="2.11.0+cu128", fakepkg="1.0", other=True) -> Path:
    """A real (empty) venv with fake packages: a dist-info and a module each, so imports and versions work."""
    venv.create(root, with_pip=False)
    site = next((root / "lib").glob("python*/site-packages"), None) or root / "Lib" / "site-packages"
    for name, ver in (("torch", torch), ("fakepkg", fakepkg), ("other", "3.0" if other else None)):
        if ver is None:
            continue
        (site / f"{name}-{ver}.dist-info").mkdir()
        (site / f"{name}-{ver}.dist-info" / "METADATA").write_text(
            f"Metadata-Version: 2.1\nName: {name}\nVersion: {ver}\n"
        )
        (site / f"{name}.py").write_text(f"VERSION = {ver!r}\n")
    return root / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def test_weights_and_python_are_linked_and_counted_as_installed(fake, tmp_path, bsa_home):
    py = make_env(tmp_path / "env")
    out = install.use_local("tiny", fake, py)
    assert install.installed() == {"tiny": True} and out["installed"] == {"tiny": True}
    dest = bsa_home / "voices" / "weights" / "tiny"
    assert (dest / "model.bin").read_bytes() == FILES["model.bin"] and (dest / "model.bin").is_symlink()
    assert json.loads((dest / ".complete").read_text()) == {"repo": "o/tiny", "revision": "abc"}
    assert json.loads((dest / ".local.json").read_text())["weights_from"] == str(fake.resolve())
    assert install.local_links()["tiny"]["python_from"] == str((tmp_path / "env").resolve())


def test_the_linked_environment_runs_the_way_the_worker_does(fake, tmp_path, bsa_home):
    install.use_local("tiny", fake, make_env(tmp_path / "env"))
    py = install._engine_python(bsa_home / "voices" / "engines" / "eng")
    proc = _proc.run([str(py), "-I", "-c", "import torch, fakepkg, sys; print(torch.VERSION, fakepkg.VERSION)"],
                     capture_output=True, text=True, check=False)  # fmt: skip
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.split() == ["2.11.0+cu128", "1.0"]


def test_the_users_own_folders_are_not_changed(fake, tmp_path):
    env = tmp_path / "env"
    py = make_env(env)
    before = sorted((p.relative_to(tmp_path), p.stat().st_mtime_ns) for p in [*fake.rglob("*"), *env.rglob("*")])
    install.use_local("tiny", fake, py)
    after = sorted((p.relative_to(tmp_path), p.stat().st_mtime_ns) for p in [*fake.rglob("*"), *env.rglob("*")])
    assert before == after


def test_whisper_check_sees_it_installed(fake, tmp_path, monkeypatch):
    monkeypatch.setattr(local, "WHISPER_MODEL", "tiny")
    assert local.whisper_installed() is False
    install.use_local("tiny", fake, make_env(tmp_path / "env"))
    assert local.whisper_installed() is True


@pytest.mark.parametrize(
    "damage, words",
    [
        (lambda d: (d / "config.json").unlink(), "config.json is missing"),
        (lambda d: (d / "model.bin").write_bytes(b"short"), "model.bin is 5 bytes"),
        (lambda d: (d / "model.bin").write_bytes(b"X" * len(FILES["model.bin"])), "wrong sha256"),
    ],
)
def test_wrong_weights_are_refused_naming_the_file(fake, tmp_path, bsa_home, damage, words):
    damage(fake)
    with pytest.raises(NarrationError, match=words):
        install.use_local("tiny", fake, make_env(tmp_path / "env"))
    assert not (bsa_home / "voices" / "weights").exists()  # nothing linked


def test_a_wrong_torch_is_refused_listing_what_differs(fake, tmp_path, bsa_home):
    with pytest.raises(NarrationError, match=r"torch is 2\.10\.0, needs 2\.11\.0"):
        install.use_local("tiny", fake, make_env(tmp_path / "env", torch="2.10.0"))
    assert not (bsa_home / "voices").exists()


def test_a_missing_package_is_refused_but_other_versions_are_fine(fake, tmp_path):
    with pytest.raises(NarrationError, match="other is not installed"):
        install.use_local("tiny", fake, make_env(tmp_path / "a", other=False))
    install.use_local("tiny", fake, make_env(tmp_path / "b", fakepkg="9.9"))  # not an exact-match package


def test_a_python_outside_a_venv_is_refused(fake):
    with pytest.raises(NarrationError, match="not inside a virtual environment"):
        install.use_local("tiny", fake, Path(sys.base_prefix) / "bin" / "python3")


def test_without_python_an_installed_environment_is_needed(fake, tmp_path, bsa_home):
    with pytest.raises(NarrationError, match="--python"):
        install.use_local("tiny", fake)
    py = install._engine_python(bsa_home / "voices" / "engines" / "eng")
    py.parent.mkdir(parents=True)
    py.write_text("", encoding="utf-8")
    assert install.use_local("tiny", fake)["installed"] == {"tiny": True}


def test_an_environment_installed_by_the_plugin_is_never_replaced(fake, tmp_path, bsa_home):
    own = bsa_home / "voices" / "engines" / "eng" / ".venv"
    own.mkdir(parents=True)
    with pytest.raises(NarrationError, match="already an environment of its own"):
        install.use_local("tiny", fake, make_env(tmp_path / "env"))


def test_unknown_model_and_missing_folder(fake, tmp_path):
    with pytest.raises(NarrationError, match="unknown voice model"):
        install.use_local("nope", fake)
    with pytest.raises(NarrationError, match="not a folder"):
        install.use_local("tiny", tmp_path / "none")


def _no_file_symlinks(monkeypatch):
    """Symlinks to files fail (Windows without the right); the .venv directory link still works."""
    real = os.symlink

    def symlink(src, dst, target_is_directory=False):
        if not target_is_directory:
            raise OSError("no symlinks")
        real(src, dst, target_is_directory=True)

    monkeypatch.setattr(os, "symlink", symlink)


def test_without_symlinks_the_files_are_hard_linked(fake, tmp_path, bsa_home, monkeypatch):
    py = make_env(tmp_path / "env")
    _no_file_symlinks(monkeypatch)
    out = install.use_local("tiny", fake, py)
    target = bsa_home / "voices" / "weights" / "tiny" / "model.bin"
    assert out["linked_how"] == ["hard link"] and not target.is_symlink()
    assert target.read_bytes() == FILES["model.bin"]


def test_without_links_the_files_are_copied(fake, tmp_path, bsa_home, monkeypatch):
    py = make_env(tmp_path / "env")
    _no_file_symlinks(monkeypatch)
    monkeypatch.setattr(os, "link", lambda *a, **k: (_ for _ in ()).throw(OSError("no links")))
    out = install.use_local("tiny", fake, py)
    assert out["linked_how"] == ["copy"]
    assert (bsa_home / "voices" / "weights" / "tiny" / "model.bin").read_bytes() == FILES["model.bin"]


def test_the_cli_command_links_and_lists(fake, tmp_path, bsa_home, monkeypatch, capsys):
    import bsa_cli

    py = make_env(tmp_path / "env")
    out = bsa_cli.cmd_voice(
        type("A", (), {"action": "use-local", "args": ["tiny"], "weights": str(fake), "python": str(py)})()
    )
    assert out["installed"] == {"tiny": True}
    with pytest.raises(bsa_cli.CliError, match="usage"):
        bsa_cli.cmd_voice(type("A", (), {"action": "use-local", "args": ["tiny"], "weights": None, "python": None})())
