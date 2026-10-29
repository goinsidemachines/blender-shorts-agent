# 05 Narration (never blocks)

Narration is made only now, after the storyboard is approved, never before. Whatever happens here, the short gets
finished.

## Run it

0. If `settings` says `custom` (the user's own voice tool), nothing needs installing: the saved command is run
   (see "Their own voice tool" below).
1. If `settings` says a local GPU voice (`chatterbox`, `chatterbox-turbo`), run `CLI machine --need-gb 4` first and
   pass on any `vram_warning` (the runner falls back to the CPU on its own, which is slower).
2. `CLI narrate SHORT`, with `run_in_background: true` (a local voice on the CPU can take many minutes, longer than
   the Bash tool's 2-minute default). It refuses to run before the storyboard is approved. It:
   - deletes takes made by another narrator, voice or speed, or for a line whose text changed since; every voice
     resumes: ElevenLabs, the local voices (Kokoro, Chatterbox) and the user's own tool make only the takes that are missing, empty or
     bad, so an interrupted run carries on where it stopped (running it again is safe);
   - speaks each line's `say` when it has one, else its `text` (the captions always show `text`; a changed `say`
     remakes that take);
   - makes one WAV per line in `audio/vo/`, checks each against the spoken words with Whisper when that is installed and
     regenerates a take that doesn't match (alone, with seeds 8 then 9; for the user's own tool the command is
     run again with a lines file holding only those lines);
   - marks the `narration` stage itself (`status` `ok` or `captions-only`);
   - re-times the short from the takes and rewrites `source/timeline.json` (returned as `timeline`).
3. Read the result:
   - `status: ok`: carry on. If `message` names lines that may not be spoken clearly, listen to them if you can,
     or reword the line in `short.json` and run `narrate` again (only that take is remade).
   - `status: captions-only` with a `message`: say it **once**, in plain words ("The Kokoro voice didn't work (the
     engine isn't installed), so this short uses captions only. You can add a voice later."), then carry on. Do not
     retry in a loop, do not ask the user to fix it now.
   - `status: captions-only` without a message: the user chose captions only. Say nothing.

Captions-only shorts are paced for reading time (about 2.6 words a second), not stretched to imaginary narration.

## After narration: the timing changed

The takes set the real line lengths, so shot lengths change. `source/build.py` reads its frame ranges from
`timeline.json`, so the build stage rebuilds everything to the new timing. Check `length_drift` in the returned
timeline: more than about 15% off the agreed length means the script is too long or too short; tighten lines (a
voice that runs long is better fixed by cutting words than by a faster speed), then `narrate` again.

## Voice choices

- **ElevenLabs** needs `ELEVENLABS_API_KEY` in the environment and `narrator.voice` set to a voice id. The key
  never goes into chat, files or commands you show.
- **Kokoro, Chatterbox, Chatterbox Turbo** need `CLI voice install MODEL` first (several GB: run it with
  `run_in_background: true`), and only after the user said yes to `CLI voice plan MODEL`. If a voice is not installed when narration runs, the result is captions only with a
  message: that is fine. Offer, once, to set it up after the short is finished.

## Their own voice tool (`custom`)

`narrator.provider custom` runs the saved `narrator.command` (placeholders `{lines}`, `{out}`, `{seed}`, optional
`{voice}`, `{speed}`; see `playbook/00-first-run.md`) without a shell: once for all missing lines, and again for a
single line when Whisper finds it unclear. The run has no time limit, so run `CLI narrate SHORT` in the background
as always. Each WAV the tool writes is converted to 48 kHz mono 16-bit in place; line lengths are measured from the
WAVs, so the tool needs no `durations.json`. Any failure (command not found, non-zero exit with its last error
line, a line without a WAV, a non-WAV file) gives captions only, said once. Changing `narrator.command` counts as a
different narrator: the old takes are removed on the next `narrate`.

Test a new command with `CLI voice test-custom` (it touches no short) before relying on it.

### Adapters

If the user's tool does not fit the placeholders (it speaks one sentence per call, needs an API key, writes MP3,
wants different arguments), write a small Python adapter at `<bsa_home>/voices/adapters/<name>.py` (`<bsa_home>` is
the folder that holds the settings file; `CLI where` prints `settings_path`) and make the command call it with a
Python the user has, for example:

```
python "<bsa_home>/voices/adapters/mytts.py" --lines {lines} --out {out} --seed {seed}
```

Example for a tool that speaks one sentence per call (`mytts "text" file.wav`):

```python
import argparse, json, pathlib, subprocess

p = argparse.ArgumentParser()
p.add_argument("--lines", required=True)
p.add_argument("--out", required=True)
p.add_argument("--seed", default="7")
args = p.parse_args()
for item in json.loads(pathlib.Path(args.lines).read_text(encoding="utf-8")):
    target = pathlib.Path(args.out) / (item["id"] + ".wav")
    subprocess.run(["mytts", item["text"], str(target)], check=True)
```

An adapter for a paid service reads the key from an environment variable (never from the file, never printed). If
the service returns MP3, the adapter converts it to WAV (for example with `ffmpeg`, if the user has it) before
finishing. Test with `CLI voice test-custom`, let the user listen, then save.

## The user's own voice

Chatterbox and Chatterbox Turbo can speak in the user's own voice: they record 10 to 20 seconds of calm narration
in the style they want back (a quiet room, no music, one continuous take), save it as a mono WAV named `me.wav` in
`voices/refs/` inside the folder that holds the settings file (`~/.blender-shorts-agent/voices/refs/me.wav`, see
`settings_path` from `CLI where`; the installed voices README has recording tips), and choose voice `me`
(`CLI settings set narrator.voice me`). Test it with `CLI voice sample chatterbox me` first. Only offer this for
their own voice, never someone else's.

## Adding a voice later

The user finished a short with captions only and now wants a voice (or a different one):

1. Set the narrator: `CLI settings set narrator.provider kokoro`, `narrator.voice ...` (install with the plan first
   if needed).
2. `CLI state SHORT reset narration`. That clears narration and every stage after it; story, storyboard and engine
   stay approved.
3. Run again from narration: `narrate`, then the build stage (rebuild to the new timing, mix), checks, preview,
   final and post, exactly as the first time. The story is not redone.
