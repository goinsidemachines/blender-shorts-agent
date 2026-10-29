import sys
from pathlib import Path

# The runner is its own uv project; make `bsa_tts` importable when the plugin's dev environment runs these tests.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
