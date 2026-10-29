# 00 First run: settings

Settings live in one file the plugin owns (`settings_path` from `CLI where`, normally
`~/.blender-shorts-agent/settings.json`, or `$BSA_HOME/settings.json`). They survive plugin updates. Ask only for
what `CLI settings missing` lists (any of `workspace`, `narrator.provider` (also listed when `custom` has no command yet), `render.engine`, `brand.mode`, `rules`, `handle`),
one question at a time, in plain words, and save each answer straight away with `CLI settings set KEY VALUE`. Text
settings (workspace, handle, voice, engine, brand mode and brand text) are stored as typed; numbers such as
`narrator.speed 1.2` and brand objects such as `brand.palette '{"accent": "#E8B33A"}'` are read as JSON.

## 1. Workspace: where shorts are saved

`CLI settings missing` also returns `default_workspace` (a git-ignored `shorts/` folder when Claude runs inside a
clone of the plugin repo, otherwise `~/Videos/Shorts` or `~/Shorts`). Suggest it:

> "I'll save your shorts in `C:\Users\you\Videos\Shorts`, one folder per short. OK, or somewhere else?"

Then `CLI settings set workspace "<path>"` (relative paths are made absolute; the folder and its `.gitignore` are
created). Tell the user where it is and how to change it: "Say 'save my shorts somewhere else' any time." Offer to
remember the workspace in your memory too ("Want me to note that in my memory as well?"); the plugin itself always
reads the settings file.

## 2. Narrator

Ask exactly this first: **"Do you already have a voice tool, or would you like me to set one up?"** Then:

- **Use what I already have** -> `narrator.provider custom` with a saved command (below).
- **Set one up for me** -> offer all of these; never hide one. Run `CLI voice suggest` first: it checks the machine
  and says which free voice fits.
  - **ElevenLabs**: paid, the best quality, needs an account and an API key in the `ELEVENLABS_API_KEY` environment
    variable (the user sets it; never ask them to paste the key into chat). `CLI voice list elevenlabs` lists
    voices. Settings: `narrator.provider elevenlabs`, `narrator.voice <voice_id>`.
  - **Kokoro** (Apache 2.0): free, local, runs on any computer (CPU is fine), clear but a little flat.
  - **Chatterbox** (MIT): free, local, the most natural voice; needs an NVIDIA GPU with about 4 GB of free video
    memory.
  - **Chatterbox Turbo** (MIT): free, local, faster, nearly as good; about 3.5 GB of free video memory.
- **Captions only**: no voice. `narrator.provider none`.

`voice suggest` returns `suggested` (Chatterbox when an NVIDIA GPU has about 4 GB free, else Kokoro) and, per model,
`fits` with the reason. Do not recommend models with non-commercial licences (XTTS-v2, Fish Audio): they are not
offered.

For a free voice: show `CLI voice plan MODEL` (each download, its source, licence and size, plus the total) and
wait for a yes. Only then `CLI voice install MODEL` (it needs `uv`; if it is missing, the error says how to install
it, and the user decides). Then let them hear it: `CLI voice list MODEL` (voices marked `narrator: true` are the
good ones), `CLI voice sample MODEL VOICE` (gives a WAV path to play). Save `narrator.provider`, `narrator.voice`,
and optionally `narrator.speed` (1.0 default; 1.1 to 1.2 suits shorts).

### Their own voice tool (`custom`)

Ask what the tool is and how they run it today (a command they type, a Python script, an account with an API key).
Turn that into **one command line** with these placeholders, and save it with
`CLI settings set narrator.command "..."` plus `CLI settings set narrator.provider custom`:

- `{lines}`: a JSON file the plugin writes, `[{"id": "l01", "text": "..."}, ...]`
- `{out}`: the folder where the tool must write `<id>.wav` for every line (`l01.wav`, `l02.wav`, ...)
- `{seed}`: a number the tool may use to vary a take; `{voice}` and `{speed}` (optional) are filled from
  `narrator.voice` and `narrator.speed`

