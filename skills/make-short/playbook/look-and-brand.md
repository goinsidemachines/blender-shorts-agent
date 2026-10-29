# Look and brand

## Brand or fresh style

`settings` `brand.mode`:

- **`brand`**: the user's look for every short, stored under `brand` (palette, font, caption style, material feel).
  Copy it into each short's `short.json` `style` (`palette`, `font`, `caption.size`) and follow it in the build. Do
  not offer style options.
- **`fresh`**: suggest 2 to 3 looks that fit each topic on the story page, each with a rendered thumbnail of the
  hook shot (`02-story.md`); the user picks one.

### One short with its own look

A short can leave the brand for itself without touching the settings: `short.json` `"look": "fresh"` (a demo, a
one-off, a brief that asks for a different look). Then, for that short only:

- the story page offers 2 to 3 looks as in fresh mode (`02-story.md`), and says the brand is off for this short;
- `BLENDER stills` compares backgrounds only with the short's own `style.palette.background` (and calls it "the
  short's background"), never with the brand's;
- the edit's captions use only `style.caption` (not `brand.caption`);
- `look.brand_view` and `look.paper_world` leave the brand's exposure and look off. `setup_scene` stamps the
  short's look on each scene, so this holds even when `build.py` does not pass `short=`; passing it is still clearer.

The same holds for every short under `brand.mode fresh` (unless the short says `"look": "brand"`): no brand
background, captions or view.

`"look": "brand"` does the opposite for one short under `brand.mode fresh`. Without `look` the short follows
`brand.mode`. Don't change `brand.mode` for one short and change it back: set `look` instead.

The look never includes plugin-author (goinsidemachines) branding, unless the user is that brand or asks for it.
By default the only text identity in a video is the user's own, on the end card: their handle, if they gave one,
with their end-card line above it (the `end_card` setting, e.g. "Follow for more").

### The closing card

