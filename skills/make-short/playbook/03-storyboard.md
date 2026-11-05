# 03 Storyboard (approval)

Goal: a still from Blender for **every** shot, in order, with its narration line, timing and captions, so the user
sees how the scene progresses and where every line lands before anything long is rendered.

**Cuts or one take.** A short is cuts by default: each shot has its own camera and its length comes from its lines.
One take is the user's choice, like every camera style: offer it when the story is one continuous journey round or
through one machine (the camera never cuts, so the joins are invisible), and use it only on their yes. A one-take
short sets `"camera": {"style": "one-take", "seconds": 24}` (`seconds`: the camera path's length) and gives every
shot `"at"`: the second its window starts on the camera path (the first is 0, each later one after the last, all
before `seconds`; the last window ends at `seconds`). The picture sets the clock: each shot is exactly its window,
each line starts in it (after the usual lead) and a window with time left over holds, so redoing the narration never
moves the camera. Give each window its lines' reading time plus about a second (and the last one 1.5 s more when
there is an end card); when a window is too short, `CLI validate` and `CLI timeline` say by how much ("s04's lines
need 7.2 s but its window is 6.0 s: shorten them or move s05's start 1.2 s later") and the page shows it on the
storyboard. Give every shot the same `scene`: build.py drives its one camera from the windows (`06-build.md`,
"One-take shorts"). The camera stage is always offered for a one-take short.

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

**The camera stage, after the storyboard.** A one-take short always has it, and a cuts short has it when the user
asks to see the camera's move first (`"camera": {"approve": true}` in short.json). Once the storyboard is approved
(the scene is already built for the stills), run `BLENDER camtest SHORT` (`06-build.md`, "Test the camera first"),
look at the clip and the contact sheet yourself, then `CLI page SHORT --stage camera` and wait on it as above (`CLI
page wait SHORT --stage camera`). The page plays the clip and shows the sheet; Approve needs a clip (with none it
names the camtest command and sends nothing). A clip that no longer matches the short (made for some shots only, or
before the windows or build.py changed) is marked out of date with the reason; run camtest again before asking,
though the user may still approve it. Notes on the camera mean fix the move in build.py, run camtest again, show it
and wait again. A yes in chat: `CLI page decide SHORT camera approve --by chat`.

Notes and decisions from the page: when a result carries `page_notes`, `CLI page notes SHORT --stage storyboard`
lists them. Decisions first, before you change anything: an unapplied decision is applied by `CLI page wait SHORT
--stage storyboard`, which returns at once; one on an older version of the storyboard is dropped automatically
(`superseded`) and the page asks the user again for the new version. Then act only on `sent` notes (an `open` note
is the user still writing: leave it). Coming back to a short whose `next` is the storyboard: when `CLI page notes SHORT`
lists storyboard in `waiting_for_yes`, the page already asks for it, so don't redo this chapter: just run that wait in
the background again and end your turn.
