# Free voices

blender-shorts-agent can narrate with three free voice models that run on your own computer. Nothing here is
downloaded until you say yes to a list of what will download, where from, its licence and its size.

| Model | Licence | Download | Needs | Speed per line |
|---|---|---|---|---|
| `kokoro` (Kokoro-82M) | Apache 2.0 | ~0.34 GB | any computer (NVIDIA GPU optional, ~1 GB free) | ~0.1 s on a GPU, ~1–2 s on a CPU |
| `chatterbox` | MIT | ~3.2 GB | NVIDIA GPU with ~4 GB free | ~3–8 s |
| `chatterbox-turbo` | MIT | ~3.0 GB | NVIDIA GPU with ~3.5 GB free | ~2–5 s |
| `whisper-small.en` (optional take check) | MIT | ~1 GB | NVIDIA GPU with ~1 GB free, or the CPU | — |

Weights come from the official Hugging Face repos: `hexgrad/Kokoro-82M`, `ResembleAI/chatterbox`,
`ResembleAI/chatterbox-turbo` and `openai/whisper-small.en`, each pinned to a fixed commit, and every file is checked
against its published SHA-256 after downloading. Chatterbox adds Resemble AI's inaudible watermark to everything it
says.

Each engine also needs its own Python environment with PyTorch, which is larger than the weights: about 4.8 GB for
Kokoro, 5.2 GB for Chatterbox (shared by Turbo), 4.7 GB for Whisper and 0.15 GB for the runner, plus Python 3.12
(~65 MB) if uv has to fetch it. These are sizes on disk measured on Windows with CUDA 12.8. The list you approve
before installing shows them next to the weights.

## Where things go

Everything is installed under `~/.blender-shorts-agent/voices/` (or `$BSA_HOME/voices/`):

- `runner/`: the `bsa-tts` command (a small uv project).
- `engines/kokoro`, `engines/chatterbox`, `engines/whisper`: one uv environment per engine, with pinned versions.
  Chatterbox Turbo shares the Chatterbox environment.
- `weights/<model>/`: the downloaded weights. Once installed, everything runs offline.
- `refs/`: the clips Chatterbox copies voices from.

The installer needs [uv](https://astral.sh/uv). If it's missing you'll be told the one-line command to install it.

## Voices

- **Kokoro** has built-in voices, for example `am_michael`, `bm_george`, `af_heart` and `bf_emma`
  (`a` = American, `b` = British, `f`/`m` = female/male).
- **Chatterbox** and **Chatterbox Turbo** have `default` (the model's own voice) plus `michael`, `george`, `heart`
  and `emma`. Those four are copied from short clips that Kokoro reads on your computer at install time, so no real
  person's voice is used. (Kokoro is installed first for this if you don't have it.) The accent only partly carries
  over.
- **`me`**: your own voice, for Chatterbox and Chatterbox Turbo. See below.

List them all with:

```
uv run --project ~/.blender-shorts-agent/voices/runner bsa-tts voices
```

## Recording your own voice (`me.wav`)

1. **Room:** a quiet, soft room (carpet, curtains; a wardrobe full of clothes is great). No fan, air conditioning
   or music.
2. **Mic:** any decent USB mic or headset, 15–20 cm from your mouth and slightly to the side to avoid pops. A phone
   voice-memo app is fine.
3. **Delivery:** read for 10–20 seconds **in the style you want back**: calm documentary narration at your normal
   pace. Chatterbox copies the energy and pacing of the clip as well as the voice. For example: *"Every great
   structure begins with a simple question. How do you keep something standing when the world around it refuses to
   stay still? Engineers answer with patience, careful measurement, and a little imagination."*
4. **Clean take:** one continuous take, no long pauses, no clipping (peaks around −6 dB), half a second of silence at
   each end. Don't add music, reverb or heavy noise reduction.
5. **Save** it as a mono WAV (24 kHz or higher; 44.1 or 48 kHz is fine) named `me.wav` in
   `~/.blender-shorts-agent/voices/refs/`. In Audacity: Tracks → Mix → Mix Stereo Down to Mono, then File → Export
   Audio → WAV.
6. Pick the voice `me` with Chatterbox or Chatterbox Turbo. If it sounds rushed, record another take a little slower.

## The `bsa-tts` command

```
bsa-tts speak --model kokoro|chatterbox|chatterbox-turbo --voice NAME --lines LINES.json --out DIR
              [--speed 0.5-2.0] [--seed INT] [--cpu] [--cpu-fallback]
bsa-tts voices [--model NAME]
bsa-tts check
bsa-tts verify --lines LINES.json --takes DIR
```

`speak` writes `<id>.wav` (48 kHz mono 16-bit), `durations.json` and `render.json`. The last line it prints is a JSON
summary. `--speed` is native for Kokoro and a time-stretch afterwards for Chatterbox (1.0 sounds best). The same
text, voice and seed give the same take. `--cpu-fallback` uses the CPU when the GPU is busy (Kokoro only).
`check` prints free video memory and which models are installed. `verify` transcribes each take with Whisper and
prints the word error rate per line.

Exit codes: `0` ok, `1` error, `2` bad arguments or lines file, `3` GPU busy before starting (the message gives the
free and needed MB), `4` the GPU ran out of memory, `5` not installed. The runner never stops other programs: if the
GPU is busy, close what's using it or choose Kokoro.
