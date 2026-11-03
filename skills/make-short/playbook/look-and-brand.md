# Look and brand

## Brand or fresh style

`settings` `brand.mode`:

- **`brand`**: the user's look for every short, stored under `brand` (palette, font, caption style, material
  feel). Copy only `font`, `palette.background` and `caption.size` into each short's `short.json` `style` and
  follow the brand in the build (the kit is read from the settings, so nothing else is copied). Do not offer
  style options.
- **`fresh`**: suggest 2 to 3 looks that fit each topic on the story page, each with a rendered thumbnail of the
  hook shot (`02-story.md`); the user picks one.

### Your brand kit

The kit is the structured part of `brand`. It is set up once (`00-first-run.md` section 4) and every brand short
uses it. Cards turn on only with **`brand.style`**: without it every short keeps today's text captions, badges and
labels, and the kit-only fields below only warn. Check the stored kit any time with `CLI settings check`; it names
any bad value and says how to fix it. A bad value is ignored (the plain value is used), never a crash.

**Keys** (set with `settings set brand.KEY VALUE`; numbers and `true`/`false` are read as JSON):

| Key | Values |
|---|---|
| `style` | `realistic`, `toy`, `toon`, `clay`, `blueprint`, `paper`, `pencil` (`3d` is read as realistic, `flat` as toon) |
| `palette` | hex colours: `background`, `ink` (outlines and shadows), `card`, `text`, `accent`, `highlight` |
| `categories` | a name and a hex for each kind of part, e.g. `{"spark": "#D9483B", "flow": "#3B8ED9"}` |
| `outline_px`, `shadow_px`, `corner_px`, `pill_corner_px` | whole pixels: 0 to 12, 0 to 24, 0 to 80, 0 to 80 |
| `font` | an installed font name, or `Name@600` for a static weight (see "Checking the font") |
| `caption` | `color`, `box` (hex; 8 digits keep alpha), `size` 32 to 120, `opacity` 0 to 1, `corner_px` 0 to 80, `pad_px` 0 to 60, `draw` true or false |
| `logo` | `{"file": "C:/path/logo.png", "place": "top-left" or "top-right", "name": "up to 36 characters"}` |
| `progress` | true or false |
| `set` | `auto`, `workbench`, `graph`, `outdoor`, `studio`, `none` |

The style is saved now and checked as a name only. The seven names are realistic, toy, toon, clay, blueprint, paper,
pencil. How a style draws the scene is added in a later version: do not promise a style's look unless your brief
says it is built. `brand.material`, `brand.cards` and `brand.handle_place` are notes Claude reads; the cards use
the kit's values, not those notes. Old flat keys still work: a hex under `brand.palette` that is not one of the six
roles, and `brand.background`, are read as category colours and `palette.background` once `brand.style` is set
(`settings check` says how to move them). The render engine is never part of the kit: it is always the user's choice.

**The cards.** With a kit the edit draws eight kinds of card as pictures (no scene render). Each comes from a field
of the shot in `short.json`:

| Card | Field |
|---|---|
| caption bar | the narration line, as today |
| title card | `title_card`: text, a list of 1 or 2 lines, or `{"lines": ["Line one", "Line two"], "highlight": 0}` (`highlight` is the index of the line drawn in the accent colour) |
| honesty pill | `badge` ("Simplified", "Motion exaggerated") |
| category tag | `category`: a name from `brand.categories`, shown at the top right |
| step tag | `step`: `[2, 4]` shows "Step 2 of 4" (whole numbers, 1 to 20) |
| stacked labels | each entry of `labels` without `near`; `{"text": "1.4 m", "kind": "measure"}` is a measure label |
| near labels | each entry of `labels` with `near`: a card by the part, with a pointer line and a dot; the dot takes the colour of the part's `category:<name>` tag (`06-build.md`) |
| value bars | `bars`: `{"items": [{"label": "Short arm", "value": 1, "text": "1x", "category": "spark"}, ...], "verdict": "3x the lift", "max": 3}` (2 to 4 items; `text`, `category`, `verdict` and `max` are optional) |

