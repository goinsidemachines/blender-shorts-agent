"""One-line messages relayed from tools are trimmed at a sentence or clause boundary, never mid-word."""

import json
import subprocess

import pytest

from bsa import messages
from bsa.narrate import custom, local
from bsa.narrate.contract import NarrationError


def test_short_text_is_left_alone():
    assert messages.trim("All fine.", 50) == "All fine."


def test_trims_at_the_last_sentence_or_clause_boundary():
    text = "The model failed to load. CUDA ran out of memory while allocating 2.00 GiB; try a smaller batch size"
    out = messages.trim(text, 80)
    assert out == "The model failed to load. CUDA ran out of memory while allocating 2.00 GiB…"
    assert len(out) <= 81
    assert messages.trim("Error: the voice file could not be opened because it is locked", 40) == "Error: the voice file could not be…"


def test_falls_back_to_the_last_space():
    out = messages.trim("averyveryverylongword " * 10, 50)
    assert out.endswith("averyveryverylongword…") and len(out) <= 51 and "  " not in out


def test_one_line_squashes_whitespace_then_trims():
    assert messages.one_line("a\n  b\tc") == "a b c"
    assert messages.one_line("") == "failed"
    long = "First problem here. " + "word " * 200
    out = messages.one_line(long, 100)
    assert len(out) <= 101 and out.endswith("…") and not out.endswith(" …")


def test_custom_tail_never_cuts_mid_word(tmp_path):
    err = "Traceback...\n" + "RuntimeError: " + "the speaker embedding could not be found for this voice; " * 6

    def run(cmd, **kw):
        return subprocess.CompletedProcess(cmd, 1, "", err)

    with pytest.raises(NarrationError) as info:
        custom.generate("tts --lines {lines} --out {out}", [], tmp_path, run=run)
    tail = str(info.value).split(": ", 1)[1]
    assert tail.endswith("…")
    assert tail[:-1].split()[-1] in [w.strip(";.:,") for w in err.split()]  # the last word kept is a whole word


def test_local_runner_message_is_trimmed(tmp_path, monkeypatch):
    from pathlib import Path

    from bsa.narrate import install

    monkeypatch.setattr(install, "uv_path", lambda: Path("uv"))
    message = "The GPU is busy: another program is using the video memory. " * 8

    def run(cmd, **kw):
        return subprocess.CompletedProcess(cmd, 4, json.dumps({"message": message.strip()}), "")

    with pytest.raises(NarrationError) as info:
        local.generate("kokoro", [], None, 1.0, tmp_path, run=run)
    assert len(str(info.value)) <= 201 and str(info.value).endswith("…")
