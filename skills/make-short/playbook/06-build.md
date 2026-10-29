# 06 Build

Goal: every scene built to the final timing in `blend/short.blend`, at full quality, plus the sound mix. You first
wrote `source/build.py` for the storyboard; now finish it and rebuild, because narration changed the timing.

## The build.py contract

`<short>/source/build.py` is yours to write. It must:

- build **every scene named in `short.json` shots' `scene`** (one Blender scene per `scene` name; several shots may
  share one), each with its own camera, lights and world;
- animate in the **short's own frame numbers**: shot `s03` runs frames `f0..f1` from `source/timeline.json` in its
  scene. Never hard-code frame numbers: read them from the timeline, so a re-timed short rebuilds correctly;
- call `bsa.blender.scene.setup_scene(scene, short, engine, "preview")` on each scene (1080×1920, 30 fps, AgX), with
  the engine approved at the engine stage (before that stage, the saved `render.engine`, else the engine that suits the
  look); the render commands apply the approved engine anyway, but stills and the MCP viewport use the scene's own;
- use `bsa.blender.look` for materials (`material`, `image_material` for downloaded maps, `bevel`, `smooth`) and
  give **every** visible mesh a real material;
- bake motion from a simulation or model with `bsa.blender.bake.keyframe_series` (never eyeballed keyframes for a
  mechanism; see `fields/*.md`), and move anything that travels along a multi-leg path with
  `bsa.blender.bake.keyframe_path(obj, points, f0, f1, ease="smooth")` (by arc length, see "Parts that move against
  each other");
- tag objects for the checks with `bsa.blender.bake.tag(obj, ...)`: `"hero"` for the subject(s) each shot is about
  (must stay inside the frame; `"hero:s03,s04"` makes it a hero only in those shots, for a subject the camera leaves
  on purpose elsewhere), `"grounded"` for things that rest on something (must not float or sink),
  `"contact:<other object name>"` for parts that really touch (a foot on a floor, a ball in its cradle, a cable at
  its anchor; `"contact:<other>:<max_depth_m>"` for the rare part that sits deeper by design;
  `"contact:<other>:moves"` for parts that touch and move against each other by design), with a comment next to
  the tag saying why (see "Quality bar" below), and `"overlay"` for what is drawn on the scene rather than built in
  it (a glow halo round a pivot, a distance line, an arrow): the overlap, z-fighting, grounding and pop checks leave
  it out, and it must still stay inside the frame (`07-checks.md`);
- read the look option from the `BSA_STYLE` environment variable when it is set (story-page thumbnails);
- start by removing its old scenes with `bsa.blender.scene.remove_scenes(names)` (it also purges their objects,
  so a rebuild in the same Blender keeps exact object names; see `mcp-mode.md`). It and `setup_scene` also remove
  the untouched factory scene ("Scene" with only its Cube, Light and Camera), so a camera you name "Camera" keeps
  that name instead of becoming "Camera.001". A "Scene" the user changed is kept;
- stamp every scene it builds with the timeline it animated: `sc["bsa_timeline"] = timeline_hash(tl)` (from
  `bsa.timeline`). Every `BLENDER` command recomputes the timing from `short.json` and the takes, compares it with
  `source/timeline.json` and with each scene's stamp, and refuses a mismatch with "timing changed: run CLI timeline
  SHORT, then rebuild" (or "built for another timing"): run `CLI timeline SHORT`, rebuild, then carry on;
- give every object that a `near` label points at exactly that name (`{"text": "Light", "near": "Light bob"}`);
- save to `blend/short.blend`. Rebuild after every edit to build.py: when `source/build.py` is newer than
  `blend/short.blend`, every `BLENDER` command warns "build.py changed since the scene was built: rebuild" (in its
  result's `warnings`, or `build_warnings` for `check` and `frame-check`, and on one stderr line), and the final
  render and edit refuse to run.

Skeleton:

```python
import json, os, sys
from pathlib import Path

if "--" in sys.argv:  # script mode: blender -b --python build.py -- PLUGIN_ROOT SHORT_DIR
    PLUGIN_ROOT, SHORT_DIR = sys.argv[sys.argv.index("--") + 1 :][:2]
    sys.path.insert(0, PLUGIN_ROOT)

import bpy
from bsa.blender import bake, look, scene as bsa_scene
from bsa.short import load_short
from bsa.timeline import timeline_hash

STYLE = int(os.environ.get("BSA_STYLE", "0") or 0)
ENGINE = "cycles"  # the engine approved at the engine stage (or the saved preference); "eevee" if that was chosen


def build(short_dir):
    short_dir = Path(short_dir)
    short = load_short(short_dir / "source" / "short.json")
    tl = json.loads((short_dir / "source" / "timeline.json").read_text(encoding="utf-8"))
    span = {s["id"]: (s["f0"], s["f1"]) for s in tl["shots"]}
    names = sorted({s.scene for s in short.shots})
    bsa_scene.remove_scenes(names)  # old scenes and their objects, so a rebuild keeps exact object names
    for name in names:
        sc = bpy.data.scenes.new(name)
        bsa_scene.setup_scene(sc, short, ENGINE, "preview")
        # ... models, materials, lights, world, camera moves, baked motion, tags ...
        sc.frame_start, sc.frame_end = 1, tl["n_frames"]
        sc["bsa_timeline"] = timeline_hash(tl)  # the Blender commands refuse a scene built for another timing
    bpy.ops.wm.save_as_mainfile(filepath=str(short_dir / "blend" / "short.blend"))


if __name__ == "__main__" and "--" in sys.argv:
    build(SHORT_DIR)
```

Run it (script mode):

```
"<blender>" -b --factory-startup --python-exit-code 1 --python "<short>/source/build.py" -- "<root>" "<short>"
```

For a style thumbnail, set `BSA_STYLE=<n>` in that command's environment. In MCP mode run the same file through the
MCP instead, always with `import runpy; runpy.run_path(r"<root>/bsa_mcp_build.py")["build_short"](r"<short>")` so the
open Blender uses the plugin's current code (`mcp-mode.md`).

## Quality bar for the build (no half-done work)

- **Hard rules** (`CLI settings rules`) hold in every scene. Name objects, meshes and materials for what they are:
  `BLENDER check` warns ("hard_rule") when a scene name has a word a rule may forbid (a "Worker" object or a "Skin"
  material under `no_humans`); remove the thing, or, when the name is innocent (a clock's "HourHand"), clear the
  word in `short.json` `rules_check` (`02-story.md`).
- **Real proportions** from sourced dimensions where they exist; a familiar object for scale (a door, a car, a
  bench, a standard container). Under a `no_humans` rule people never appear, not even as a silhouette.
- **Every surface finished:** bevels on hard edges (nothing is razor-sharp in life), smooth shading on curved
  surfaces, micro-bump or a real texture so materials do not look like plastic. No default grey, no pink.
- **Motion from simulation or computed contact** (`fields/physics.md`): mechanisms move because the physics or the
  geometry says so, and the timing of the motion follows the timeline (a swing peaks when the line says "and pulls
  it back").
- **Camera:** frame for vertical; keep the subject and any labels inside the safe area and off the overlays (see
  "Where the overlays sit" below). Smooth moves, no cuts mid-motion unless the story needs one.
- **Exaggeration is a decision, not an accident:** if real motion is too small to see, scale it, put the factor in
  your notes, and set the shot's `badge` ("Motion exaggerated").
- Downloaded assets come only from the approved plan (`assets.md`); find their files with
  `bsa.assets.polyhaven.asset_files(short_dir, asset)` (`<workspace>/.assets-cache/<file path>`), append models'
  objects (never collection instances), and pack images (`look.image_material` does).

### Parts that move against each other

The checks pass geometry that is wrong to the eye if you excuse it, so build it right:

1. **Moving parts live in hollow parts.** A rod that slides into a cylinder needs a hollow tube with a hole larger
   than the rod and a head smaller than the bore, never a solid body the rod disappears into.
2. **Linkages start and end on surfaces, computed in code.** A rod pinned to a hub starts at the hub's radius, not
   its axis; a cable ends on a sphere at `sqrt(R^2 - offset^2)`; a bracket's top is the tube's underside. Never a
   guessed length.
3. **Two parts never share a volume unless they are one part.** Two rods that meet in a hub each end at the hub.
4. **Fluids are bounded by what moves them.** Oil fills the gap between a piston head and the cap, and that gap is
   animated from the same motion data, so the fluid shortens and lengthens with the stroke.
5. **Paths for overlays and effects go around walls or through openings**, via a waypoint in the opening, never
   straight through solid structure; test the path for intersections over the whole shot.
6. **Clearances are computed:** place icons, bars and labels-in-3D from their neighbours' sizes plus a margin
   (moving and static neighbours alike), never by eye.
7. **`contact:` only for real contact, with a reason.** A foot on a floor, a ball in its cradle, a cable at its
   anchor. Never to silence an overlap you have not looked at; write why next to every tag. Contact is depth-limited:
   `contact:<name>` allows a touch, and the checks still report a part that sits deep inside the other;
   `contact:<name>:<max_depth_m>` allows a stated depth for the rare deep-by-design case.
   `contact:<name>:moves` (or `contact:<name>:<max_depth_m>:moves`) is for parts that touch at some point and move
   against each other by design: pendulum balls that strike and swing apart, a string hinged at its rail, a pawl
   sliding on a turning ratchet, a tyre rolling on a scrolling floor. The depth limit still holds; only the
   `contact_parent` warning is left out.
8. **Run the checks** (`BLENDER check SHORT`, `07-checks.md`) and read every overlap pair they list, even the ones
   your tags allow: most are intended touches, the rest are your artifacts.
9. **Look at full-resolution frames at the extremes of the motion** (max stroke, max swing, a hinge fully open) of
   everything that slides, hinges, hangs or joins: `BLENDER extremes SHORT` renders each hero there. Mid-shot
   contact sheets hide thin parts passing through each other.
10. **Each effect gets its own lane.** Particles, sparks or arrows sent through one shared waypoint merge into a
    blob there. Give each its own offset waypoint and end point, and stagger their timing; never send several
    objects through one point.
11. **Move along paths by arc length.** A multi-leg path eased over one parameter (per leg or per point) races on
    the long legs and crawls on the short ones, and the `pops` check flags the jump at each corner. Use
    `bake.keyframe_path(obj, points, f0, f1, ease="smooth")`: it keys every frame at an even distance along the
    whole polyline, with one easing over the whole length.
12. **Every fix can make a new artifact.** After each geometry or motion fix, run `BLENDER check SHORT` again (and
    the unfiltered intersection pass in `07-checks.md`), not only the stills: moving one part puts it against
    another.
13. **Build openings into the mesh; no live Booleans.** A Boolean modifier (an EXACT cut on a hand-built or
    non-manifold mesh above all) is evaluated again for every render and can come out differently: a window that
    shows in the stills can be gone in the final render. Leave the faces out and close the edges in the mesh
    itself; if a Boolean is unavoidable, apply it in build.py and check the face count. The checks warn on any
    live Boolean on a rendered mesh.
14. **Parts in contact share the moving parent.** When two parts touch (a rod in its cylinder, a part on a moving
    deck), parent both to the moving part that carries them. When you replace a part's constructor, keep its
    parenting: have part builders return parented parts. A dropped parent leaves one part standing while the other
    sways through it; the checks warn when a `contact:` pair follows different moving parents and drifts apart.
15. **Never let two faces share a plane, even inside one mesh.** Two faces on one plane flicker in the render
    (z-fighting), between two objects and inside one joined object alike. Joined boxes in one object are the usual
    cause: a ledge whose top is flush with the body's top, a column whose outer face is flush with the wall's, a
    band whose side faces lie on its neighbour's. Tuck one face inside the other (make the smaller part 0.5 to 1 mm
    thinner, or sink it that far into the body) or stop one part at the other's face. The checks report it as
    `self_zfight` (an error when the two faces have different materials).

`BLENDER preview SHORT` runs the whole rebuild chain after a change (build, `state done build`, mix, check, preview
render, frame-check, edit), so even a one-number camera tweak is one command; then look at the edited video itself
with `BLENDER grab SHORT --preset preview --at <s>...` (or `--sheet` for a contact sheet of the whole edit).

### Where the overlays sit (1080x1920, pixel rows from the top)

Frame the heroes around the overlays the edit draws (`bsa/layout.py`, `bsa/blender/edit.py`):

- **Safe area:** x 60-940, y 220-1500. The bottom 420 px and the right 140 px are under the platform's buttons.
- **Caption band: rows about 1165-1395 (61-73% down the frame)** whenever a caption shows, full safe width. That
  is a two-line card (default 64 px captions: a card wraps at about 22 characters, so most cards are two lines) with
  its box; a one-line card covers about 1245-1395. A larger `style.caption.size` makes the band taller upward. Keep
  heroes, the parts the line talks about, scale figures and comparison bars out of this band while captions show:
  the band is not the bottom fifth, it is two-thirds of the way down.
- **Badge: top-left, rows about 221-297**, x from 61 to about 100 + 17 px per character (about 400 for "MOTION
  EXAGGERATED"); stacked (plain) labels follow below it, about 88 px each.
- **Frame with `bsa.blender.camera.frame_objects`, not by trial and error.** Set the camera's angle, then
  `camera.frame_objects(cam, [objects...], frames=[f0, ..., f1])` moves it along its view axis (an orthographic
  camera: its `ortho_scale`) and slides it in its own plane until the objects' bounding boxes, at every frame you
  give (the extremes of the motion), just fill the safe area above the caption band (`bsa.layout.frame_area`: x
  60-940, rows 220 to about 1165, 24 px inside; `margin=` to change that, `avoid=None` for a shot with no captions,
  `aim=True` to turn the camera to the objects first, `center=False` to keep them where they sit). It returns the
  box it reached; then look at the still. For a camera move, frame each end with `key=FRAME` (it keys that
  position): it is safe on an already animated camera (it starts from the camera's pose now, measuring other frames
  does not snap it back, existing keys stay), so frame and key each position in turn without clearing the
  animation. Without `key` the new position is lost at the next frame change on a keyed camera.
  `camera.frame_box(cam, lo, hi, of=obj)` frames a box instead (`lo`, `hi` its corners, in `obj`'s own coordinates,
  following it; in the world without `of`): a part of one long mesh, with no helper object. Read the result's
  `warnings` (also printed as a `FramingWarning`): the camera sent far off (more than 5 times the box's size or new
  distance: the objects are not where the camera looked, often because a parent moved or turned them), the box
  outside the view or past the clip end, or the fit not settling. Fix the cause, never ignore it: such a camera
  renders black or empty stills. A box with no size (an object with no geometry) is refused.
- **Wide subjects in a tall frame** (a row of balls, a train, a bridge, a long machine): side-on, a wide subject
  fills the 1080 px width and only a third of the height, and looks small. Instead:
  - Shoot it at a 3/4 angle **along the row** (camera 30-45 degrees off its long axis, a little above): the row
    recedes into depth, so it takes height as well as width and the near end reads large.
  - Use the space it leaves above and below for the overlays (a velocity arrow, an energy bar, a live plot, the
    "1 in, 1 out" label), never for the captions' band.
  - Push in on the part that is acting (the ball being released, the far ball flying out) and cut or move back to
    the whole row only when the whole row matters; `frame_objects` on just that part (with its motion's extreme
    frames) frames the push-in.
- **Near labels** sit just above their object's top and are moved off the captions, the badge, the stacked labels
  and the near labels placed before them, so an object high in the frame or two labelled objects side by side push
  a label sideways or down onto the picture. Leave room above a labelled object, below the badge rows.

## No helper fits

Never refuse a topic because no field helper exists. Write the model or simulation yourself in `source/model.py`
(numpy only; `bsa.fields.physics.integrate.rk4` integrates any ODE), and before animating, **verify it against a
sourced number** (a period, a peak, a speed, a ratio from your facts) with the `bsa.fields.physics.verify` helpers
or your own check; note the comparison in `source/notes.md`. If you had to simplify (2D for 3D, a lumped mass, a
slowed-down process), the shot carries a badge ("Simplified" / "Motion slowed"). Build the motion from the model's
output, exactly as with a ready-made helper.

## Sound mix

Write the mix after the build (it needs the final timing): `CLI mix SHORT` writes `audio/mix.wav` (voice at -15
dBFS while speaking, effects at -23 dBFS where they sound, ducked 8 dB under speech, peak -1 dBFS) and runs
`source/sound.py` if present (`sound.md`). Check the returned `levels`, measured on the finished mix the way the
targets are set: `voice_dbfs` is the mix while the voice is speaking (pauses don't count), about -15 (-14 to -17);
`bed_dbfs` is the mix where the effects sound clear of speech (silence between sparse effects doesn't count), about
-23 (-21 to -27: it reads lower when the loudest effects land under speech, where they duck); `peak_dbfs` at or
under -1. `null` means there is no voice or no effects. Far outside those ranges: look at `level_db` and `sound.py`.

## Finish

Re-render the stills (`BLENDER stills SHORT`) and look at them again: the final build must still match what the user
approved. Then `CLI state SHORT done build` and go to the checks. Marking `build` done clears `checks`, `preview` and
`final`: a rebuilt scene must be checked and rendered again (the final render and edit also refuse checks that ran on
an older build or timing).
