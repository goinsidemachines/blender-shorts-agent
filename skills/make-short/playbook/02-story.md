# 02 Story (approval)

Goal: a story the user approves on an HTML page: the hook, the beats, the facts with their sources, the closing line,
and (for no-brand users) a choice of look. Read `craft-short-form.md` and `honesty.md` before writing it.

## 1. Research the facts first

- Find every number and claim you might use. Prefer primary or expert sources (the engineers, the manufacturer,
  a standards body, a peer-reviewed paper, a reference work); confirm important numbers in two independent sources.
- Keep a **Use** list and a **Don't use** list in `source/notes.md`. Conflicting figures go in "Don't use" with the
  reason ("cable count: the sources disagree with each other and with the drawings, so don't state it"). Popular claims that are wrong or overstated go there too ("the gyroscope effect keeps a bike upright":
  experiments with the gyro effect cancelled say otherwise).
- Only "Use" facts go into `short.json` `facts`, each with a working `source` URL.

## 2. Write the story into short.json

`source/short.json` (schema below). Claude writes it; the user never reads it.

- **Hook (first line, first ~3 s):** the most surprising true thing, said plainly, over the strongest picture.
  "A mechanical watch ticks 28,800 times an hour." (an example of the form, not a fact to reuse)
- **Beats:** one idea per shot, in an order where each needs only the ones before it. Problem → mechanism → proof
  → payoff is a good default.
- **Closing line:** short, rhythmic, and it lands the idea. "It doesn't store time. It spends it, one tick at a time." Not a
  summary, not "thanks for watching".
- **Lines** are the narration and the captions at once: write them to be heard and read. Short sentences, concrete
  words, no jargon without a picture. Numbers in `text` are written as the captions show them ("28,800 times"). When
  the caption shows something a voice would misread (an equation, "×", "≈", "→", an unusual number), add `say`: the
  words the narrator speaks and Whisper checks (`"text": "30 × 2.7 = 80 × 1", "say": "Thirty times two point
  seven equals eighty times one."`). The caption keeps `text`; `validate` checks both (a number in either needs a
  fact) and warns about such symbols in a line with no `say`.
