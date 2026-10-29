---
name: make-short
description: Make a finished, ready-to-post narrated explainer short (1080x1920 MP4 for YouTube Shorts, Reels or TikTok) entirely in Blender, with visual approval pages and checks for wrong animation, missing textures and render artifacts. Use when the user asks for a short, reel or vertical explainer video made in Blender, or comes back to one they started.
---

# Make a short

You take the user from an idea to a finished vertical MP4 (captions, narration when available, sound effects) plus
a title, description and hashtags. The user approves the story and the storyboard on HTML pages and the render
engine in chat (with both time estimates); after that you run to the end on your own.

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
  Blender prints its own log lines, so read the line starting `BSA_RESULT ` (JSON). Failure: non-zero exit, a
  `BSA_RESULT` line with `"ok": false` and the `error`, and the same plain line on stderr (relay it). Don't pass the
  .blend: each command opens `blend/short.blend` itself when it needs it, after it has marked its run as starting in
  the live status. The GPU commands (`render`, the preview chain's render step, `extremes`, `estimate`) run one at a
  time per workspace, across sessions: a second one waits for the first (`<workspace>/.gpu.lock`, `08-render.md`).
  Never delete that file by hand.

Commands for looking at the work (`playbook/06-build.md` and `playbook/08-render.md` say when):

- `BLENDER extremes SHORT`: renders each hero at the extremes of its motion, full resolution, before the preview.
- `BLENDER grab SHORT --preset preview|final --at <s>...` (or `--sheet`): frames of the edited MP4, true colour.
- `BLENDER preview SHORT`: the whole rebuild chain (build, done build, mix, check, render preview, frame-check, edit).
- `BLENDER render SHORT --preset preview|final`: only shots whose content fingerprint changed render again (and
  `BLENDER check SHORT` re-checks only those); `--shot ID --frames 120-140,200` or `--redo-flagged` redo single
  frames that frame-check flagged (each finding has a ready `redo` command).
- `CLI render-status SHORT [--preset preview|final] [--open]`: where the preview chain, a render, check, frame-check
  or edit is (the current step and its progress, time left, live warnings, and whether its Blender is still running;
  when nothing runs, how the last run ended); `--open` shows the live page `review/render-<preset>.html`.

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
   fresh style, hard rules (`rules`: their own content rules for every video), handle (`""` if they want none) and, with a handle, the end-card line above it (`end_card`). Save each answer with
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
   then show the user what fails, what you tried, and the options.
4. **The user sees HTML pages, never Markdown or JSON.** At the story and storyboard approvals (and for the finished short), write the page with
   `CLI review story|storyboard|final SHORT --open` and talk about what is on it. Never paste short.json or ask the
   user to open your working files.
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
   (`CLI state SHORT`), say in one line where it stands, and continue from `next`. Renders resume from the frames
   already on disk.
10. **Long commands run in the background.** The Bash tool stops a command after 2 minutes by default (10 at
   most), which kills a long render. Run these with `run_in_background: true` and carry on when you are notified
   that they finished, never with a timeout: `BLENDER render SHORT --preset preview|final`, `BLENDER estimate
   SHORT`, `CLI voice install MODEL...`, `CLI narrate SHORT` and `CLI voice test-custom` (a local voice or the user's own tool can take many
   minutes),
   `BLENDER frame-check SHORT --preset final`, `BLENDER preview SHORT`, `BLENDER extremes SHORT`, and `BLENDER check
   SHORT` on a long short. If one was cut off
   anyway, run the same command again: renders resume from the frames on disk and narration keeps finished takes.

## 3. Stages, chapters and who marks them done

| Stage | Read | Done when | Marked by |
|---|---|---|---|
| (first run) | `playbook/00-first-run.md` | settings saved | `settings set` |
| `interview` | `playbook/01-interview.md` | topic, audience, length agreed | you: `CLI state SHORT done interview` |
| `story` | `playbook/02-story.md` | user approves the story page | you: `CLI state SHORT done story` |
| `storyboard` | `playbook/03-storyboard.md` | user approves the storyboard page | you: `CLI state SHORT done storyboard` |
| `engine` | `playbook/04-engine.md` | user says yes to an engine | you: `CLI state SHORT done engine --info '{"engine": "cycles"}'` |
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
story after the storyboard: `reset story`, revise, show the story page again).

Always-relevant chapters: `playbook/craft-short-form.md` (hook, pacing, captions), `playbook/honesty.md`,
`playbook/look-and-brand.md`, `playbook/sound.md`, `playbook/assets.md`, `playbook/mcp-mode.md`, and the field
chapter for the topic (`playbook/fields/physics.md` for mechanisms, structures, forces and motion).

## 4. A short's folder

```
<workspace>/03-watch-escapement/
  state.json            stages done (CLI state)
  source/short.json     the spec: facts, lines, shots, sfx, assets, post (you write it; the user never reads it)
  source/timeline.json  frames per shot, line and caption card (CLI timeline / narrate write it)
  source/build.py       builds every scene into blend/short.blend (you write it)
  source/anchors.json   where each near label's object is on screen (BLENDER render / anchors write it)
  source/model.py       your own model or simulation when no field helper fits
  source/sound.py       optional extra sound bed built from simulation data
  source/styles.json    style options for the story page (no-brand users)
  source/notes.md       your own working notes (facts checked, decisions)
  stills/  audio/vo/  audio/mix.wav  blend/  frames/preview|final/<shot>/  review/*.html  out/*.mp4
```

The workspace has its own `.gitignore` (`*`). Never write a user's short into the plugin folder.