Example for a local command-line tool:

```
python -m tts speak --model M --voice V --lines {lines} --out {out} --seed {seed}
```

Another example: an ElevenLabs account through a tiny adapter that reads the key from the `ELEVENLABS_API_KEY`
environment variable (see "Adapters" in `playbook/05-narration.md`).

The command is run without a shell, so quote paths that contain spaces, and use a full path to the program if it is
not on PATH. Any WAV is fine (any sample rate, stereo or mono, 16-bit, 24-bit or float): the plugin converts each
take to 48 kHz mono 16-bit itself. MP3 or other formats are not accepted: write an adapter. Never put an API key in
the command; tools read keys from an environment variable the user sets.

Before saving for good, run `CLI voice test-custom` (it renders one test sentence into a temporary folder and gives
a `path` and `seconds`) and let the user listen. If it fails, tell them the one-line error in plain words and fix
the command or write an adapter. Only keep `custom` once they like what they hear (otherwise go back to the
options above or `narrator.provider none`). When you keep it, also save a short name for it that the settings
summary shows, the tool and the voice in a few words: `CLI settings set narrator.label "mytts · narrator"`
(without a label the summary names the model and voice from the command, or the script it runs).

If anything here fails or the user would rather not now: `narrator.provider none`, say once that a voice can be
added later, and move on. Never let narrator setup stop the short.

## 3. Render preference

"Should I ask which render engine to use for each short (I'll estimate both times), or always use one?" Save
`render.engine` as `cycles` or `eevee` for "always", or `ask-always` for "ask me each time" (that counts as answered,
so this question is not repeated; the engine stage then estimates and asks for every short).

## 4. Brand or a fresh style

"Do you have a brand look for every short, or should I suggest a fresh style each time?"

- **Fresh style**: `brand.mode fresh`. Each story page then shows 2 to 3 style options (`playbook/02-story.md`).
- **Brand**: `brand.mode brand`, then collect the essentials into `brand.*`: `brand.palette` (accent and background
  colours as hex), `brand.font` (an installed font name: check it with `CLI fonts "NAME"`), `brand.caption` (a JSON object: `size`, `color` and `box` as hex, e.g.
  `'{"color": "#FFD000", "box": "#000000"}'`; captions are white on a dark box when unset), `brand.material` (overall
  feel: matte clay, brushed metal, glossy toy...), optionally `brand.exposure` (stops, e.g. `0.3`) and `brand.look`
  (e.g. `Punchy`) for the view (a plain paper backdrop is set with `look.paper_world`, `look-and-brand.md`), and where their handle or logo should sit: `brand.handle_place`
  (the handle goes on the end card after the last line, in the upper third of the safe area unless a hero is there; a logo, if they have one, is built into the scene as an
  object or a sign, never over the captions or the platform buttons). See `playbook/look-and-brand.md`.

## 5. Hard rules

`rules` is listed until the user has answered this once (existing users too, at their next short). Ask exactly:

> "Any hard rules every video must follow? (for example: no music, no people, no animals, a particular dress code or content limit) — I'll follow them in every short and check what I can."

The rules are theirs, for any belief, taste or brand: store each one **in the user's own words**, never reworded,
softened or widened, and never comment on why they hold it.

- **One concrete rule each:** `CLI settings rule add "No people, no hands, no stick figures" --kind no_humans`.
  Add `--kind` only when it matches what they said; the plugin then checks it too (`CLI settings rules` lists the
  kinds): `no_music` (always on anyway: shorts never have music), `no_humans` (no people, body parts, faces,
  silhouettes or stick figures), `no_animals`, `no_faces`, `no_text_on_screen_except_captions`,
  `natural_sounds_only` (recorded effects only: it forces `sound_policy natural-only`), `no_brand_logos`. A rule
  with no matching kind is saved without one; you keep it yourself.
