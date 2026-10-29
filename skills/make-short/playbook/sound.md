# Sound

**No music, ever.** Every short gets sound effects that match what is on screen, plus the narration when there is
one. **Use real recordings.** Synthesised effects (made by code) read as fake next to a real voice: viewers rejected
a whole cut over them. Recordings come from CC0 sources (free for any use, no credit required), are downloaded only
after the user's yes, and are reused by every later short in the workspace. Synthesis is only the fallback.

## Get recordings first (once per workspace, then as needed)

When writing the `sfx` list, check which kinds have recordings: `CLI validate SHORT` warns "no recording is cached
for sfx kind(s) ...". For those kinds:

1. `CLI assets search sounds "<words>"` (for example `"wood thud"`, `"metal scrape"`, `"switch click"`). Results
   come from curated CC0 packs (`pack:...`, the cheapest start: one download covers many kinds), Wikimedia Commons
   (`commons:...`) and, when the user has a Freesound API key, Freesound (`freesound:...`). Each result shows its
   licence (always CC0), source and the kinds its file name suggests. A source that could not be reached (no
   connection, DNS) is listed in `skipped` with a plain error ("can't reach commons.wikimedia.org (network or
   DNS)") and a retry hint, and the note says the other sources only were searched: tell the user which source was
   left out and that the same search may find more in a minute; never present the results as everything there is.
2. `CLI assets plan SHORT pack:oga-metal-wood "commons:Wood thud.ogg=thud" freesound:12345=scrape,slide`. After
   `=` you may set the kinds a single clip is used for; pack clips are tagged from their file names. An item whose
   source fails is in `skipped` (and named under the table), not silently dropped: say so and offer to plan it again.
   A pack already in the cache, in `.assets-cache/sounds/` or an earlier version's `.assets-cache/sfx/<folder>/`
   (copied in), shows as "already in the cache" and counts 0 MB: no new download to ask about, but `assets
   download` still adds it to the library and the short's credits. `CLI validate` and `CLI mix` also index packs
   left in the old `sfx/<folder>/` layout (its `pack.zip`) by themselves, once and with no network: their clips are
   in the library before you search.
