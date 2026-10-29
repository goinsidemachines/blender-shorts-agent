# Downloaded assets

Shorts may use downloaded textures, HDRIs (sky lighting) and models from Poly Haven, recorded sound effects, and
static font weights. Sounds and Poly Haven assets are all CC0 (free for commercial use, no attribution required);
fonts are OFL, Apache 2.0 or Ubuntu Font Licence (free to use in a video). Nothing is downloaded without the user's
yes to an exact list.

1. **Search:** `CLI assets search textures|hdris|models|sounds "<words>"` (for example `assets search textures
   "brushed metal"`, `assets search sounds "metal scrape"`). Pick what fits the look; prefer modelling simple things
   yourself and downloading what is hard to make well (realistic ground, weathered metal, a sky). For sounds, see
   `sound.md`: real recordings always beat synthesis. Under the user's hard rules, Poly Haven results whose name,
   tags or categories match a rule (people or statues under `no_humans`, animals under `no_animals`, logos under
   `no_brand_logos`) are left out (`hidden_by_rules`), and `assets plan` refuses such an ID. Tags are not
   pictures: look at each preview against every rule (a street HDRI with passers-by) before planning it.
2. **Plan:** `CLI assets plan SHORT ID...`, one call for any mix of:
   - a Poly Haven `ID[="Display name"]` (add `--resolution 1k|2k|4k`; 2k is the default and enough for most shots);
   - a sound: `pack:ID` (a curated CC0 pack), `commons:FILE NAME[=kind,...]` (Wikimedia Commons) or
     `freesound:ID[=kind,...]` (Freesound, only with the user's API key); the kinds say which `sfx` kinds a single
     clip serves, else they come from its file name;
   - a font: `font:Family@WEIGHT` (a static weight from Google Fonts, for a brand font installed only as a variable
     font, `look-and-brand.md`).
   It saves the pending plan and returns a `table`: each asset's name, source, licence and size, and the total.
   Planning reads small JSON and file sizes only; it never fetches the files.
3. **Ask:** show that table to the user exactly (it is short Markdown) and ask "OK to download these (X MB)?" The
   story page also lists the assets. Wait for a clear yes. If the answer is no, build without them.
4. **Download:** `CLI assets download SHORT`. Files go to the workspace's shared cache `<workspace>/.assets-cache/`
   (reused by later shorts, never downloaded twice), each checked against its size and, where the source publishes
   one, its MD5 (Poly Haven) or SHA-1 (Wikimedia Commons). Each asset is recorded in `short.json` `assets` with its
   source and licence, for the credits on the final page. Sound packs are unpacked into
   `.assets-cache/sounds/<pack>/` and every clip is added to the sound library with its kinds, source, author, page
   and licence, where `CLI mix` and `bsa.audio.clip` find it. It returns the local paths per asset id.
5. **Use:** in `build.py`, find the files with `bsa.assets.polyhaven.asset_files(short_dir, asset)` for each entry of
   `short.json` `assets`: it returns `{role: path}`, each file at `<workspace>/.assets-cache/<file path>` (the
   workspace is the short folder's parent; `<file path>` is the asset's `files[].path`). Load the files (`look.image_material` for texture maps, mapping each
   file's `role` in `short.json` `assets[].files` to its key: `diffuse` → `base_color`, `rough` → `roughness`,
   `nor_gl` → `normal`, `disp` → `displacement`; an Environment
   Texture node for an HDRI; for a model, append its objects from the .blend
   (`bpy.data.libraries.load(path, link=False)`, then link each object into your scene), never a linked or
   collection instance: the checks only see an instance's materials and images and warn "instances", while
   overlaps, normals and grounding need real objects). Check imported models with the same rules as your own
   geometry (materials, normals, scale). Sounds need nothing in `build.py`: `sfx` events use them, and
   `source/sound.py` loads them with `bsa.audio.clip` (`sound.md`). A font's `role` is `font`: set the short's
   `style.font` to its file path.

## Where sounds come from, and what is not offered

- **Curated packs** on OpenGameArt, at stable direct-file URLs, all CC0: rubberduck's "100 CC0 metal and wood SFX"
  (`pack:oga-metal-wood`: hits, drops, doors, locks, tools), Kenney's "51 UI sound effects" (`pack:kenney-ui-sfx`:
  clicks and switches) and Kenney's "50 RPG sound effects" (`pack:kenney-rpg`: doors, creaks, latches, metal, cloth,
  a blade drawn).
- **Wikimedia Commons**: no key. The licence is read from each file's own page; anything but CC0 is refused at plan
  time.
- **Freesound**: a large library, searched for CC0 only, and each sound's licence is checked again at plan time. It
  needs the user's free API key (`settings set freesound.api_key KEY`); the plugin downloads the high-quality OGG
  preview.
- **Not offered:** Sonniss GDC bundles (royalty-free, but their licence is not CC0), and any sound or asset whose
  licence is not CC0 (CC BY, CC BY-SA, "free for personal use", ...).

Never download from other sites without the same plan-and-yes step and a licence that allows commercial use.
