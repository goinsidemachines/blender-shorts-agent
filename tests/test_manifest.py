import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_plugin_manifest():
    data = json.loads((ROOT / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
    assert data["name"] == "blender-shorts-agent"
    assert data["version"]
    assert data["description"]
    assert data["license"] == "MIT"


def test_marketplace_lists_plugin():
    data = json.loads((ROOT / ".claude-plugin" / "marketplace.json").read_text(encoding="utf-8"))
    assert len(data["plugins"]) == 1
    plugin = data["plugins"][0]
    assert plugin["name"] == "blender-shorts-agent"
    assert plugin["source"] == "./"


def test_skill_frontmatter():
    text = (ROOT / "skills" / "make-short" / "SKILL.md").read_text(encoding="utf-8")
    match = re.match(r"---\r?\n(.*?)\r?\n---", text, re.DOTALL)
    assert match, "SKILL.md must start with frontmatter"
    fields = {}
    for line in match.group(1).splitlines():
        key, _, value = line.partition(":")
        fields[key.strip()] = value.strip().strip("\"'")
    assert fields["name"] == "make-short"
    assert "short" in fields["description"].lower()