3. Show the plan's `table` and ask "OK to download these (X MB)?" (`assets.md`). Only a clear yes downloads:
   `CLI assets download SHORT`. Clips land in `<workspace>/.assets-cache/sounds/` and in its library
   (`sounds/library.json`: each clip's kinds, source, author, page and licence); the short's `assets` list gets the
   credits.

Freesound needs a free API key (freesound.org/apiv2/apply). If the user wants it:
`CLI settings set freesound.api_key KEY` (or `$FREESOUND_API_KEY`). Never ask for it otherwise; packs and Commons need
no key.

## Effects in short.json

Each entry in `sfx`: `{"kind", "shot", "at_s", "dur_s", "level_db"}`. `at_s` is seconds from the start of the shot;
`dur_s` is how long it may last (a longer recording is cut with a short fade); `level_db` sets the balance between
effects (about -18 for a feature sound, -24 to -30 for a bed). Kinds:

| kind | Use it for | natural |
|---|---|---|
| `impact` | a hit, a bump, a hammer blow | yes |
| `thud` | something heavy landing or dropped, a dull knock | yes |
| `click` | a latch, a switch, a ratchet notch, a key in a lock | yes |
| `scrape` | something dragged with friction: metal on stone, a crate on concrete | yes |
| `slide` | a smooth slide: a drawer, a sliding weight, a blade drawn | yes |
| `creak` | structures under load, hinges, swinging masses | yes |
| `rumble` | storms, heavy machinery, the ground | yes |
| `hiss` | hydraulics, air, steam | yes |
| `wind` | weather, height, open air | yes |
| `whoosh` | camera moves, something passing fast | synthetic |
| `blip` | a label or number popping in (a designed tone) | synthetic |
| `hum` | motors, electrics (a designed tone) | synthetic |

Place effects where things happen on screen: the click when a latch closes, the thud when the crate lands, the
scrape while the weight drags. Fewer, well-timed sounds beat a busy bed. Labels popping in usually need no sound.

## How an effect gets its sound

`CLI mix SHORT` plays, for each event, a cached recording of its kind: variants rotate so two events in a row never
use the same clip, and each is trimmed of leading silence, peak-normalised and given a small random pitch (about
half a semitone) and gain (±1.5 dB) change so repeats don't sound copied. A kind with no recording falls back to
synthesis. The mix result's `sfx` says what happened: `recorded`, `synthesised` and `skipped` counts, the clips used
with their source and licence, and `notes` naming any kind that was synthesised. Treat a synthesised effect as a gap
to fill with a recording (search, plan, yes, download), not as finished.

## sound_policy: natural-only

A hard rule of kind `natural_sounds_only` (`00-first-run.md`) sets this for every short, and no short can loosen it;
`no_music` only repeats what is always true here. A sound rule with no kind (no sounds at all, say) you keep by hand:
ask what the user wants when it is unclear.

When the user wants "voice and natural sounds only", set `sound_policy` to `natural-only`: for one short in
`short.json` (`"sound_policy": "natural-only"`), or for every short with `CLI settings set sound_policy
natural-only`. Then the synthetic kinds (`blip`, `whoosh`, `hum`) are forbidden and nothing is synthesised: such
events, and natural kinds with no cached recording, are left out of the mix. `CLI validate SHORT` warns about both
before you get that far; fix them by changing the kind or downloading recordings.

Designed UI packs (`kenney-ui-sfx`: interface clicks and switches, tonal and partly synthesised) are flagged
`designed` in search results, under the plan table and in the library. Under natural-only the mix never uses their
clips, even ones tagged `click`: plan a natural pack (`oga-metal-wood`, `kenney-rpg`) or a recording for those kinds.
Each library clip also has a `flatness` score (near 0 tonal, near 1 noisy) to look at; it does not decide picking,
because real knocks ring as well.

## Sounds on simulation events: `source/events.json`

`sfx` `at_s` is typed by hand. When a sound must land on something the build computes (each ratchet notch, each
ball hitting the next, a latch closing at a solved frame), let `build.py` write the events instead, and `CLI mix
SHORT` plays a cached clip of each one's kind:

```python
events = [{"frame": f, "kind": "click", "gain": min(1.0, speed / top_speed)} for f, speed in notches]
(short_dir / "source" / "events.json").write_text(json.dumps(events), encoding="utf-8")
```

Each event: `kind` (an sfx kind), `frame` (the short's 1-based frame) or `t` (seconds from frame 1, or from the start
of `shot` when it is given), and optional `gain` (0..1, default 1: a soft hit plays quieter), `dur_s` (0.4) and
`level_db` (-18). Events of one kind closer than 15 ms are heard as one; ones under gain 0.03 are left out. They
play exactly like `sfx` entries (recordings rotate, natural-only rules apply) and the mix result counts them in
`events`. Write the file in every build, so a change to the simulation moves the sounds with it.

## Custom sound from the simulation: `source/sound.py`

For sound that follows the motion (a click on every ratchet notch, a creak at each swing peak, a scrape that lasts
exactly as long as the drag), write `source/sound.py` defining

```python
def build_bed(short, timeline, short_dir):
    """Return a numpy array at 48 kHz, mono (n,) or stereo (n, 2), lined up with frame 1 of the short."""
```

Load recordings with `bsa.audio.clip`:

```python
from bsa.audio import clip

notch = clip("click", variant=2, short_dir=short_dir)   # the 3rd cached click (a kind), mono float at 48 kHz
crate = clip("impactWood_heavy_001", short_dir=short_dir)  # a cached clip by file name (or its cache path)
mine = clip(r"C:\sounds\drop.wav")                        # any WAV/OGG/FLAC/MP3 file path
```

WAV is read directly; OGG, FLAC and MP3 are decoded with Blender's own audio library (inside Blender directly, else by
a short background Blender run that leaves a decoded `<file>.48k.wav` next to it). Scale and place the clips from
your model's data; the sample for frame `f` is at `round((f - 1) / timeline.fps * 48000)`.

For collisions and other events with an energy (a simulation's impacts), `bsa.audio.place_clips(events, clips,
length_s=..., offset_s=...)` does the placing: one clip per event (a list of clips is used in turn), events closer
than `merge_s` (15 ms) heard as one, gain `(energy / ref_energy) ** 0.5`, quiet ones (under `floor`) left out.
`events` are `(t_s, energy_J)` pairs or the `impacts` of `bsa.fields.physics.contact.contact_chain` as they are;
`offset_s` moves simulation time to the short's (seconds from frame 1). Give every shot the same `ref_energy` so a
soft hit sounds soft everywhere. `bsa.audio.sfx`
(`shaped_noise`, `creak`, ...) is still there for beds no recording fits, never under `natural-only`. `CLI mix
SHORT` adds the bed to the effects before the bed is normalised, so its level relative to the `sfx` events is what
you give it.

## The mix

`CLI mix SHORT` writes `audio/mix.wav`: voice normalised to -15 dBFS while speaking, effects to -23 dBFS where they
sound (a few short effects are not boosted to fill the silence between them) and ducked 8 dB under speech, a soft
limiter, peak at -1 dBFS. The `levels` it returns are measured the same way on the finished mix (`06-build.md`).
Captions-only shorts get the effects alone. Re-run the mix whenever the timing, the narration, the effects or the
cached recordings change (the build stage does it last).
