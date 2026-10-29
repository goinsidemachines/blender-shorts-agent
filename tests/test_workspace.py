import json

from bsa import workspace


def test_new_short_layout(tmp_path):
    root = workspace.ensure_workspace(tmp_path / "shorts")
    a = workspace.new_short(root, "The 660 tonne ball")
    assert a.name == "01-the-660-tonne-ball"
    for d in workspace.SHORT_DIRS:
        assert (a / d).is_dir()
    b = workspace.new_short(root, "Second one")
    assert b.name == "02-second-one"


def test_workspace_gitignore(tmp_path):
    root = workspace.ensure_workspace(tmp_path / "w")
    assert (root / ".gitignore").read_text(encoding="utf-8").strip() == "*"


def test_paths_with_spaces_and_unicode(tmp_path):
    from bsa import jobstate

    root = workspace.ensure_workspace(tmp_path / "My Shorts é")
    assert workspace.slugify("Café's 3 gears") == "cafes-3-gears"
    short = workspace.new_short(root, "Café's 3 gears")
    assert short.name == "01-cafes-3-gears"
    jobstate.mark_done(short, "interview", topic="café")
    assert jobstate.load_state(short)["stages"]["interview"]["topic"] == "café"


def test_find_short(tmp_path):
    root = workspace.ensure_workspace(tmp_path / "w")
    a = workspace.new_short(root, "Alpha")
    b = workspace.new_short(root, "Beta")
    assert workspace.find_short(root, "01") == a
    assert workspace.find_short(root, "1") == a
    assert workspace.find_short(root, "beta") == b
    assert workspace.find_short(root, "02-beta") == b
    try:
        workspace.find_short(root, "nope")
    except FileNotFoundError:
        pass
    else:
        raise AssertionError("expected FileNotFoundError")


def test_default_workspace(tmp_path, monkeypatch):
    clone = tmp_path / "clone"
    (clone / ".claude-plugin").mkdir(parents=True)
    (clone / ".claude-plugin" / "plugin.json").write_text(json.dumps({"name": "blender-shorts-agent"}))
    assert workspace.default_workspace(clone) == clone / "shorts"

    other = tmp_path / "other"
    (other / ".claude-plugin").mkdir(parents=True)
    (other / ".claude-plugin" / "plugin.json").write_text(json.dumps({"name": "something-else"}))
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr("pathlib.Path.home", lambda: home)
    assert workspace.default_workspace(other) == home / "Shorts"
    (home / "Videos").mkdir()
    assert workspace.default_workspace(other) == home / "Videos" / "Shorts"


def test_resolve_short_by_path_slug_or_number(tmp_path):
    import pytest

    root = tmp_path / "ws"
    short = workspace.new_short(root, "Pendulum")
    assert workspace.resolve_short(str(short), None) == short.resolve()
    assert workspace.resolve_short("pendulum", root) == short
    assert workspace.resolve_short("1", root) == short
    with pytest.raises(FileNotFoundError, match="no workspace"):
        workspace.resolve_short("pendulum", None)


def test_ambiguous_slug_is_refused_with_the_matches(tmp_path):
    import pytest

    root = workspace.ensure_workspace(tmp_path / "w")
    first = workspace.new_short(root, "Pendulum")
    workspace.new_short(root, "Gears")
    second = workspace.new_short(root, "Pendulum")
    with pytest.raises(ValueError, match="matches 2 shorts") as err:
        workspace.find_short(root, "pendulum")
    assert first.name in str(err.value) and second.name in str(err.value) and "number" in str(err.value)
    assert workspace.find_short(root, "3") == second  # the number, folder name or path still resolve uniquely
    assert workspace.find_short(root, "01-pendulum") == first
    assert workspace.resolve_short(str(second), root) == second.resolve()


def test_new_short_skips_a_number_another_session_claimed(tmp_path):
    root = tmp_path / "ws"
    workspace.new_short(root, "First")
    (root / ".numbers" / "02").mkdir(parents=True)  # another session is making short 02 right now
    assert workspace.new_short(root, "Second").name == "03-second"


def test_concurrent_new_shorts_get_different_numbers(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    root = tmp_path / "ws"
    workspace.new_short(root, "First")
    with ThreadPoolExecutor(8) as pool:
        made = list(pool.map(lambda i: workspace.new_short(root, f"Short {i}"), range(8)))
    numbers = [p.name.split("-", 1)[0] for p in made]
    assert len(set(numbers)) == 8 and "01" not in numbers


def test_new_short_with_a_chosen_number(tmp_path):
    import pytest

    root = tmp_path / "ws"
    workspace.new_short(root, "First")
    workspace.new_short(root, "Second")
    made = workspace.new_short(root, "The lever", number=7)
    assert made.name == "07-the-lever"
    assert workspace.new_short(root, "Next").name == "08-next"  # automatic numbers carry on after it
    with pytest.raises(ValueError, match="02") as err:
        workspace.new_short(root, "The lever", number=2)  # 02-second has it
    assert "02-second" in str(err.value)
    with pytest.raises(ValueError, match="must be 1 or more"):
        workspace.new_short(root, "Zero", number=0)


def test_chosen_number_claimed_by_another_session_is_refused(tmp_path):
    import pytest

    root = tmp_path / "ws"
    workspace.new_short(root, "First")
    (root / ".numbers" / "04").mkdir(parents=True)  # another session is making short 04 right now
    with pytest.raises(ValueError, match="reserved") as err:
        workspace.new_short(root, "Mine", number=4)
    assert ".numbers" in str(err.value)
    assert not any(p.name.startswith("04-") for p in root.iterdir())
