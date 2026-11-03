# 03 Storyboard (approval)

Goal: a still from Blender for **every** shot, in order, with its narration line, timing and captions, so the user
sees how the scene progresses and where every line lands before anything long is rendered.

1. **Time it.** `CLI timeline SHORT` writes `source/timeline.json` at reading pace (narration comes later and
   re-times it). Each shot has `f0`/`f1` in the short's frame numbers (1-based, 30 fps).
2. **Build it.** Write `source/build.py` for every scene (`06-build.md` has the contract, read it now) and run it.
   The storyboard build needs the real models, materials, lighting, cameras and the motion at each `still_at`, not
   placeholders: the user approves what they see. Motion comes from the simulation or model (`fields/*.md`), even
   now.
3. **Stills:** `BLENDER stills SHORT` writes `stills/<shot>.jpg` at each shot's `still_at` (half resolution, quick
   samples).
3b. **With a brand kit: the look preview.** When the short has a kit (`brand.style` set, and no `"look": "fresh"`),
    run `BLENDER look SHORT` after the stills. It renders three frames (the first shot, a middle one, the last) in
    the kit's style and set, with the kit's cards on top, and writes `stills/look.json`. It needs the user's engine
    (`--engine eevee|cycles`, else the short's approved engine or `render.engine`): when there is none, ask the user
    and never choose one. Look at the three frames yourself, as with the stills. The page shows them as the "Look"
    section of the Storyboard stage, and the user approves the look together with the storyboard (one approval).
    A `look` note on the page (`id` `style` or `set`, "Use the pencil style") is handled as `look-and-brand.md`
    "Look preview" says: `BLENDER look SHORT --style NAME` or `--set NAME`, then save the choice once they agree.
4. **Look at every still yourself** (read each image) before showing anything. Hunt for: pink or flat-grey
   surfaces (missing or default material), black or blown-out frames, the subject cut off or outside the safe area,
   wrong-looking motion (a part through another, something floating), a picture that does not show what its line
   says. Fix and re-run the stills until every one is right. `07-checks.md` lists the usual causes.
5. **Each idea lands on its visual.** Read each line next to its still: the line about the escapement sits on
   the escapement close-up, not on the wide shot before it. If a line needs a different picture, change the shot, not the
   line.
6. **Hard rules:** read `CLI settings rules` and look at every still against each rule (a silhouette in a window,
   a face in a texture, a logo on a downloaded model, words on a sign). Fix the scene, and update `rules_check` if
   what keeps a rule changed. The page shows the rules beside the shots.
7. **Show it:** `CLI page SHORT --stage storyboard` switches the page to the storyboard; say "the page has switched
   to the storyboard" (the same `opened` rule as at the interview: false means say so and give the `url` as a
   link). In chat: how long it runs, anything you simplified or badged, and "Does this storyboard work? Approve it
   on the page, or tell me what should change."
8. **Wait:** `CLI page wait SHORT --stage storyboard` with `run_in_background: true`, and end your turn. `applied:
   true` with an `approve` decision means the user approved on the page and the stage is done; `notes` (or a
   `changes` decision) mean fix the shots, answer each note (`CLI page note SHORT ID done --reply "..."`, or
   `declined` and why), re-run the stills, show it again and wait again. `timed_out: true` means nothing yet: run
   the wait again in the background only while the storyboard still waits for a yes (`CLI state SHORT` still has
   `next` storyboard); otherwise drop it.

A yes in chat instead: `CLI page decide SHORT storyboard approve --by chat`. Story changes at this point mean `CLI
state SHORT reset story` and back to `02-story.md` (ask the user first when the note is on the approved story).

Notes and decisions from the page: when a result carries `page_notes`, `CLI page notes SHORT --stage storyboard`
lists them. Decisions first, before you change anything: an unapplied decision is applied by `CLI page wait SHORT
--stage storyboard`, which returns at once; one on an older version of the storyboard is dropped automatically
(`superseded`) and the page asks the user again for the new version. Then act only on `sent` notes (an `open` note
is the user still writing: leave it). Coming back to a short whose `next` is the storyboard: when `CLI page notes SHORT`
lists storyboard in `waiting_for_yes`, the page already asks for it, so don't redo this chapter: just run that wait in
the background again and end your turn.