- **Shots:** `picture` describes what is on screen (camera, subject, motion, light); `scene` names the Blender scene
  that will hold it; `min_s` is the least time the picture needs (keep it near what the line needs: shorts are
  fast-paced, see `craft-short-form.md` "Pacing"); `still_at` (0 to 1) is where in the shot the
  storyboard still is taken (pick the most telling moment); `labels` are on-screen words: a plain string is stacked in the top-left corner for
  the whole shot; `{"text": "Light", "near": "Light bob"}` sits just above that object and follows it (`near` is the
  object's exact name in build.py, so label the things the picture shows by name); `badge` is the honesty
  badge ("Motion exaggerated", "Simplified") or `null`; `hold` (optional) marks a deliberate freeze, `true` for the
  whole shot or `[[from_s, to_s], ...]` seconds from its start, so frame-check does not call it stuck.
- **Sound:** plan effects per shot now (`sound.md`); no music.

### short.json

```json
{
  "version": 1, "slug": "watch-escapement", "title": "Why a watch ticks 28,800 times an hour",
  "topic": "mechanical watch escapement", "audience": "curious general viewers", "field": "physics",
  "length_s": 45, "fps": 30, "width": 1080, "height": 1920,
  "style": {"name": "workshop macro", "palette": {"accent": "#E8B33A"}, "font": "Segoe UI", "caption": {"size": 64}},
  "facts": [{"id": "f-rate", "claim": "This movement beats at 28,800 vibrations per hour", "source": "https://..."}],
  "lines": [{"id": "l01", "text": "A mechanical watch ticks 28,800 times an hour.", "shot": "s01",
             "facts": ["f-rate"]}],
  "shots": [{"id": "s01", "title": "Hook", "picture": "Macro push-in on the balance wheel...", "scene": "Movement",
             "min_s": 3.0, "still_at": 0.5, "labels": [], "badge": null}],
  "sfx": [{"kind": "whoosh", "shot": "s03", "at_s": 0.2, "dur_s": 0.8, "level_db": -18}],
  "assets": [],
  "post": {"title": "", "description": "", "hashtags": []},
  "end_card": null
}
```

- `style.font` must be installed on this computer: check it with `CLI fonts "NAME"` (`installed`, plus
  `suggestions` of installed fonts). Pick one that ships with the OS when in doubt ("Segoe UI" on Windows, "Helvetica
  Neue" on macOS, "DejaVu Sans" on Linux); a missing font falls back to those, then to Blender's own.
- Every line whose text has a digit must cite at least one fact; every label with a digit needs a line in the same
  shot that cites one. Leave `fps`, `width`, `height` as shown.
- Ids are stable: `l01`... for lines (narration takes are named after them), `s01`... for shots, `f-...` for facts.
- `end_card`: `null` uses the user's `end_card` setting above their @handle; a line, or `{"text": "...", "second":
  "..."}` (a second line under the handle, such as their site), gives this short its own (`look-and-brand.md`).
- `sfx.kind` is one of: whoosh, blip, impact, rumble, hiss, creak, hum, wind.

Run `CLI validate SHORT` and fix everything it reports before showing anything. `warnings` tell you when the reading
pace puts the short far off `length_s`, when a shot is mostly silent (`pacing`: spoken vs silent seconds per shot,
with a suggested length of about speech + 25%), and when a caption needs a `say`: cut, re-time or add, don't just
accept it. A deliberate hold is fine; say in chat which holds are on purpose.

## 3. Style options (only when `brand.mode` is `fresh`, or this short has `"look": "fresh"`)

A brand user who wants a different look for one short (a demo, a one-off): set `"look": "fresh"` in `short.json`
and leave the settings alone (`look-and-brand.md`, "One short with its own look").

Suggest 2 to 3 looks that fit this topic (see `look-and-brand.md`), each a name and one sentence, in
`source/styles.json`: `[{"name": "Storm dusk", "description": "..."}, ...]`. Make a thumbnail of the hook shot in
each look: run `CLI timeline SHORT` first (build.py reads its frame ranges from `source/timeline.json`), write
enough of `source/build.py` to build the hook scene, reading the look from the `BSA_STYLE`
environment variable (1, 2, 3), then for each n run the build with `BSA_STYLE=n` and

```
BLENDER stills SHORT --shot s01 --out "<short>/stills/style-<n>.jpg"
```

Look at each thumbnail yourself before showing it (pink or grey surfaces, a black frame, a cut-off subject: fix
first). When the user picks one, write it into `short.json` `style` and note the number for the build. With a brand,
copy the brand's palette, font and caption style into `style` instead and skip the thumbnails.

## 4. Assets

If a look needs outside textures, HDRIs or models, plan them now (`assets.md`): the story page lists them as
"downloaded after your yes" with name, source, licence and size, and nothing is downloaded until the user says yes.

## 5. Hard rules

Read `CLI settings rules` again and write the story inside them: every line, picture, label, badge and sound.
A short may add rules of its own in `short.json` `rules` (`[{"id": "t1", "text": "the user's words", "kind": ...}]`,
ids that are not the user's `r...` ones) when the user asks for one; it can never drop or loosen theirs (a
`sound_policy` of `any` under a natural-sounds rule is an error).

Then write `rules_check`: for every rule, in plain words, how this short keeps it.

```json
"rules_check": {
  "r1": "No people anywhere: only the watch, its parts and the workbench.",
  "r2": {"kept": "Clock parts only.", "cleared": {"hand": "the watch's hour hand, not a person's"}}
}
```

`CLI validate SHORT` reports an error for any rule with no entry, and warnings where a word may break a rule with a
kind (a "man" in a picture under `no_humans`, a "logo" under `no_brand_logos`, labels under captions-only). Change
the story, or, only when the word truly keeps the rule (a clock's "hand" or "face"), add it to that rule's
`cleared` with the reason: the story page shows each one to the user. Each flagged word is one warning that lists
every place it appears with the words around it ("line l02 \"…the other face is a ramp…\""), and one hint per rule
says how to clear words: read the context, never clear a word you have not looked at. The words a rule checks are
never narrowed for engineering terms (a tooth's "face", a freehub "body", an arrow "head"): clearing is your
judgement, said in your own words. Word checks only catch words; you still judge every rule, including the ones
with no kind.

## 6. Show it

`CLI review story SHORT --open` (its "Your hard rules" panel shows each rule and how this short keeps it). In chat, give two or three sentences: the hook, the angle, what you cut and why
(myths especially), and the question: "Happy with this story, or what should change?"

Revise until the user approves. Then `CLI state SHORT done story` (it refuses while `CLI validate SHORT` reports
errors).