The **progress bar** runs under the caption bar and fills over the short; `brand.progress false` turns it off.
`brand.caption.draw false` (or `style.caption.draw false` for one short) draws no caption bar at all.
A kit draws at most 6 labels per shot (`CLI validate` and the edit refuse more): move some to another shot.

**`badge` is not `category`.** `badge` is the honesty pill: it says the picture was simplified or exaggerated
(`honesty.md`), so keep it truthful and short. `category` is the kind of thing the shot is about (force, heat...)
and only picks a colour from the kit. A `category` the kit does not define shows in the accent colour and
`CLI validate` names the shot. A title card, category, step or bars on a short without a kit only warn.

**The logo badge.** With `brand.logo.file` the edit, thumbnails and closing card draw a small badge with the
user's logo (and `name`, if they gave one) in `place`. The file must be a PNG: an SVG is refused, so ask for a PNG
export. Only the user's own logo ever shows. If the file is later moved or deleted, no badge is drawn and the
result says so once: ask the user to set `brand.logo.file` again.

**One short that differs.** Put kit keys in the short's own `style` (for example `"style": {"palette":
{"accent": "#2E7D6B"}, "outline_px": 0}`): they win over the brand key by key, and the rest of the kit stays. A short
with `"look": "fresh"` (below) drops the brand kit, so only its own `style` can give it one.

**Preview.** `BLENDER kit-sample --kit '<json>' --out sample.png` draws a sample sheet of a kit before it is
saved (without `--kit` it uses the saved brand). It writes nothing to the settings.

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
- `closing.seconds` is the shortest hold, not a cut-off. When the voice line needs longer, the card holds for the
  line plus a short lead-in (0.2 s) and pause (0.5 s), on purpose, so the line is never cut. The mix, edit and
  `BLENDER preview` results then say so under `closing.note` ("closing card held 4.5 s so the voice line fits ..."), and the final page shows
  the hold: tell the user. To keep the card short, shorten the voice line.
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
- **Lights: `look.lights(scene, target, size)`** (`bsa.blender.look`) sets up a key, a fill and a rim light around
  the scene camera's view, aimed at `target` (a point, or an object: its box's centre); `size` is the subject's
  largest dimension in metres, `strength=` the key's strength (W/m², default 3). Call it again to move or retune the
  same three lights. It returns `{"kind", "distance", "lights"}`: change a light's colour or strength there for
  the mood.
  - **Scale matters.** An area light's light falls off with the square of the distance. A rig scaled up for a big
    subject still stands close to it: three area lights about 50 m from a 97 m tower light its top far more than
    its base, and its flat face shades from light to grey, so it looks dirty or curved. So for subjects larger than
    a few metres (`look.SUN_ABOVE_M`, 3 m) `look.lights` uses sun lamps (no falloff: every part of a big face is lit
    the same, soft 12° shadows); for smaller ones it uses area lights `look.AREA_DISTANCE` (8) sizes away, powered
    so the subject gets the same light. Building lights by hand, keep area lights at least that far, or use suns.
  - A material's edge or facing tone (Layer Weight "Facing") also varies across a big flat face seen in
    perspective: keep it to true grazing angles. The `shading` frame check warns about a strong gradient across a
    hero's large flat face (`07-checks.md`).
- **Palette:** a background tone, one accent colour for what matters (the hero, the key label, an arrow), a neutral
  for everything else. Use the accent sparingly; it should point the eye.
- **Materials:** pick a material family and stick to it (brushed metal and glass, painted toy-like plastic, matte
  clay). Real materials have roughness variation and micro-bump: `look.material(..., bump_scale=...)`, or downloaded
  CC0 textures (`assets.md`).
- **Water in EEVEE:** glass-like water (transmission with an IOR of 1.33) needs ray-traced refraction in EEVEE, and
  that fails behind other transparent parts (a float seen through the tank water and its glass wall comes out
  wrong). For a toy-like EEVEE look use `bsa.blender.look.toy_water(name, colour)`: a tinted, partly see-through
  Principled BSDF (alpha 0.4) with a clear coat, blended (`surface_render_method` BLENDED), so the parts inside show
  through tinted and the surface still reads as water. `stream=True` gives falling water more body (alpha 0.7);
  `alpha=` sets any other (about 0.2 for a clear pipe the water runs through). Take the colour from the short's
  palette. Real refraction is for Cycles.
- **Typography:** one clean sans font for captions and labels (`style.font`; check it with `CLI fonts "NAME"` when writing the story: a
  missing font falls back to one that ships with the OS (Segoe UI or Arial on Windows, Helvetica Neue or Helvetica on
  macOS, DejaVu Sans or Liberation Sans on Linux), then to Blender's own, and the edit says which it used). Caption size 64 px by default.
  See "Checking the font" below before the story page.
- **Caption colours:** `style.caption` (or `brand.caption`) `color` and `box` as hex. A 6-digit hex (`#14161A`) is
  opaque; an 8-digit one keeps its alpha (`#14161A99` is 60 % ink). With no `box`, captions sit on the default dark
  see-through box (55 % alpha).
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
  ΔE 5 from it. It checks only shots whose world is a flat backdrop (`paper_world` or a plain colour). A sky, an
  HDRI or a gradient world is an environment, not a backdrop: those shots get no background warning.