- With a `website` or a `closing.image` set (`00-first-run.md` section 7), every final short ends on the closing card:
  the user's image, or one made from the brand, held `closing.seconds` after the story, with `closing.voice_line`
  narrated over it (it is narrated with the story's lines, as take `closing`; no caption, no music, no sound effects).
  It replaces the overlay end card. `BLENDER closing SHORT` makes it (`stills/closing.png`) and reports warnings;
  `BLENDER stills` and the edit make it too, and the storyboard and final pages show it for approval.
- Look at the card against the user's hard rules like any other picture (no humans, faces and so on). The rule word
  checks in `CLI validate` read its voice line and website.
- Warnings say when its text sits in the bottom 18% or right 12%, where YouTube's title and buttons cover it.
- One short: `"closing": false` turns it off, `"closing": {...}` changes image, seconds, voice_line or website.
- After turning it on or changing the voice line: `CLI narrate SHORT` (it makes only the missing take), then
  `CLI mix SHORT`, then the edit (the edit refuses a mix made without the card). The rendered shots stay current.

### The end card

- The `end_card` setting is the line above the @handle for every short; with no handle there is no end card.
- A short can have its own end card in `short.json`: `"end_card": "Play with it"` replaces the line, and
  `"end_card": {"text": "Play with it", "second": "example.com"}` adds a second, smaller line under the handle (the
  user's own site or call to action). A short's own end card shows even without a handle (its `text` is then the big
  line). Use it for a brief that asks for a particular end card, instead of changing the global setting for one
  short. `CLI validate` checks its shape.
- Check every character of the end-card lines exists in the caption font (an arrow "→" often does not): prefer
  words and a colon ("Play with it: example.com").

## Building a look

A look is a few decisions held consistently across every shot:

- **Light and mood:** time of day, key light direction and colour, how dark the shadows go. Examples: "storm dusk"
  (cool haze outside, warm practical lights on the hero inside), "workshop macro" (warm key, shallow focus, dark
  background), "clean lab" (soft white light, pale background, saturated accent).
- **Palette:** a background tone, one accent colour for what matters (the hero, the key label, an arrow), a neutral
  for everything else. Use the accent sparingly; it should point the eye.
- **Materials:** pick a material family and stick to it (brushed metal and glass, painted toy-like plastic, matte
  clay). Real materials have roughness variation and micro-bump: `look.material(..., bump_scale=...)`, or downloaded
  CC0 textures (`assets.md`).
- **Typography:** one clean sans font for captions and labels (`style.font`; check it with `CLI fonts "NAME"` when writing the story: a
  missing font falls back to one that ships with the OS (Segoe UI or Arial on Windows, Helvetica Neue or Helvetica on
  macOS, DejaVu Sans or Liberation Sans on Linux), then to Blender's own, and the edit says which it used). Caption size 64 px by default.
  See "Checking the font" below before the story page.
- **World:** an HDRI (CC0, downloaded only after a yes) or a simple gradient world; never a pure black void unless
  that is the look.
- **A plain backdrop in an exact colour** (a brand's paper, say): never set the world to the hex and hope. Under AgX
  a world of #F3F2EE at strength 1 renders about #C5C5C3, grey, and saturated colours wash out. Use
  `bsa.blender.look.paper_world(scene, "#F3F2EE", light=1.0)` after `setup_scene`: it solves the colour the camera
  sees through the scene's view transform, look and exposure (Blender's own colour management), and lights the
  scene separately with the plain colour times `light`. It returns the colour it reached and its `delta_e`; over
  2 the view can't reach it, and the result has a `warning` (also printed) naming the colour it shows instead.
  Read it in build.py's result and tell the user then (change exposure= or look=, or accept that colour), not when
  the stills check finds the background off. Put the backdrop colour in `style.palette.background`
  (or the brand's `brand.background`): `BLENDER stills SHORT` then warns when a still's background is more than
  ΔE 5 from it.
- **Brand view:** `brand.exposure` (stops, e.g. `0.3`) and `brand.look` (e.g. `Punchy`) in the settings set the
  view for every brand short: `paper_world` applies them, and `look.brand_view(scene)` does on its own. A fresh
  short never gets them.

## Checking the font

Two things go wrong with a brand font, and both are caught before anything renders:

- **Variable fonts draw the wrong weight.** A family installed only as a variable font (one file such as
  `Fredoka-VF.ttf` or `Inter[wght].ttf`) loads in Blender as its default instance, which is often Light: captions and
  3D text come out thin. `CLI fonts "NAME"` reports `variable`, the `default_weight` and `default_instance` Blender
  will use, a `warning`, and any installed `static_weights` of the family. Use a static file instead: set
  `style.font` (and `bpy.data.fonts.load` in `build.py`) to an installed static weight's path, or, if none is
  installed, offer to download one: `CLI assets plan SHORT "font:Fredoka@600"` plans a static weight from Google
  Fonts (its licence is checked: OFL, Apache 2.0 or Ubuntu Font Licence), shown and approved like any asset
  (`assets.md`); after the yes, `assets download SHORT` puts it in `<workspace>/.assets-cache/fonts/` and you set
  `style.font` to that path. Don't thicken thin text with `curve.offset` instead.
- **Missing glyphs draw as boxes.** Every character of the captions, labels, badges (upper case, as drawn) and the
  end card (its line and the @handle) is checked against the style font's character map. `CLI validate SHORT` (and
  `CLI fonts "NAME" --short SHORT`, or `--text "..."` for any text) warns with the character, its code point, where
  it is and the font the edit will use instead. The edit draws a text that has a missing glyph in the first
  installed OS font that has every glyph it needs (the OS fallbacks above, then symbol fonts such as Segoe UI Symbol
  or Apple Symbols), and its result lists each such text under `warnings`. A text strip has one font, so that whole
  caption or label switches font: prefer rewording ("Play with it: site.com" rather than "→") or a font that has the
  character, so the brand font stays on every line. When no installed font has the character, the warning says so:
  change the text.

## Things that make a scene look great (from the reference shorts)

- Bevel every hard edge; smooth-shade curved surfaces; add surface detail at the scale the camera sees.
- Warm practical lights on the hero against a cooler surround separate the subject.
- Depth: haze or volumetrics outdoors, depth of field on macro shots, foreground framing.
- A person silhouette or a familiar object for scale when size matters.
- Overlays (arrows, plots) in the accent colour with a clear meaning, explained once ("cyan = which way the piston
  moves, gold = the force on the crank").
