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
7. **Show it:** `CLI review storyboard SHORT --open`. In chat: how long it runs, anything you simplified or badged,
   and "Does this storyboard work, or what should change?"

When the user approves: `CLI state SHORT done storyboard`. Story changes at this point mean
`CLI state SHORT reset story` and back to `02-story.md`.
