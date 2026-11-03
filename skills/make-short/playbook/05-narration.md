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
   - with `narrator.take_mode paragraph`, speaks a few lines per take and cuts each take into its lines (Whisper's
     word timings place the cuts when it is installed); `narrator.lead_in` pads each take's start;
   - re-times the short from the takes and rewrites `source/timeline.json` (returned as `timeline`). A take that
     opens with silence (some voices leave a lead-in) keeps its place in the mix, but its caption cards start when
     the voice does: the timing measures each take's leading silence, so captions never show before the word;
   - with the user's own tool, saves what the tool printed (stdout and stderr) to `audio/vo/narrator.log` and lists
     its lines that mention a fallback, a warning or an error under `voice_log_notes` (the log's path is
     `voice_log`).
3. Read the result:
   - `status: ok`: carry on. If `message` names lines that may not be spoken clearly, listen to them if you can,
     or reword the line in `short.json` and run `narrate` again (only that take is remade). If `voice_log_notes`
     has lines (the tool fell back to another mode, or warned), tell the user in one line and check the takes it
     names; the full output is in `audio/vo/narrator.log`.
   - `status: captions-only` with a `message`: say it **once**, in plain words ("The Kokoro voice didn't work (the
     engine isn't installed), so this short uses captions only. You can add a voice later."), then carry on. Do not
     retry in a loop, do not ask the user to fix it now.
   - `status: captions-only` without a message: the user chose captions only. Say nothing.

## The voice check on the page

Every narrate run records what Whisper heard in each take in `audio/vo/heard.json` (per line: the words asked for,
what was heard, and the word error rate), or, when the takes weren't checked, `checked: false` with `why_not`.
Every voice is checked the same way, ElevenLabs takes too, and the user's own tool. The page's Narration stage shows
it as a table, one row per line: the words heard with the misheard ones marked, and whether the take passed. A
take that is being made again shows "Waiting for a new take" until the next check.

- **Re-taking one line.** The user asks for it with "Re-take this line" on that row (a note on the line), or in
  chat. Run `CLI narrate SHORT --line ID` with `run_in_background: true`: only that take is thrown away and spoken
  again, with the next seed, so it reads differently; the other takes are kept. The result has `retake` (`line`,
  `seed`). When the voice misread a word, add `--say "..."` with the words spelled the way the voice should say
  them (`CLI narrate SHORT --line l02 --say "the ess-capement turns"`): it is saved as the line's
  `say`, and the caption still shows `text`. `--say` needs `--line`, and the closing line's words are the
  `closing.voice_line` setting, not a respelling. The new take has another length, so the timing changes: once
  the short has been built, run `BLENDER preview SHORT` in the background (it reruns the timeline, the mix, the
  render of the frames that changed, the frame-check and the edit as needed) and, when the final was made, the
  final render, frame-check and edit again (`08-render.md`). The closing line has its own row once it has been
  narrated, with the same "Re-take this line" (`CLI narrate SHORT --line closing`).
  Then answer the note (`CLI page note SHORT ID done --reply "..."`).
- **Whisper missing.** When `why_not` says Whisper isn't installed, the page shows "Whisper isn't installed, so
  the takes weren't checked. CLI voice plan whisper-small.en shows what it downloads." Offer it once, at a calm
  moment (never mid-render): run `CLI voice plan whisper-small.en`, show its plan (name, source, licence, size)
  and, only on a clear yes, `CLI voice install whisper-small.en` with `run_in_background: true`. The next
  `narrate` checks the takes. Narration never waits for it.
- Any other `why_not` (the check is turned off, captions only, or the file can't be read) is shown on the page as
  it is; say it in one line if the user asks.

Captions-only shorts are paced for reading time (about 2.6 words a second), not stretched to imaginary narration.

## After narration: the timing changed

The takes set the real line lengths, so shot lengths change. `source/build.py` reads its frame ranges from
`timeline.json`, so the build stage rebuilds everything to the new timing. Check `length_drift` in the returned
timeline: more than about 15% off the agreed length means the script is too long or too short; tighten lines (a
voice that runs long is better fixed by cutting words than by a faster speed), then `narrate` again.

## Takes: one line or one paragraph

By default each line is spoken on its own (`narrator.take_mode` is `line`). Set `narrator.take_mode` to
`paragraph` (`CLI settings set narrator.take_mode paragraph`) and the voice speaks a few lines at a time, then the
plugin cuts each take back into one WAV per line. The lines flow into each other like one read. Every voice works
this way, the user's own tool too (it needs `{take}` in its command, see below). A line the cut can't find, a
retry and the closing line are spoken alone, like before.

`narrator.lead_in` is the quiet before the voice in each take, from 0 to 1 second (default 0). The captions start
with the voice, not with the quiet.

A short can have its own `narrator` in `short.json` (`voice`, `speed`, `take_mode`, `lead_in`) that wins over the
settings for that short only. With neither set, narration is exactly as it was. `voice_for` names the narrator
its `voice` and `speed` were chosen for (`new-short --like` records it): when the settings' narrator is a different
one now, the short's own voice and speed are not used, the settings' are, and `narrate` says so in `notes`. A short
with its own `voice` but no `voice_for` (made before it was recorded) keeps using its voice.

The result says where each line's audio came from in `sources`: `take`, `line` (spoken alone) or `fallback` (its take
failed or couldn't be cut there, so it was spoken alone), and `fallbacks` gives the reason for each fallback line.
Without Whisper the cuts use character timing, and a line it can't cut safely is spoken alone and marked
`fallback`. The voice check on the
page shows the same on each row. Say a fallback in one line and check that line. The voice's own notes (for
example, that `{take}` is missing from the command) show on the Narration stage of the page too.

- `CLI narrate SHORT --line ID` re-takes one line and keeps its neighbours. In a paragraph take, only that line is
  spoken again.
- `CLI narrate SHORT --line ID --take` re-speaks the whole paragraph that holds the line, with the next seed, and
  cuts it again. It needs paragraph mode and does not go with `--say`.

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

### Paragraph takes with their own tool

For `narrator.take_mode paragraph` the command must contain `{take}`. Without it the lines are spoken one by one and
the result says so (take mode needs `{take}` in the voice command). The contract:

- For a paragraph take, `{take}` is the take's id (`t01`, `t02`, ...) and the `{lines}` file holds one item:
  `{"id": "t01", "text": <the take's lines joined with spaces>, "lines": [line ids], "texts": [each line's text]}`.
  The tool speaks `text` in one go (it may use `lines` and `texts`, for example to put a pause between lines; that is
  optional) and writes `<out>/t01.wav`, where `{out}` is the takes folder. The plugin cuts the take back into one WAV
  per line; the tool never cuts.
- For a line spoken on its own (line mode, a line the take couldn't be cut at, a retry, the closing line), `{take}`
  is empty (`""`) and the `{lines}` file holds the lines as usual (`{"id", "text"}`); the tool writes
  `<out>/<line id>.wav`. The tool must accept an empty `{take}` argument.
- Take ids are reused: `t01` is always a short's first paragraph, whatever its words are now. A tool must not cache
  its output by id, and should not skip a take whose file already exists.

A command without `{take}` is always run line by line, also in paragraph mode.

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
