# 04 Render engine (approval)

Goal: the user says yes to one engine knowing what it costs in time. This is the last question before you run to
the end on your own.

1. `CLI engine SHORT` says which engine this short would use and where that comes from (`from`): `short.json` when
   the short sets its own (`"render": {"engine": "eevee"}`, for a short whose look calls for one engine; `CLI
   validate` refuses any other value), else `settings`. A short's own engine wins over the setting. When `from` is
   `short.json`, or `settings` has `render.engine` set to `cycles` or `eevee` (the user said "always"), that engine
   is already chosen: never ask about it. The page shows it as chosen and asks nothing; say which in one line and
   record it with `CLI page decide SHORT engine approve --by settings --choice ENGINE` (the engine `CLI engine`
   named; it refuses any other). Then go on to `05-narration.md`. Otherwise:
2. **Check the machine:** `CLI machine` (CPU, cores, RAM, each GPU with its video memory and how much is free).
3. **Estimate:** `BLENDER estimate SHORT` renders one frame per scene at final quality in each engine and returns
   `eevee_s` and `cycles_s` for the whole short. It takes a few minutes; say so first, and run it with
   `run_in_background: true` (the Bash tool would cut it off after 2 minutes).
4. **Show both times and recommend one**, in plain words:
   - **Cycles** (path tracing): the best light, glass, metal and shadows; slower. The default for a polished short
     when the time is reasonable (a few hours at most).
   - **EEVEE** (real-time): much faster; fine for stylised or matte looks, weaker on reflections and glass. Its
     presets turn ray tracing on, so water, glass and metal still reflect the scene.
   - Example: "Cycles: about 2 h 10 min. EEVEE: about 18 min. This short is mostly glossy metal and glass, so I'd use
     Cycles. Go with Cycles?"
5. **Video memory:** run `CLI machine --need-gb 6` before recommending a GPU render (use about 4 GB for EEVEE). If it
   returns a `vram_warning`, pass it on as it is ("another program is using 11 GB of video memory: closing it would
   help") and include it in the recommendation. Do the same before any later GPU render or GPU voice run.
6. **Show it and wait for a yes.** `CLI page SHORT --stage engine` switches the page to the engine choice (both
   times, your recommendation); say "the page has switched to the engine" (`opened` false: say so and give the
   `url` as a link). Then run `CLI page wait SHORT --stage engine` with `run_in_background: true` and end your turn.
   `applied: true` with an `approve` decision means the user picked an engine on the page and the stage is done
   with it; `notes` (sent notes) mean answer them (`CLI page note SHORT ID done --reply "..."`) and wait again;
   `timed_out: true` means nothing yet: run the wait again in the background only while the engine still waits
   for a yes (`CLI state SHORT` still has `next` engine); otherwise drop it. A yes in chat: `CLI page decide SHORT
   engine approve --by chat --choice cycles` (or `eevee`). If the user says "always" ("always use Cycles"), also
   `CLI settings set render.engine cycles`.

Notes and decisions from the page: when a result carries `page_notes`, `CLI page notes SHORT --stage engine`
lists them. Decisions first, before you change anything: an unapplied decision is applied by `CLI page wait SHORT
--stage engine`, which returns at once; one on an older version of the engine is dropped automatically
(`superseded`) and the page asks the user again for the new version. Then act only on `sent` notes (an `open` note
is the user still writing: leave it). Coming back to a short whose `next` is the engine: when `CLI page notes SHORT`
lists engine in `waiting_for_yes`, the page already asks for it, so don't redo this chapter: just run that wait in
the background again and end your turn.

From here on, do not ask the user anything unless a check fails that you can't fix after 3 attempts.

## Comparing the engines on real frames

When the user wants to see the other engine before (or after) choosing, render it as a **side run**:
`BLENDER preview SHORT --engine eevee` (in the background; `--tag NAME` names the run, otherwise it is named after
the engine). A side run renders into its own folder and video, `frames/preview-eevee/` and
`out/<slug>-preview-eevee.mp4`, with its own frame-check findings (`review/frame-checks-preview-eevee.json`). It
never touches the approved engine's frames or video, and it never changes the engine stage or marks `preview`:
switching engines is still the user's yes and a new engine approval (`CLI page decide SHORT engine approve --by chat
--choice eevee`). Once the engine stage is approved, that means going back first: `CLI state SHORT reset engine`
clears it and every later stage, so ask the user before you do it.
The same options work on `BLENDER render`, `frame-check`, `edit` and `grab`, for the final preset too
(`out/<slug>-final-eevee.mp4`). Fingerprints, resume and the GPU lock work for side runs exactly as for the
approved one.

Every render notes the seconds per frame it measured, per engine and run, in `review/engine-runs.json`. `CLI engine
SHORT` (and the `engines` of a render's or the preview chain's result) lists each run: `engine`, `tag`, `approved`,
`s_per_frame`, `frames_measured` and its `mp4` while that file exists. Show the user those numbers as they are, with
both videos ("Cycles took 4.2 s a frame over 640 frames, EEVEE 0.6 s over 640: both previews are in out/"). A run
that kept every frame measured nothing new; a run of a few frames (`--shot`, `--frames`) measured only those, so say
how many frames a number comes from. Never extrapolate a number a run did not measure.
