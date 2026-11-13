---
name: make-short
description: Make a finished, ready-to-post narrated explainer short (1080x1920 MP4 for YouTube Shorts, Reels or TikTok) entirely in Blender, with a live review page for approvals and checks for wrong animation, missing textures and render artifacts. Use when the user asks for a short, reel or vertical explainer video made in Blender, or comes back to one they started.
---

# Make a short

You take the user from an idea to a finished vertical MP4 (captions, narration when available, sound effects) plus
a title, description and hashtags. The user approves the story, the storyboard and the render engine on one review page
(the engine with both time estimates), or in chat; after that you run to the end on your own.

## 1. Find the plugin and Blender first

**Where it runs:** this skill needs a shell and Blender on the user's computer, as in Claude Code. If you have no
tool that runs shell commands (claude.ai chat, or a Cowork session that doesn't run on the user's computer), say so
plainly once: this plugin makes shorts from Claude Code with Blender 4.5 or newer installed. Then stop; don't
pretend to build or render anything.

The **plugin root** is this skill's base directory's grandparent: Claude Code announces "Base directory for this
skill: `<root>/skills/make-short`", so the root is `<root>`. Every command below uses absolute paths, so it works
from any current folder, including paths with spaces (always quote them).

The **playbook** is `<root>/skills/make-short/playbook/` (next to this file), not `<root>/playbook/`. Every
`playbook/...` path in this skill means `<root>/skills/make-short/playbook/...`: for example
`<root>/skills/make-short/playbook/06-build.md` and `<root>/skills/make-short/playbook/fields/physics.md`.

Run `where` before anything else. Blender's Python may not be known yet, so bootstrap with any Python 3.11+:

```
python "<root>/bsa_cli.py" where          # or: py -3 ... / python3 ...
```

If no Python is on PATH, look for Blender's bundled one: on Windows
`C:\Program Files\Blender Foundation\Blender <ver>\<ver>\python\bin\python.exe`, on macOS
`/Applications/Blender.app/Contents/Resources/<ver>/python/bin/python3.11` (or similar), on Linux next to the
`blender` binary. `where` prints `blender`, `blender_python`, `settings_path` and any `problems`. If Blender is
missing or too old, tell the user plainly (Blender 4.5 or newer from blender.org) or, if they know where it is,
`settings set blender_exe "<path>"`. From then on use Blender's Python for every command:

- **CLI** = `"<blender_python>" "<root>/bsa_cli.py"`. Prints one JSON object on stdout. On failure: non-zero exit,
  one plain line on stderr (relay it in your own words).