- **Brand view:** `brand.exposure` (stops, e.g. `0.3`) and `brand.look` (e.g. `Punchy`) in the settings set the
  view for every brand short: `paper_world` applies them, and `look.brand_view(scene)` does on its own. A fresh
  short never gets them.

## Checking the font

Two things go wrong with a brand font, and both are caught before anything renders:

- **Variable fonts draw the wrong weight.** A family installed only as a variable font (one file such as
  `Fredoka-VF.ttf` or `Inter[wght].ttf`) loads in Blender as its default instance, which is often Light: captions and
  3D text come out thin. `CLI fonts "NAME"` reports `variable`, the `default_weight` and `default_instance` Blender
  will use, a `warning`, and any installed `static_weights` of the family. Use a static weight instead: set
  `brand.font` and `style.font` to the family with its weight, `"Fredoka@600"` (a file path still works too). It
  loads an installed static file of exactly that weight, else the one `CLI assets` downloaded into
  `<workspace>/.assets-cache/fonts/` (`Fredoka-600.ttf`). `CLI fonts "Fredoka@600"` says which file that is; when
  neither exists it warns (and `CLI validate` and the edit's `font_fallback` say so) and the text uses the family as
  installed. Then offer to download it: `CLI assets plan SHORT "font:Fredoka@600"` plans a static weight from Google
  Fonts (its licence is checked: OFL, Apache 2.0 or Ubuntu Font Licence), shown and approved like any asset
  (`assets.md`); after the yes, `assets download SHORT` puts it in the cache, where `"Fredoka@600"` finds it for
  every short of the workspace. In `build.py`, load the same file for 3D text: `bsa.fonts.resolve("Fredoka@600",
  cache_dirs=bsa.fonts.font_caches(SHORT_DIR))[0]`. Don't thicken thin text with `curve.offset` instead. For a brand, this is checked once,
  at setup: `settings set brand.font NAME` returns a `font_check` with the same facts and a `warning` that says how
  to fix it in the brand (set `brand.font` to a static weight, `"Fredoka@600"` or a file path, then every brand
  short uses it). While a brand short still uses a variable `brand.font`, `CLI validate` says so in one short line
  that points at the setting, rather than repeating the full advice on every short.
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
- A familiar object for scale when size matters: a door, a car, a bench, a standard shipping container. Under a
  `no_humans` rule people never appear, not even as a silhouette or a stick figure.
- Overlays (arrows, plots) in the accent colour with a clear meaning, explained once ("cyan = which way the piston
  moves, gold = the force on the crank").