- **A broad rule** (a religious or ethical standard, "family friendly", "nothing my school would object to"): save
  it exactly as said, with no kind. Then ask the user to spell out what it means **for their videos**, as concrete
  do's and don'ts: "So I get this right for your videos: what should I always do or never show to keep that?" Save
  each answer as its own rule with `--part-of <the broad rule's id>` (and a `--kind` where one matches). **Never
  guess** what a broad rule means, never fill in details from your own knowledge of a religion, culture or
  tradition, and never describe it back to them. When a later short raises a case their list does not cover, ask.
- **No rules:** `CLI settings rule none` (recorded, not asked again).

Then say where they live and how to change them: "I've saved these in your settings
(`settings_path` from `CLI where`, normally `~/.blender-shorts-agent/settings.json`); they apply to every short. Say 'add a hard rule' or 'drop the rule about
...' any time" (`settings rule add` / `settings rule remove ID`; `settings rules` lists them). A single short can
add rules of its own (`rules` in its short.json, `02-story.md`), never remove or loosen these.

## 6. Handle

"Do you want your handle on the end card (for example @yourname), or nothing?" Save `handle` (the `@` is
optional), or `CLI settings set handle ""` for no handle (that records the answer, so it is not asked again). The user's own handle (and, per short, their own end-card
line or site) is the only branding a video carries by default. There is no credit to this plugin or its author
(goinsidemachines) unless the user is that brand or asks for it.

If they gave a handle, ask right after: **"Add a line above your handle on the end card, like 'Follow for more'?"**
Save their words with `CLI settings set end_card "Follow for more"` (that is the default), or `CLI settings set
end_card ""` for the handle alone. The end card shows from the frame after the last line to the end (the timeline
holds the last shot 1.5 s longer for it when there is a handle), the line smaller above `@handle`. One short can
have its own end card (`"end_card"` in its short.json, with an optional second line such as their site:
`look-and-brand.md`), so a brief that wants a particular end card never needs this setting changed and restored.

## 7. Closing card

Shorts viewers can't tap links in the description, so a site has to be shown and said in the video. Ask once:
**"Do you have a website or channel link to show at the end of each short?"** and, if they do, **"Do you have your
own end image (1080x1920), or should I make a simple one?"**

- Their site: `CLI settings set website example.com`.
- Their own image: `CLI settings set closing.image "C:/path/card.png"` (PNG or JPG, best 1080x1920; another size is
  scaled to fill and warned). No image: leave it unset and the plugin makes a card from their brand (background, font,
  accent): "More at", the website in a pill, and the @handle.
- Offer a line said over the card in the narrator's voice, in their words, e.g. `CLI settings set closing.voice_line
  "Play with it yourself at example dot com."` (spell the site the way it should be spoken).
- How long it holds: `CLI settings set closing.seconds 2.5` (1.5 to 5; it holds longer if the line needs it).
- Then `CLI settings set closing_asked true`. No site and no image: just `closing_asked true` (no closing card; a
  handle alone keeps the overlay end card above).

With a closing card the overlay end card is not shown (the card says it once). The card comes after the story's last
line, adds no music or sound effects, and has no caption over it. One short can switch it off (`"closing": false` in
its short.json) or change it (`"closing": {"image": null, "voice_line": "...", "seconds": 3, "website": "..."}`). The
settings, image and site stay in the user's own settings folder, never in a short's repo or the plugin.

## Plugin feedback (no question)

Don't ask about this. Add one line to the summary at the end of the first run: "If I hit a problem in the plugin
itself I'll note it and, at the end, ask whether you want to send it to its authors. Change with settings set
feedback never|off" (`never`: still note, never ask; `off`: don't note). Nothing is sent unless they choose to.

## Changing a setting later

The user changes any setting by saying so ("use Cycles from now on", "switch to Kokoro"). Save it with `settings
set` and show the new `settings summary`. Hard rules change only with `settings rule add|remove` (or `rule none`),
and only when the user asks.