- **BLENDER** = `"<blender>" -b --factory-startup --python-exit-code 1 --python "<root>/bsa_blender.py" --`.
  When BLENDER steps are chained (build, check, preview, relink in a row), put them in a bash script (`#!/usr/bin/env
  bash`, one `BLENDER=(...)` array or the full command on each line), not a one-line shell variable: zsh does not
  split an unquoted `$B` into words, so `$B build SHORT` fails there with "command not found". Bash arrays
  (`"${BLENDER[@]}" build SHORT`) work in both.
  Blender prints its own log lines, so read the line starting `BSA_RESULT ` (JSON). Failure: non-zero exit, a
  `BSA_RESULT` line with `"ok": false` and the `error`, and the same plain line on stderr (relay it). Don't pass the
  .blend: each command opens `blend/short.blend` itself when it needs it, after it has marked its run as starting in
  the live status. The GPU commands (`render`, the preview chain's render step, `extremes`, `test-frames`, `estimate`, `camtest`) run one at a
  time per workspace, across sessions: a second one waits for the first (`<workspace>/.gpu.lock`, `08-render.md`).
  Never delete that file by hand.
  These commands, the preview chain and `check` also run under a memory guard (`render.memory_limit_gb`, `08-render.md`):
  a Blender that grows past the limit is killed with a plain message.

Commands for starting and voicing a short (`playbook/01-interview.md`, `playbook/05-narration.md`):

- `CLI shorts`: the workspace's shorts (title, look, length, voice) and `last_finished`, the last three finished
  titles, for "Make it like an earlier one?".
- `CLI workspace rename SHORT NEW-SLUG`: renames one short's folder (its number stays) and repoints the paths stored
  inside it. Use it when the title changed and the folder still has an old word (`CLI validate` warns). It refuses
  while the page server, a render or the GPU lock is live, and when the new folder exists. A short's title always
  comes from `source/short.json`, never from its folder name.
- `CLI deliver SHORT`: copies the final video and the chosen thumbnails into `render.deliver_dir` (`08-render.md`).
- `CLI workspace move NEW`: to move the workspace, use this; never move it by hand. It refuses while the page
  server, a render or the GPU lock is live (and holds the GPU lock while it moves), when a drive is not there, or when
  NEW is a folder that isn't empty. One drive: renamed. Another drive: copied, every file and folder checked, the old
  folder kept (`old_folder`: the user deletes it after checking). It repoints the settings and the plugin's own files
  in each short (state.json, review/*.json: render statuses, engine runs, checks, camera test), so the page keeps its
  videos, and runs `BLENDER relink` on every short's .blend (`relinked`); relay `not_fixed` (each with its `fix`, e.g.
  `BLENDER relink SHORT --from OLD` for a .blend Blender couldn't open or save, or the user's own source/short.json
  naming the old folder) and `note` (the fingerprints include the files' paths, so a short in progress re-renders and
  its checks run once more; its next preview reopens checks, preview, final and post for approval).
- `BLENDER relink SHORT --from OLD [--to NEW]`: re-points the .blend's images, sounds, fonts, movie clips, texts,
  caches (point, fluid, mesh), linked files, sequencer strips, File Output nodes and the render output path under
  OLD to the same path under NEW (default: the short's workspace) and saves it only when something changed (same
  compression, no .blend1); packed and `//relative` paths are left alone, the render engine and every other setting
  untouched. `missing` lists repointed files that aren't there (the move lists each under `not_fixed`).
- `CLI new-short "<title>" --like OTHER`: starts a short from an earlier one (its number, slug or folder): copies only
  what differs from the user's settings and brand, never the story or the render engine. The result has `said`,
  `notes` and `skipped` (the interview questions to leave out).
- `CLI narrate SHORT --line ID`: re-takes one line and keeps its neighbours. `CLI narrate SHORT --line ID --take`
  re-speaks the whole paragraph holding that line (paragraph take mode only; not with `--say`).

Commands for looking at the work (`playbook/06-build.md` and `playbook/08-render.md` say when):

- `BLENDER extremes SHORT`: renders each hero at the extremes of its motion, full resolution, before the preview.
- `BLENDER test-frames SHORT --at s01:0.5 s05:0.3 [--scale 0.4]`: single frames at fractions of shots, current
  build, engine and look, reduced size, into `review/test-frames/` (takes the GPU lock).
- `BLENDER camtest SHORT [--scale 0.33] [--shots s01,s02]`: the camera path in Workbench grey into
  `review/camtest/` (`camtest.mp4`, a contact sheet, `camtest.json`); a draft of the move, never a picture. The
  engine and settings are restored and the .blend is never saved (takes the GPU lock; `playbook/06-build.md`).
- `BLENDER grab SHORT --preset preview|final --at <s>...` (or `--sheet`): frames of the edited MP4, true colour.
- `BLENDER thumbs SHORT [--hero SHOT@FRAME]... [--only N]`: renders the thumbnail candidates (both sizes) into
  `review/thumbs/`, with the checks on each (`playbook/09-post.md`). Run it with `run_in_background: true`.
- `CLI captions SHORT`: writes the caption files (`.srt`, `.vtt`, transcript) beside the final video. The final edit
  already does; this is for a manual rerun, and it refuses when the final video is stale.
- `CLI post SHORT`: writes `out/post-pack.md` (every post field and file) and lists problems. `CLI post SHORT
  --posted URL [--platform youtube|instagram|tiktok|other]` records where it went (stored only, never opened);
  `CLI post related SHORT` lists the user's other posted shorts for the "related video" line.
- `BLENDER preview SHORT`: the whole rebuild chain (build, done build, mix, check, render preview, frame-check, edit).
- `BLENDER render SHORT --preset preview|final`: only shots whose content fingerprint changed render again (and
  `BLENDER check SHORT` re-checks only those); `--shot ID --frames 120-140,200` or `--redo-flagged` redo single
  frames that frame-check flagged (each finding has a ready `redo` command).
- `CLI render-status SHORT [--preset preview|final] [--tag NAME]`: where the preview chain, a render, check, frame-check
  or edit is (the current step and its progress, time left, live warnings, and whether its Blender is still running;
  when nothing runs, how the last run ended). Its `page` is the link to the review page's Render stage, which
  shows the same live, while the page server runs; when none runs it is null and `page_hint` says how to open it
  (`CLI page SHORT --stage render`, which also switches an open page there). It never starts a server itself.

`SHORT` in commands is the short's number, slug or folder path (`3`, `pendulum`, or the full path). Always use the
short's number (or its folder path): a slug works only while no other short has the same one, and the commands
refuse an ambiguous one and list the matches. For anything you run in the background or chain yourself, use the
full folder path: another session may make a short while yours runs.

Run these commands through the **Bash** tool (Git Bash on Windows), not PowerShell: JSON arguments such as
`--info '{"engine": "cycles"}'` lose their inner quotes in Windows PowerShell 5.

## 2. The rules (never break these)

1. **Start of every short:** run `CLI where`, then show `CLI settings summary` as one line (e.g. "Kokoro (am_michael,
   1.0×) · Cycles · @handle · saving to ~/Videos/Shorts", or "your voice tool (mytts) · ..."). Run `CLI settings missing`; for anything missing ask the
   first-run questions in `playbook/00-first-run.md`: workspace (suggest `default_workspace`, then say where it is
   and how to change it), narrator ("Do you already have a voice tool, or would you like me to set one up?": their own
   command, a voice I set up, or captions only), render preference (`ask-always` if they want to be asked each time), brand or
   fresh style, hard rules (`rules`: their own content rules for every video), handle (`""` if they want none) and, with a handle, the end-card line above it (`end_card`). Then the closing card (`website`, `closing.image`, `closing.voice_line`, `closing.seconds`, then `closing_asked true`). Save each answer with
   `CLI settings set KEY VALUE` as soon as it is given, and offer to note the workspace in your memory as well (the
   plugin always reads the settings file; memory is only a convenience).
2. **The user's hard rules come first.** Read `CLI settings rules` at the start of every short and before each
   approval (story, storyboard, engine, final). Never break a hard rule, whatever the topic or a brief asks; if
   the user's request conflicts with one, say so plainly and ask (keep the rule, or they change it with `settings
   rule remove`). Rules are the user's own words: never reinterpret, widen or narrow them, and never guess what a
   broad rule means (`playbook/00-first-run.md`, "Hard rules"). Write in `short.json` `rules_check` how this short
   keeps each one; `CLI validate` refuses a short that leaves one unanswered.
3. **Follow the state.** `CLI state SHORT` gives `next`. Read that stage's playbook chapter before acting, every
   time, even if you think you remember it. Never skip an approval stage (`story`, `storyboard`, `engine`). After
   `engine` is approved, run to the end without asking, unless a check fails that you can't fix after 3 attempts:
   then show the user what fails, what you tried, and the options. If a command stops with "state.json was damaged"
   (a crash or power cut), never re-mark stages by hand: tell the user, and on their yes run `CLI state SHORT
   restore`, which puts back `state.json.bak` (the copy before the last save; the damaged file is kept), then
   check `next` and redo only the stage that the backup lacks.
4. **The user sees one review page, never Markdown or JSON.** Every stage of a short (interview, story,
   storyboard, the optional camera stage, engine, narration, render, final, post) is on one local page that updates itself as the files
   change. Talk about what is on it; never paste short.json or ask the user to open your working files.
   - **Open it once, at the interview:** `CLI page SHORT` (`playbook/01-interview.md`). Its result has `url` and
     `opened`. When `opened` is false, the page did not open (or never checked in): tell the user it didn't open
     and give the `url` as a link they can click. Never carry on as if it opened. Relay anything in `health`. (The cheap doctor checks run again each time the page is opened and after `assets download`, so the page's health banner shows the current state.)
   - **Show a stage:** when the tab is already open, `CLI page SHORT --stage STAGE` switches it there (Python can't
     bring the browser window to the front), so say so: "the page has switched to the storyboard".
   - **Wait for a yes:** after showing a stage that needs one (story, storyboard, engine), run `CLI page wait SHORT
     --stage STAGE` with `run_in_background: true` and end your turn. It returns when the user approves on the page
     (`applied: true`: the stage is marked done), sends notes (`notes`, or a `changes` decision), or after 10
     minutes (`timed_out: true`). It also ends by itself when you record the answer in chat (`page decide --by
     chat`: `applied: true`, `by: "chat"`) and when notes are sent on any other stage (`note_stages` names them).
     After a timeout, run it again in the background only while that stage still waits for a yes (`CLI state
     SHORT` still has it as `next`); otherwise drop it.
   - **A half-applied approval** (the power went off after the yes was taken but before the stage was marked done)
     is repaired by `CLI page wait SHORT --stage STAGE` (and `CLI page notes SHORT`): it marks the stage done and
     says so in `repaired`.
   - **A yes in chat** is recorded with `CLI page decide SHORT STAGE approve --by chat` (for the engine add
     `--choice cycles|eevee`); a change asked in chat with `CLI page decide SHORT STAGE changes --by chat --note
     "..."`. That is the only way to approve: `CLI state` refuses to mark story, storyboard or engine done.
   - **The engine from short.json or the `render.engine` setting** is never asked about: record it with `CLI page
     decide SHORT engine approve --by settings --choice ENGINE` (`playbook/04-engine.md`).
   - **Notes from the page:** any command's result can carry `page_notes` (ids of notes and decisions the user
     left on the page). Handle them before the next step, and again when resuming a short: `CLI page notes SHORT
     [--stage STAGE]` lists them in full. Decisions first, before you change anything: for each decision there,
     run `CLI page wait SHORT --stage <its stage>`, which applies it and returns at once. A decision on an older
     version of a stage (its content changed since the user answered) is dropped automatically (`superseded` in
     the result) and the page asks the user again for the new version. Then the notes: act only on notes whose
     `status` is `sent`; an `open` note is the user still writing, so leave it. For each sent note do what it
     asks, then `CLI page note SHORT ID done --reply "what you changed"`, or `CLI page note SHORT ID declined
     --reply "why not"`. A note on a stage that is already approved means going back: ask the user before you
     reset anything (`CLI state SHORT reset STAGE`).
   - **What a note points at:** each note has a `target` (`kind`, `id`, and for a frame `shot`) and the user's
     `text`; read both. These kinds come from the page's review tools:
     - **A frame note** (`kind` `frame`, from "Note at this moment" under a video's timeline) means "look at frame
       N" of that video. Frames count from 1 in the edited video, at 30 fps. Grab it with `BLENDER grab SHORT
       --preset final --at S`, where `S = (N - 0.5) / 30` (the middle of the frame: frame 412 is `--at 13.7167`),
       and look at the PNG it writes (its result names the file) before you change anything. On Final the `id` is
       `N`. A Render note's id is `<run key>:<N>`, and the run key names the video: `preview`, `final`, or
       `<preset>-<tag>` for a side run. Add `--preset <preset>` and, for a side run, `--tag <tag>`: `preview:412`
       is `BLENDER grab SHORT --preset preview --at 13.7167`, `preview-eevee:412` is `BLENDER grab SHORT --preset
       preview --tag eevee --at 13.7167`. Its `shot` says which shot the frame is in (null on the closing card):
       fix that shot, then rebuild as usual (`BLENDER preview SHORT`, and the final again when it was made).
       When the page says "This video was cut from an older timing", its markers are hidden and frame numbers
       may not match the current timing: run the edit again first (`playbook/08-render.md`).
     - **A `look` note** (`kind` `look`, from the "Look" section on the Storyboard stage; its `id` is `style` or
       `set`, and its `text` is "Use the toon style", "Use the studio set" or "Use no set") asks to see the storyboard
       in another style or set. Run `BLENDER look SHORT --style NAME` (or `--set NAME`, `none` for no set) with
       `run_in_background: true`; look at the new frames yourself, answer the note (`CLI page note SHORT ID done
       --reply "..."`), and show the page again. The engine is always the user's choice, never yours: `look` and
       `style-sample` need it (`--engine eevee|cycles`, else the short's approved engine or `render.engine`), and when
       `render.engine` is `ask-always` ask the user which to use; never pick or suggest an engine for a style. Once the
       user agrees with the new look, save it: the workspace's first brand short, `settings set brand.style NAME` (or
       `brand.set NAME`) for every short; a later one, ask whether it is for every short or only this one (the short's
       own `style`: `{"style": {"style": "NAME"}}` or `{"style": {"set": "NAME"}}`); then rebuild
       (`playbook/look-and-brand.md` "Look preview"). `blueprint` and `pencil` draw colour only on `key=[...]`
       parts, so keep them in build.py's `styles.apply` call.
     - **A `thumbnail` note** (`kind` `thumbnail`, from "+ Note on candidate N" on the Post stage; its `id` is the
       candidate's number) asks for a change to that thumbnail. Change `post.thumbnail` (the text, or that
       candidate's entry in `heroes`), then `BLENDER thumbs SHORT --only N` and answer the note. A frame note whose
       text is "Use this frame for the thumbnail" (the button on a frame of the Final timeline) means: make that
       frame a thumbnail hero. Its `shot` and `id` give `<shot>@<frame>`; see `playbook/09-post.md`.
     - **"Re-take this line"** (a `line` note on Narration, from the voice check; its `id` is the line's id): run
       `CLI narrate SHORT --line ID` with `run_in_background: true`. It throws away only that line's take and
       speaks it again with the next seed; every other take is kept. When the voice misread a word (the voice
       check shows what Whisper heard against the line), add `--say "..."`: the words spelled the way the voice
       should say them, saved as the line's `say` (the caption still shows `text`). The closing line can't be
       respelled this way: change the `closing.voice_line` setting instead. The new take has another length, so
       the timing changes: once the short has been built, run `BLENDER preview SHORT` in the background (it
       reruns the timeline, the mix, the render of the frames that changed, the frame-check and the edit as
       needed), and, when the final was made, `BLENDER render SHORT --preset final`, `BLENDER frame-check SHORT
       --preset final` and `BLENDER edit SHORT --preset final` again (`playbook/08-render.md`).
       Then answer the note: `CLI page note SHORT ID done --reply "re-took l02 ..."`. If `narrate` refuses (no
       narration yet, captions only, an unknown line), relay its message in the reply and decline the note.
     - **"Needs a source"** (a `line` note from the fact panel, "Facts behind every line", on Story or Final): the
       line states a number that no fact backs. Either add a fact to `facts` in short.json with a real source
       (`playbook/honesty.md`: never invent a source) and cite it from the line, or reword the line without the
       number. Then `CLI validate SHORT`, and answer the note (`CLI page note SHORT ID done --reply "..."`). A
       number in a shot's label needs a fact cited by a line of the same shot. Once the story is approved, a
       reworded line changes it: ask the user first. Once narrated, run `CLI narrate SHORT` in the background
       (only the changed line's take is remade) and rebuild as for a re-take.
     - **A `finding` note** (from "+ Note on finding" on Final, or on a Render run) is about one finding of the
       checks. `CLI page notes SHORT` gives the note its `"finding"` (check, severity, message, where, redo): act on
       that row, using its `redo` when it has one (it renders the flagged frames again), else the shot in `where`;
       fix what it says, then rebuild as usual. `"gone": true` means the checks no longer report it: reply that it
       is gone (`done`) after a quick look.
   - **Before and after:** Story, Storyboard and Final keep the last 3 versions the user saw, and the page offers
     "Compare with version N" (a word diff of the lines, the frames side by side, or the two videos). Nothing to
     run for it: a note left while comparing is about the version shown now unless its text says otherwise.
   - **Final is not a gate:** its "Looks good" is the user's acknowledgement; never wait for it. It shows up in
     `CLI page notes SHORT` as an unapplied `final` decision: apply it with `CLI page wait SHORT --stage final`.
   - **The feedback panel** shows on the Post stage only after the user said yes to seeing the plugin's notes
     (`CLI feedback report SHORT`, `playbook/09-post.md`).
5. **Narration never blocks** (`playbook/05-narration.md`). Any narrator problem means captions only, said once, and
   the short goes on. **Never download** anything (voices, assets) without first showing the plan
   (`CLI voice plan ...`, `CLI assets plan ...`: name, source, licence, size) and getting a clear yes.
6. **Honesty** (`playbook/honesty.md`): every number on screen or in the narration has a sourced fact; any
   exaggerated or simplified motion carries an on-screen badge; myths are cut even when they look good.
7. **No music, ever.** Sound effects only (`playbook/sound.md`). **No plugin-author (goinsidemachines) branding**
   in the user's video unless the user is that brand or asks for it: by default only the user's own handle and site
   (their end card, `playbook/look-and-brand.md`), or nothing. **Never upload or post** anything: you prepare the post text and
   the user uploads.
8. **MCP or script mode:** if the Blender MCP is connected (a tool like `execute_blender_code` is available), ask
   "MCP or script mode?" once per short and remember the answer (`playbook/mcp-mode.md`; MCP builds always go
   through `bsa_mcp_build.py`, which reloads the plugin's code first). If it isn't connected,
   use script mode without asking.
9. **Resuming:** when the user comes back to a short ("carry on with the watch short"), find it
   (`CLI state SHORT`), say in one line where it stands, handle any `page_notes` in its result (`CLI page notes
   SHORT`, as in rule 4: decisions first, then sent notes), and continue from `next`. Run `CLI page SHORT` again
   so the user has the page (the same `opened` rule). When `next` is in the `waiting_for_yes` list of `CLI page
   notes SHORT` (an approval stage the page already shows and asks a yes for), don't redo its chapter: run `CLI
   page wait SHORT --stage <next>` in the background again and end your turn. Renders resume from the frames
   already on disk.
10. **Long commands run in the background.** The Bash tool stops a command after 2 minutes by default (10 at
   most), which kills a long render. Run these with `run_in_background: true` and carry on when you are notified
   that they finished, never with a timeout: `BLENDER render SHORT --preset preview|final`, `BLENDER estimate
   SHORT`, `CLI voice install MODEL...`, `CLI narrate SHORT` and `CLI voice test-custom` (a local voice or the user's own tool can take many
   minutes),
   `BLENDER frame-check SHORT --preset final`, `BLENDER preview SHORT`, `BLENDER extremes SHORT`, and `BLENDER check
   SHORT` on a long short. If one was cut off
   anyway, run the same command again: renders resume from the frames on disk and narration keeps finished takes.
11. **Note problems in the plugin itself.** When the plugin gets something wrong (a check that is wrong, a helper
   that misbehaves, a misleading doc or message, a crash), note it with `CLI feedback add SHORT --severity
   blocking|wrong-output|rough-edge --summary "..."` plus `--happened`, `--expected`, `--command`, `--error` and
   `--workaround` where you have them (and `--blender`, `--mode script|mcp` if known). Only the plugin's own
   problems, never the user's own scene or your own mistakes. Note it and carry on: never stop the short for it,
   and don't mention it until the post stage (`playbook/09-post.md`). With the setting `feedback off` it notes
   nothing. Nothing is ever sent by the plugin.

## 3. Stages, chapters and who marks them done

| Stage | Read | Done when | Marked by |
|---|---|---|---|
| (first run) | `playbook/00-first-run.md` | settings saved | `settings set` |
| `interview` | `playbook/01-interview.md` | topic, audience, length agreed | you: `CLI state SHORT done interview` |
| `story` | `playbook/02-story.md` | user approves the story on the page or in chat | `CLI page wait SHORT --stage story` (a page yes) or you: `CLI page decide SHORT story approve --by chat` |
| `storyboard` | `playbook/03-storyboard.md` | user approves the storyboard on the page or in chat | `CLI page wait SHORT --stage storyboard` or you: `CLI page decide SHORT storyboard approve --by chat` |
| `engine` | `playbook/04-engine.md` | user says yes to an engine (or it comes from short.json or the setting) | `CLI page wait SHORT --stage engine`, or you: `CLI page decide SHORT engine approve --by chat --choice cycles` (`--by settings` when it comes from short.json or the setting) |
| `narration` | `playbook/05-narration.md` | narration made, or captions only | `CLI narrate SHORT` marks it itself |
| `build` | `playbook/06-build.md` | full scene rebuilt to the final timing, mix written | you: `CLI state SHORT done build` |
| `checks` | `playbook/07-checks.md` | no error findings | `BLENDER check SHORT` marks it only when there are no errors |
| `preview` | `playbook/08-render.md` | preview MP4 made and reviewed by you | `BLENDER edit SHORT --preset preview` |
| `final` | `playbook/08-render.md` | final MP4 made | `BLENDER edit SHORT --preset final` |
| `post` | `playbook/09-post.md` | post text written into short.json | you: `CLI state SHORT done post` |

After any change once the build stage is reached (build.py, short.json, a camera tweak), run `BLENDER preview SHORT`
(in the background): it runs the timeline if the timing changed, the build in script mode if needed, `state done
build`, `mix`, `check`, the preview render, `frame-check` and `edit`, skipping what is current and stopping at the
first failure ("stopped at <step>: ..."). In MCP mode rebuild through the MCP, then `BLENDER preview SHORT
--no-build`. Before the first preview, `BLENDER extremes SHORT` renders each hero's moving parts at full size at the
ends of their motion into `review/extremes/`; after any edit, `BLENDER grab SHORT --preset preview|final --at
SECONDS... | --sheet` pulls frames of the edited video at its own colours into `review/grabs/`. Look at both
(`playbook/08-render.md`).

Going back: `CLI state SHORT reset STAGE` clears that stage and every later one (for example, the user changes the
story after the storyboard: `reset story`, revise, show the story on the page again and wait for a new yes).

Always-relevant chapters: `playbook/craft-short-form.md` (hook, pacing, captions), `playbook/honesty.md`,
`playbook/look-and-brand.md`, `playbook/sound.md`, `playbook/assets.md`, `playbook/mcp-mode.md`, and the field
chapter for the topic (`playbook/fields/physics.md` for mechanisms, structures, forces and motion).

## 4. A short's folder

```
<workspace>/03-watch-escapement/
  state.json            stages done (CLI state)
  source/short.json     the spec: facts, lines, shots, camera (one-take: each shot's `at`), sfx, assets, post (you
                        write it; the user never reads it)
  source/timeline.json  frames per shot, line and caption card (CLI timeline / narrate write it)
  source/build.py       builds every scene into blend/short.blend (you write it)
  source/anchors.json   where each near label's object is on screen (BLENDER render / anchors write it)
  source/model.py       your own model or simulation when no field helper fits
  source/sound.py       optional extra sound bed built from simulation data
  source/styles.json    style options for the story page (no-brand users)
  source/notes.md       your own working notes (facts checked, decisions)
  review/               notes, decisions, live render status (the page reads them; short.html only without a server)
  stills/  audio/vo/  audio/mix.wav  blend/  frames/preview|final/<shot>/  out/*.mp4
```

The workspace has its own `.gitignore` (`*`). Never write a user's short into the plugin folder.
