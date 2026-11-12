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
   licence (always CC0), source and the kinds its file name suggests. A pack shows the kinds its clips are tagged
   with: from the library when it is already cached (`kinds_from: library`), else from the catalogue. A source that could not be reached (no
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
`dur_s` is how long it may last; `level_db` is how loud it seems, in LUFS-ish (perceived loudness, close to dBFS RMS
for a steady sound): about -18 for a feature sound, -24 to -30 for a bed. It sets the balance between effects, so a
sharp knock and a steady rumble at the same `level_db` seem equally loud. Kinds:

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
| `water` | water lapping, flowing or splashing, rain | yes |
| `room` | room tone: the quiet interior ambience under the whole short (not an event, see below) | yes |
| `whoosh` | camera moves, something passing fast | synthetic |
| `blip` | a label or number popping in (a designed tone) | synthetic |
| `hum` | motors, electrics (a designed tone) | synthetic |

Place effects where things happen on screen: the click when a latch closes, the thud when the crate lands, the
scrape while the weight drags. Fewer, well-timed sounds beat a busy bed. Labels popping in usually need no sound.

## How an effect gets its sound

`CLI mix SHORT` plays, for each event, a cached recording of its kind: variants rotate so two events in a row never
use the same clip, and each is trimmed of leading silence and given a small random pitch (about half a semitone) and
gain (±1.5 dB) change so repeats don't sound copied. A kind with no recording falls back to synthesis. Then it is
shaped by kind:

- **Transients** (`impact`, `thud`, `click`, `blip`): a 3 ms fade-in so there is no click, and the sound is not
  chopped at `dur_s`. Its own tail rings on for up to 0.6 s more, fading smoothly.
- **Sustained sounds** (`rumble`, `wind`, `water`, `hiss`, `hum`, `creak`, `scrape`, `slide`, `whoosh`): cut to
  `dur_s`, fading in over 0.3 s and out over 0.5 s (each at most a third of `dur_s`, so short ones still work).

Last, each event is levelled by perceived loudness: the loudest 400 ms of the K-weighted signal (ITU-R BS.1770, the
LUFS meter) is set to `level_db`. Peak level and RMS no longer decide it.

The mix result's `sfx` says what happened: `recorded`, `synthesised` and `skipped` counts, the clips used with their
source and licence, a `room` entry, and `notes` naming any kind that was synthesised or a missing room tone. Treat a
synthesised effect as a gap to fill with a recording (search, plan, yes, download), not as finished.

## Room tone: the floor

Real interiors never go silent. Without a floor the mix drops to digital silence between effects and in pauses, then
jumps back: it sounds abrupt. So `CLI mix SHORT` loops a `room` recording under the whole story (a seamless loop with
1 s crossfades from a random start, faded in and out over 0.5 s). It is the floor: the voice never ducks it. It
stops with the story; the closing card stays silent, as it brings no sound of its own.

Get one like any recording: `CLI assets search sounds "room tone"` (also try "interior quiet", "ambience"), plan it,
get the user's yes, download. A file name with room tone, roomtone or interior quiet is tagged `room`, and so is a
Commons or Freesound name with ambience or ambient that suggests no other kind (clips in curated packs are tagged by
the pack); tag a clip yourself with `commons:FILE=room` or `freesound:ID=room`. Pick a quiet, steady
recording with no voices, music or one-off sounds, at least a few seconds long. Room tone is a recording only (never
synthesised) and natural-only allows it. `CLI validate SHORT` warns when none is cached, and the mix skips it.

Settings: `sound.room_db` (default -50, LUFS-ish; `CLI settings set sound.room_db null` turns the floor off; the range
is -80 to -20) and `sound.reverb`.

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
For water and wind, plan `pack:oga-water` and `pack:oga-wind`: they need no Commons and no key.
For machines, plan `pack:oga-sfx-100-v2` (2.4 MB: machine loops and thunder as `rumble`, air as `hiss`, metal hits
as `impact`, doors, locks and water loops) and `pack:oga-steam-hisses` (0.6 MB: five steam hisses, all `hiss`); `CLI assets plan
SHORT pack:oga-steam-hisses` lists each with its size, and nothing downloads without the user's yes. Neither pack has a
clip named for a thud, so for a piston or firing use its `impact` clips or a recording. Their pages do not say whether
the sounds were recorded or synthesised, so they are not marked `designed` and natural-only accepts them: say so when
the user wants natural sounds only. Placing them: a `hiss` event on the frame a valve opens, an `impact` (or `thud`
where a clip is tagged for it) on each firing, written by `build.py` into `source/events.json`, one per power stroke,
and a `rumble` bed under a running engine at about -26 dB.
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

### Time warps

When time is sped up or slowed, sound must stay on the mechanism's real beats. Drive the mechanism from its own
clock (the time warp maps short time to mechanism time), and write events from the build at the mechanism's real
beats, mapped back through the warp to short frames. Never place sounds by eye on the warped timeline. When time is
sped up, beats crowd together: skip beats closer than a set rate (for example 12 per second), or the ticks smear into
a buzz. Tag a part `fast` only for real sub-frame motion (`07-checks.md`).

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
SHORT` adds the bed to the effects bus as given (no loudness levelling, so its level relative to the `sfx` events is
what you give it), before the bus is normalised.

## The mix

`CLI mix SHORT` writes `audio/mix.wav`:

1. **Effects bus.** All effects (the `sfx` events, `events.json` and the `sound.py` bed) go through a 75 Hz high-pass,
   so rumbles don't muddy the voice, and one short room reverb (about 0.35 s, wet about 16 dB under the dry sound), so
   they sit in the same space. The voice and the room tone stay dry. `sound.reverb` false turns the reverb off. The
   `sound.py` bed is part of the bus: it gets the high-pass and the reverb too.
2. **Levels.** The bus is set to -23 dBFS RMS where it sounds (a few short effects are not boosted to fill the
   silence between them); the voice to -15 dBFS while speaking.
3. **Ducking.** The bus drops 10 dB under speech. It starts to dip 60 ms before a line begins, so the effects are
   already down when the voice arrives, and comes back over about 0.4 s.
4. **Room tone** (above) is added last and is not ducked.
5. **Limiter.** A transparent lookahead peak limiter (5 ms lookahead, 100 ms release) holds the sample peak at
   -1 dBFS. Nothing under that is touched.

The `levels` it returns are measured on the finished mix (`06-build.md`). Captions-only shorts get the effects and
the room tone alone. Re-run the mix whenever the timing, the narration, the effects or the cached recordings change
(the build stage does it last).
