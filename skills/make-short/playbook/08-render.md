# 08 Render: preview, then final

Hands-off: the preview is reviewed by you, not sent to the user for approval.

## Before any GPU render

`CLI machine --need-gb 6` (about 4 for EEVEE). If it returns a `vram_warning`, pass it on once and carry on. It
names the programs holding the video memory when `nvidia-smi` can say ("llama-server.exe is using most of the video
memory"); suggest closing it, but never stop a program yourself.

## One GPU job at a time

`BLENDER render`, the preview chain's render step, `BLENDER extremes`, `BLENDER test-frames`, `BLENDER stills` and `BLENDER estimate` take the workspace's
GPU lock (`<workspace>/.gpu.lock`: pid, short, command, start time), so two sessions never render at once. A second
command waits: it says "waiting for pendulum render (pid 1234, since 14:02:31)" on stderr (and again every 30 s: "still waiting for ...: waited 1 min 30 s"), and `render-status` and
the page say "final render waiting for the GPU: ...". It starts by itself when the first ends, or at once when the
first's Blender is gone (a crashed or killed run leaves a stale lock, which is cleared). Tell the user what it waits
for. With `--no-wait` it fails at once instead, naming the holder: use that when the user would rather not queue.

## Test frames (a quick look before the preview)

`BLENDER test-frames SHORT --at s01:0.5 s05:0.3 [--scale 0.4]` renders single frames, each at a fraction of its
shot (0 = the shot's first frame, 1 = its last), with the short's current build (`blend/short.blend`), engine and
look, at `--scale` of the full resolution (default 0.4), into `review/test-frames/<shot>-<percent>-f<frame>.png`.
Its result lists the PNG `paths` (and each one's shot, fraction and frame): open them. Use it to tune a material,
a light or a framing without a whole preview render, instead of a render script of your own. It takes the GPU lock
like the commands above (it waits and says for what, or fails at once with `--no-wait`), so it never renders
alongside another session's job. Rebuild first after changing `build.py` (it warns when the .blend is older).
Judge framing on `--guides` frames: they show the caption zone, the top cards and the framing band the edit and the
checks use. `--guides` (on `test-frames`, `stills` and `grab`) also writes `<name>-guides.png` beside each frame
with those zones outlined (caption zone yellow, top cards cyan with a kit, framing band magenta) and lists them in
the result's `guides`; the plain frames are unchanged. The magenta band is the default framing band: a shot's own
framing (`px_top`, `px_bottom`, `px_left`, `px_right` or `top` given to `camera.frame_objects`) is not drawn, so
judge such a shot against the band you gave it. `grab --guides` warns when `source/timeline.json` is newer than the
video (the timing changed after the edit: a grab's shot may be off).

## Moving parts at their extremes (before the first preview)

`BLENDER extremes SHORT [--shot ID]` finds, for every hero of each shot (`hero`, or `hero:s03,s04` for those
shots) and every object parented under it, the frames where each animated channel is at its minimum and maximum
within the shot, and renders each of those frames once, at full resolution, through the shot's camera, into
`review/extremes/<shot>-f0123.jpg` (its result lists each still's frame and `why`, such as "Piston rod
location[0] max"). A hero moved by something other than its own keys (a keyed pivot empty that is no hero, a
constraint, a driver) is followed in the world: its stills say "Ball world location[0] max (moved by Pivot)".
Run it in the background on a long short. **Open every still and look at the joints**: rods
inside hollow cylinders (never through a solid body), cables ending on surfaces, parts that must not touch with a
gap between them. Max stroke and max swing are where parts pass through each other; mid-shot stills hide it. A
warning "no hero moves" means nothing tagged hero moves, by its own keys or through a parent or constraint: tag the
moving subject and parent its moving parts under it.

## Watching a render (and the rest of the chain)

Every `BLENDER preview`, `BLENDER render`, `BLENDER check`, `BLENDER frame-check` and `BLENDER edit` keeps a live
status while it runs: `review/render-status-<preset>.json`, which the review page's Render stage shows live
(`BLENDER check` shows with the preview unless given `--preset final`).

- **The chain.** `BLENDER preview` lists its steps (timeline, build, build done, mix, check, render, frame-check,
  edit), each pending, running, done, skipped (with why: "the .blend is current", "already passed on every shot as
  it is now") or failed (with the error), with start and end times and a summary per step. The current step is
  highlighted at the top. The check step runs beside the render: both show running, the check with the shot it is
  on ("checking s02 (2 of 4), 0 errors so far"); a check that stops moving says "no progress for ..." on its row
  and in the summary. A standalone `check`, `frame-check`, `edit` or `render` shows as a run of one step.
- **Progress inside a step.** `check` counts the frames it has checked of those it has to check, the shot (and
  scene) it is on, a row per shot (kept, waiting, checking, done), the elapsed time and the time left from its
  measured speed; `frame-check` counts frames per shot the same way, and `edit` counts the frames it has encoded.
  The status is written at most every 2 s, so it costs nothing you would notice.
- **The render.** While it renders, the page also shows the render settings (engine, resolution and %, samples,
  denoise, motion blur, device and GPU, video memory), an overall bar, a bar and a thumbnail of the latest checked
  frame per shot, frames reused from disk versus rendered now, the time per frame, the time left (from `BLENDER
  estimate` until a few frames are measured), and the frame checks it runs as it goes (black frames and fireflies on
  a sample of frames, flicker where the sampled frames follow one another, stuck frames on every frame), and per
  shot whether it is kept or rendered again and why ("kept (unchanged)", "rendered again: fingerprint changed",
  "new range", "engine changed", "redoing frames 120-140").
- **A new run replaces the old one.** When a run starts, the page stops showing the previous run as current: it
  keeps one line about the previous run (how it ended and when), so an old "done" never passes for this run. A run
  that is still going in another process is left alone (the new one is not recorded there).

The page updates itself while something runs.

- **When the chain or a render starts** (the preview chain or the final render, in the background), run
  `CLI page SHORT --stage render` once it has started: it switches the open page to the Render stage (or opens the
  page: when `opened` is false, say it didn't open and give the `url` as a link). Say in one line that the page has
  switched to the render and shows the progress live.
- **When the user asks where it is** (or you need to know), run `CLI render-status SHORT [--preset ...]` instead of
  counting PNGs. Relay its `summary` (e.g. "preview chain running: step 5 of 8, check: 340 of 1200 frames (28%),
  shot s03 (scene Clock), about 4 min left"). `step` is the current step (name, index of total, progress); `idle:
  true` means nothing is running and `last_run` says how the last run ended and when. `alive` says whether its
  Blender is still running; `stale: true` means it says running but its process is gone (killed, crashed, the
  session ended): run the same command again (`BLENDER preview` skips the steps already current, a render resumes
  from the frames on disk). It works for every step, after a session ended or the computer restarted, and the
  page shows the true state too. Each run has a `run_id` and `started_at`, and the summary ends "(run started
  14:02:31)": if that is before you launched the command, it is the last run, not yours (`note` says so). Blender
  takes a few seconds to start before the new run writes "starting": check again then.
- Live warnings are early news, not the verdict: `BLENDER frame-check` still checks every frame afterwards.

## Only what changed renders again

Every shot carries a **fingerprint**: a hash of everything that can change its pixels (its scene's render settings,
world and colour management, its camera and every object's motion at each of its frames, meshes, modifiers,
materials and node trees, images and fonts with their files, lights, text, the compositor, visibility). Its frames
(`frames/<preset>/<shot>/.render.json`), its anchors and its scene checks are stamped with it, so after a rebuild
only the shots whose fingerprint changed render again, re-project their anchors and are checked again; the others
are kept, even though the .blend was saved again. A shot also renders again when its frame range (`new range`) or
the engine (`engine changed`) changed. `BLENDER render`'s result says per shot `rendered`, `kept` and `why`; the
preview chain's `render` step lists the shots kept and the ones rendered with why. Shots that share a scene are
told apart by what happens in their own frames: a camera key moved inside shot s02 re-renders s02 only (and s01 too
if the move bends the camera's path inside s01). Anything you are unsure about counts as a change: at worst a shot
renders again that did not need to. The fingerprints of a saved .blend are cached in
`blend/short.fingerprints.json`; it is kept current by itself (never edit or copy it).

`BLENDER check SHORT` works the same way: it re-checks only shots whose fingerprint, frames, short.json `scene` or
`labels`, list of shots or objects' tags (`contact:`, `hero`, `grounded`, `overlay`, `surface`: a tag changes no
pixel, so it re-checks without rendering again) changed, or (when a hard rule scans scene names) whose rules changed, and keeps the
other shots' findings (its result's `checked` and `kept`); a change
to the checks' own code (a plugin update) re-checks everything, and `--full` does so on demand. Nothing else counts:
the post text, sound levels (`sfx`, `source/sound.py`, `CLI mix`), the end-card text and a shot's `title`,
`picture`, `still_at` or `badge` never make frames or checks stale. When the final render or edit refuses, its
message names what changed ("shot s01's camera/objects changed", "short.json shot s02 entry changed (fields:
labels)", "the timing changed", "the checks code changed"): run what it says. A change every shot shares is said
once: editing `rules_check` (adding `cleared` words included) reads "the hard rules the scene names are checked
against changed (all 7 shots)". It does make every shot's checks stale, because the scene-name scan is part of each
shot's check: run `BLENDER check SHORT` once, and say so when you clear a word.

## Redoing single frames

When frame-check flags frames (a black or half-written frame, fireflies, flicker), every finding in
`review/frame-checks-<preset>.json` carries its `shot`, the `redo_frames` and a ready `redo` command:
`BLENDER render "<short>" --preset preview --shot s02 --frames 120-122` renders just those frames again
(overwriting them; `--frames` takes ranges and single frames, `120-140,200`, and needs `--shot`).
`BLENDER render SHORT --preset preview --redo-flagged` redoes every frame the last frame-check flagged (for flicker
the frame and one either side). Fix the cause first (flicker: z-fighting from coplanar faces, or too few samples for
the denoiser; see `07-checks.md`); a render of unchanged settings gives the same pixels. If the scene changed since
the shot was rendered, the redone frames come from the new build and the shot's other frames stay from the old one:
the result's `warnings` say so, and you render the whole shot (`--shot ID`) when the change shows outside the
redone frames. Run frame-check again afterwards.

## Another engine, side by side (side runs)

`--engine cycles|eevee [--tag NAME]` on `BLENDER preview`, `render`, `frame-check`, `edit` and `grab` makes a
**side run**: another engine than the approved one (tagged with the engine's name), or any run given a tag. It
reads and writes only its own files: frames in `frames/<preset>-<tag>/<shot>/`, findings in
`review/frame-checks-<preset>-<tag>.json` (its `redo` commands carry the same options), the video in
`out/<slug>-<preset>-<tag>.mp4`, grabs as `review/grabs/<preset>-<tag>-...`. The approved engine's frames and video
are never overwritten, the engine stage keeps its engine, and a side edit never marks `preview` or `final`. Use the
same options on every command of one side run (`BLENDER render SHORT --preset preview --engine eevee`, then
`frame-check` and `edit` with `--engine eevee`); the chain passes them on itself. Tags are lowercase letters, digits
and hyphens; `preview` and `final` are refused. Its fingerprints, resume, scene checks and GPU lock are the same as
the approved run's. Its live status is its own too: `review/render-status-<preset>-<tag>.json` and its thumbnails,
so the approved run's status is never overwritten (`CLI render-status SHORT --preset preview --tag eevee` reads it,
and the page's Render stage shows it beside the approved run).

## Each run's timeline on the page

Once a run's edit has made its video, the Render stage shows that run in its own tab (`Preview`, and a side run as
`Preview · eevee`) with the video and a timeline under it: a frame slider and rows of markers for the shots, the
captions, the sounds, the findings (the scene checks and that run's frame checks), the holds and the closing
card, each at its frame. Clicking a marker shows that frame; findings that have no frame are listed under the
timeline. The Final stage has the same timeline for the final video. "Note at this moment" leaves a frame note: on
the Render stage its id is `<run key>:<frame>` (`preview:412`, `preview-eevee:412`), on Final the frame alone; read
it as SKILL.md rule 4 says (grab that frame and look at it).

When the timing changed after a video was cut (a re-take, a new narration, a timeline change), the page says "This
video was cut from an older timing; run the edit again to line up the markers." and hides the markers, because they
would point at the wrong frames. Run the edit again for that video: `BLENDER preview SHORT` for the preview (it reruns
what the new timing needs, render and frame-check included, then the edit), and for the final `BLENDER render SHORT
--preset final`, `BLENDER frame-check SHORT --preset final` and `BLENDER edit SHORT --preset final` (shots whose
frames are current are kept); a side run's edit takes its own `--engine` / `--tag`. The banner goes away by itself
once the new edit is made. A video edited before edits kept their timing record shows "This video has no timing
record; run the edit again to line up the markers." instead: the same fix.

Each render's result has `s_per_frame` (the average over the frames it rendered fresh) and `engines`: every run of
that preset on record in `review/engine-runs.json`, with what it measured and its MP4 while it exists. Relay those
numbers as measured (`04-engine.md`, "Comparing the engines on real frames").

## Preview

Renders and frame checks run with `run_in_background: true` (the Bash tool stops a command after 2 minutes by
default); you are notified when they finish. If one is cut off, run the same command again: it resumes. A resume
first verifies every frame it keeps (PNG signature through the end chunk): frames a crash or power cut left cut off
or zero-filled are deleted and rendered again, and the result's `warnings` and each shot's `damaged` name them.

**After any change (build.py, short.json, a camera tweak), use the chain:** `BLENDER preview SHORT` (in the
background) runs everything needed to get back to a checked preview, through the same code as the single commands:
the timeline (only if the timing changed), the build in script mode (only if build.py is newer than the .blend, the
scenes are missing or from another timing, or with `--rebuild`), `state done build` (when the .blend is newer than
that mark), `mix`, `check` (skipped when it already passed on every shot as it is now; otherwise only changed shots
are checked again, in a second Blender beside the render: the checks need no GPU, and the page shows their progress
on the check step while the render runs), `render --preset preview` (it resumes; only changed shots render again, and
its step says which were kept and why the others rendered), `frame-check --preset preview`, then (once the checks
have ended) `edit --preset preview`. Its result's `steps` say what ran or was skipped and why, and its `closing` (as
the edit's) has a `note` when the closing card holds longer than `closing.seconds` so its voice line fits: tell the
user. When the checks find errors the preview is still made: the result's `warnings` (and a `warning:` line on
stderr) say how many and the first few, the check step shows failed and the `checks` stage stays unmarked, so the
final render and the final edit refuse until they are fixed and checked again; tell the user and fix them. Any other
failure stops the chain with "stopped at <step>: ..." (a check process that crashed: "stopped at check: the checks
stopped: ...", before the edit): fix that and run it again. Stopping the chain (or a failure in the render) also
ends the check process. In MCP mode,
rebuild through the MCP first and run `BLENDER preview SHORT --no-build`: it never runs build.py and refuses with
a plain message when the .blend is not current. The chain needs every stage up to narration done. Then do steps 3,
5 and 6 below (look at the frames, grab frames of the video, review the timing) yourself.

The single steps, when you need one on its own:

1. `BLENDER render SHORT --preset preview`: half resolution, few samples, PNG frames into
   `frames/preview/<shot>/`, using the engine approved at the engine stage. Renders are resumable: frames already on
   disk of the same shot content (fingerprint), range and engine are kept; if any of these changed, that shot's old
   frames are cleared first so nothing stale slips into the video. `--shot ID` renders one shot; `--frames` and
   `--redo-flagged` redo single frames (above). After rendering it also
   runs `BLENDER anchors SHORT` itself: where each `near` label's object is on screen, frame by frame, and
   the screen boxes of the heroes of those shots (labels keep off them) and of the last shot, with that shot's other
   meshes during the end card (the end card keeps off them too), into `source/anchors.json` (its result's
   `anchors`). Run `BLENDER anchors SHORT` on its own only after changing labels
   without re-rendering; the edit refuses missing or stale anchors with "run BLENDER anchors SHORT".
2. `BLENDER frame-check SHORT --preset preview`: black frames, fireflies, stuck frames and flicker (an area that
   changes for one frame and comes back: z-fighting or denoiser flicker; the finding says the frame and where on
   screen). Thin lines (a string, a turning spoke) do not count: they alias as they move. More than three small
   spots (each under 0.5% of the frame) at frames where something moves are one finding for the shot, with their
   count and frames and the largest one's place: usually thin or fast parts aliasing. Look at that frame; a still
   surface flickering there is z-fighting. A lone spot under 0.2% of the frame at a frame where something moves is
   not reported: it shows for one frame, too small to see. One that comes back at the same place still is. Fix any error (`07-checks.md`); redo flagged frames as above.
3. **Look at the frames yourself** (`07-checks.md`, "Look at the images yourself"): first, middle and last frame of
   every shot plus the key moments.
4. `BLENDER edit SHORT --preset preview` (no .blend needed): captions, badges, labels (near labels just above their object, inside the safe
   area, never on a caption, and off the shot's other heroes where there is room; a hero whose box holds the
   label's object, such as the assembly it is part of, does not count), the end card (the user's line and handle after the last line, if they have a
   handle: in the upper third, else where the captions sit (free by then), else the middle, else low above the
   captions, whichever no hero of the last shot and no other mesh on screen covers (rails, a stand; a floor or
   backdrop filling much of the frame does not count), else the first no hero covers; a `warnings` entry says when it could not avoid one, or when
   hero positions were missing so it was not checked: run `BLENDER anchors SHORT`), the mix, H.264 + AAC into `out/<slug>-preview.mp4`. Any old file at that path is deleted first,
   so a failed encode can never leave a stale video behind. It uses exactly each shot's frames from the current timing,
   engine and build (a short or stale render is refused: render again), probes the result and fails if the size,
   fps, frame count or audio is wrong; on success it marks `preview`. `font` in the result is the font the text used; a
   `font_fallback` means the style font isn't installed and says which font was used instead (`CLI fonts` lists
   installed ones): install it or pick another, then edit again.
5. **Look at the edited video:** `BLENDER grab SHORT --preset preview --at 1.5 12 30.2` pulls those moments of
   `out/<slug>-preview.mp4` (`out/<slug>.mp4` for `--preset final`) at full size into
   `review/grabs/preview-f0046.png` and so on (its result maps each second to its frame and file);
   `--sheet [--count 12] [--columns 4]` makes one quarter-size contact sheet of evenly spaced frames,
   `review/grabs/preview-sheet.png`, for an overview. Grabs of an older edit of the same video are removed first
   (the result's `cleared` counts them), so the folder only ever holds frames of the video as it is now; a side
   run's grabs are its own. The result's `older` counts this video's other grabs still in the folder (with an
   `older_note`; a grab and its `--guides` copy count as one); `--clear` removes them before grabbing. `--guides`
   also writes `preview-f0046-guides.png` beside each `--at` grab (the shot comes from the timeline; a frame in the
   closing card gets none): judge framing on those. No .blend is needed. The grabs use the Standard view
   transform, so their colours match the video (pulling frames through a default scene's AgX washes them out). Check
   captions, labels, badges and the end card where they really sit, and grab the moments where parts move against
   each other at full size: a contact sheet is too small to show thin parts passing through each other.
6. **Review the timing:** each idea lands on its visual (the line about the escapement is on screen while the
   escapement is), captions are on screen long enough to read, nothing important happens under a caption. Fix the timeline
   or the build and redo the preview if not.

## Final

1. `BLENDER render SHORT --preset final` (full resolution, final samples, motion blur), in the background. It
   refuses to start unless `BLENDER check SHORT` passed on every shot as it is now and this timing (rebuilt since?
   check again: it re-checks only changed shots). Long renders survive the
   session ending: if the user comes back later, run the same command and it carries on from the frames on disk.
   Tell the user roughly how long it will take (from the estimate) and that they can leave it running, and switch
   the page to the render (`CLI page SHORT --stage render`, with the same `opened` rule). While it renders is a good time to write the
   post text (`09-post.md` step 1): it never makes the frames or checks stale.
2. `BLENDER frame-check SHORT --preset final` (in the background too), fix errors, and look at a sample of final frames yourself (noise
   and fireflies show up here, not in the preview).
   After the final edit, `BLENDER grab SHORT --preset final --sheet` and a few `--at` grabs show the finished video
   itself.
3. `BLENDER edit SHORT --preset final` (it also needs the passing checks) writes `out/<slug>.mp4` (1080×1920, 30 fps, H.264 + AAC 48 kHz stereo) and
   marks `final`. The same edit also writes the caption files beside it: `out/<slug>.srt`, `out/<slug>.vtt` and
   `out/<slug>-transcript.txt`, cue for cue with the captions in the video. If you chain it after the render and frame-check, read every step's `BSA_RESULT`: a refusal
   prints one too (`"ok": false` and the `error`), so relay it rather than retrying blind.
4. `BLENDER probe "<short>/out/<slug>.mp4"` if you want to double-check: width 1080, height 1920, fps 30, frames
   equal to the timeline's `n_frames`, `has_audio` true.

Then go to `09-post.md`.
