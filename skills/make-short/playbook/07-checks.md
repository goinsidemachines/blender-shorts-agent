# 07 Checks

A short must never go out with wrong animation, missing textures or render artifacts. Two kinds of checks catch
them: code checks, and **your own eyes**.

## Run the code checks

`BLENDER check SHORT` runs every scene check over every shot (every 5th frame plus each shot's last; pops use every
frame, and anything that moves or deforms is checked for overlaps on every frame, so a part that passes through
another between two samples is still found) and writes `review/checks.json`, errors first. It marks the `checks` stage **only when there are no error
findings**; with errors it exits non-zero. Warnings do not block, but read each one and fix it unless it is clearly
intended (say why in your notes).

Fix, rebuild (`06-build.md`), check again, after every fix: a fix moves parts, and moved parts make new
artifacts. `checks.json` is stamped with the build and the timing: the final render
and edit refuse checks that are missing, failed, or ran on an older build or timing, so check again after every
rebuild. If an error is still there after 3 real attempts, stop and tell the user
what fails, what you tried, and the options (for example: simplify the shot, accept a badge, change the camera).

## Every check, and how to fix the usual causes

Scene checks (`bsa/blender/checks.py`, in `review/checks.json`):

| Check | Severity | What it means | Usual causes and fixes |
|---|---|---|---|
| `images` | error | An image texture node (or the world's environment texture) has no image, or its file is missing: renders pink. | A path to a file that was moved or never downloaded; an empty Image Texture node left in. Load from the path `assets download` returned, use `look.image_material` (it packs images), delete unused nodes. |
| `materials` | error | Geometry in view (a mesh, curve, text label, surface or metaball, or an instanced object) has no material: renders default grey. | Forgot `look.assign`; a modifier or join created a new object; an imported model lost its slots; a text label made without a material. Assign a real material to everything visible. |
| `instances` | warning | Instanced geometry (a collection instance, geometry-node instances) is checked for materials and images only: overlaps, normals, grounding and pops are not checked on it. | Append the model's objects instead of instancing a collection (`assets.md`), or check those instances yourself in the stills and frames. |
| `normals` (flipped) | error | Some faces are wound the opposite way to their neighbours: dark patches, wrong shading, broken bevels. | Mirrored or joined geometry, hand-built faces in the wrong order. Recalculate normals outside (`bmesh.ops.recalc_face_normals`) in build.py. |
| `normals` (inside out) | error | A closed shell has negative volume: the whole object is inside out. | Negative scale applied, faces built clockwise. Recalculate normals, or apply scale before building faces. |
| `normals` (non-manifold hero) | warning | The hero mesh has open edges. | Missing caps, duplicate vertices. Merge by distance, fill holes; open edges show as gaps and break bevels. |
| `faceted` | warning | A curved surface has many edges between 1° and 25° shaded flat: it looks faceted. | Flat shading on a cylinder or sphere. `look.smooth(obj)` (keeps sharp edges sharp), more segments, or a bevel modifier with segments. `look.smooth` then `look.bevel` (hardened normals) renders smooth and is not reported, on boxes and on cylinders, cones and rings alike. |
| `overlap` (z-fighting) | error | Two objects have coplanar faces facing the same way, overlapping: they flicker in the render. How close counts depends on how far the shot camera is: at most 0.1 mm (far away), down to 0.01 mm close up, so a small part a hair inside another near the camera is fine. Faces exactly on one plane always count. The finding gives both objects' sizes, one place where the faces overlap (world x, y, z) and how far apart they are. | A plate laid exactly on a floor, two copies at the same spot, a decal with no offset. Move one by 0.5 to 1 mm or delete the duplicate face. |
| `overlap` (interpenetrate) | error | Two objects pass through each other. The finding names the frames and how deep the deeper one goes (`'Rod' is 4 cm inside 'Block'`). When the other mesh is not closed (holes, open parts) and not a flat sheet like a floor, a depth would mean nothing: the finding says it is not closed, with its count of open edges. A pair that overlaps the same way in every frame (scenery that never moves) says "in every frame" and is listed once with all its shots. The first 20 pairs are listed (a pair counts once, in any number of shots); one more finding says how many pairs were not. | Wrong motion (a part swings into another), wrong sizes, a clearance that is too small, a linkage built from an axis instead of from a surface. Fix the motion or the geometry. Tag `contact:<other>` only for real surface contact (below). Siblings under one parent or assembly empty are checked like any other pair. |
| `overlap` (joined / interpenetrating parts excused by contact tags) | error | A pair tagged `contact:<other>`, or a direct parent and child, overlaps deeper than it may: by default the smaller of 5 cm and 10% of the thinner part's smallest dimension (at least 2 mm), so a 1 cm rod may sink 2 mm. The finding names the pair, the depth, the frames and the allowance. | A rod that runs to the centre of the hub, ball or bob it is attached to; a cable whose length was set by eye and ends inside what it holds; a sliding part pushed into a solid body. End each part at the other's surface, computed from the geometry; make bodies that something slides into hollow. For the rare part that is deep by design (a pin seated in a board), tag `contact:<other>:<max depth in metres>`, e.g. `contact:Board:0.015`, and write why next to the tag. Only a direct parent/child pair is excused (and depth-limited the same way); a grandparent, or two children of one parent, are not. |
| `self_zfight` | error (different materials), warning (one material) | Faces of one mesh lie on the same plane (within 0.1 mm, less when the camera is close), face the same way and overlap: z-fighting inside one object. The finding names both materials, the overlap area and where it is. With two materials it flickers between them; with one it shows as shading noise. Checked on heroes and on every rendered mesh in a shot camera's view. Faces that only share an edge (one flat surface split into quads) are fine, and so are faces on one plane that face opposite ways (where two joined parts meet face to face, inside the solid). | Joined boxes: a ledge or cap flush with the body's top, a column whose outer face lies on the wall's, a window band whose sides lie on the columns', a lug's bottom flush with a ring's bottom. Tuck one face inside the other (make the smaller part 0.5 to 1 mm thinner or sink it into the body), or stop one part at the other's face. |
| `self_overlap` | warning | A hero mesh is made of loose parts (pieces of one mesh not joined by edges) of comparable size that pass into each other deeper than the contact allowance: they render as one solid, so two rods through each other read as one bar. Parts that only touch are fine, and so is detail sunk into a big body (one part with at least 10 times the other's bounding-box volume: ledges set into a building's wall, a pin in a block). A hero whose mesh deforms (shape keys, an armature) is checked at every sampled frame, and there big-body detail warns too when it moves against the body (it sinks in at some frames and not others, or its depth changes). | Two primitives joined into one object without trimming them; a shape key or bone that pushes one part into another. Make them separate objects (then the overlap checks see them), end one part at the other's surface, or fix the deformation. |
| `boolean` | warning | A rendered mesh has a live Boolean modifier. It is evaluated again for every render and can come out differently: an opening that shows in the stills can vanish in the final render (an EXACT cut on a hand-built or non-manifold mesh above all). | A window or cut-away made with a Boolean. Build the opening into the mesh itself (leave out the faces, close the edges), or apply the modifier in build.py and check the face count. |
| `contact_parent` | warning | A pair tagged `contact:<other>` follows different moving parents (one part rides on an animated or constrained parent or grandparent that the other does not), and where they meet drifts apart during a shot. The finding names what moves each part. | A part rebuilt with a new constructor that dropped its parent, so it stands still while its partner sways through it. Parent both to the moving part they share. A part that really moves against a fixed one through its parent (a foot stepping on a floor) also warns: then say why in your notes. A part's own animation (a rod sliding in a fixed sleeve) and a hinge on a fixed pin through the meeting point do not warn. When the parts move against each other by design (pendulum balls that strike and swing apart, a pawl on a turning ratchet ring, a tyre on a scrolling floor), tag `contact:<other>:moves` instead, and write why. |
| `floating` | error | An object tagged `grounded` floats above or sinks into what is under it (more than 2 mm), or has nothing under it. | Origin at the centre instead of the base, a support moved, motion lifting it. Compute its Z from the support's top, or bake contact from geometry. |
| `pops` | error | An object jumps in one frame (or two) far more than it moves in the frames on both sides: a velocity discontinuity. Fast motion that then slows or stops (a swing ending in a collision, a struck ball setting off) is not a pop. | A keyframe gap or a missing frame in a baked series, a cut inside one scene, a teleport for a reset, a multi-leg path eased over one parameter (fast on long legs, slow on short ones). Bake every frame (`keyframe_series` step 1), move along paths by arc length (`bake.keyframe_path`), split a cut into two shots, hide the object instead of moving it. |
| `frame_edge` | warning | A `hero` (or `overlay`) object is partly or wholly outside the camera frame during its shot. | Camera too close, the subject moves out, the wrong camera. Reframe or move the camera; tag only the true subject as hero (`hero:s03,s04` when it is the subject of those shots only). |
| `names` (renamed duplicate) | warning | An object name ends in `.001`, `.002`...: Blender renamed it because the name was taken. Object names are shared by every scene in the .blend, so a label or tag using the plain name points at the other scene's object. One warning names every renamed object. | Two scenes each making `Person`, or a rebuild that did not remove the old objects. Give each object a name unique across the short (`Person (lab)`, `Person (street)`), or remove the old scenes before building. |
| `names` (missing name) | error | A `near` label in short.json names an object its shot's scene does not have, a `contact:` tag names an object the scene does not have, or a `hero:` tag names a shot short.json does not have. The finding suggests the `.001` name when there is one. | A typo, a renamed duplicate, an object built in another scene. Use the object's exact name, and rename duplicates. |
| `hard_rule` | warning | An object, mesh, material, collection or linked library name has a word one of the user's hard rules may forbid (`no_humans`, `no_animals`, `no_faces`, `no_brand_logos`): a "Worker" object, a "Skin" material. Only with such a rule. | A downloaded model or material that shows what a rule forbids: remove it. An innocent name (a clock's "HourHand"): rename it, or clear the word with its reason in `short.json` `rules_check` (`02-story.md`). Never leave one unanswered. |

Tags the checks read (`bake.tag(obj, ...)` in build.py, `06-build.md`):

| Tag | What it does |
|---|---|
| `hero` / `hero:s03,s04` | The subject (of those shots only): `frame_edge` warns when it leaves the frame; `self_overlap` and `normals` (non-manifold) look at it. |
| `grounded` | Rests on something: `floating` errors when it floats or sinks more than 2 mm. |
| `contact:<other>` / `contact:<other>:<max depth m>` | Really touches `<other>`: the pair may overlap up to the contact allowance (or the stated depth); `contact_parent` watches the pair's parents. |
| `contact:<other>:moves` / `contact:<other>:<max depth m>:moves` | Touches `<other>` at some point and moves against it by design (parts that strike and part, slide, roll or hinge). The depth limit holds as for `contact:`; `contact_parent` does not warn about the pair. |
| `overlay` | Not physical geometry: a glow halo, a distance or guide line, an arrow. Left out of `overlap` and z-fighting (as either object of a pair), `self_zfight`, `self_overlap`, `floating` (and never counted as what holds a grounded object up) and `pops`. Still checked for `materials`, `images`, `normals` and `faceted`, and kept inside the frame like a hero (`frame_edge`). Only for things that are drawn on the scene, never to silence a real part. |

At most 20 overlap findings are listed per scene; one more finding says how many pairs were left out. Fix the ones
listed and check again.

### Contact tags: only for real contact

- **`contact:` only for real surface contact:** a foot on a floor, a ball in its cradle, a cable at its anchor.
  Never to silence an overlap you have not looked at. If a pair needs `contact:`, write why next to the tag in
  build.py (`bake.tag(cable, "contact:Ball")  # the cable ends on the ball's surface at its anchor`). The tag
  allows a touch, not a part buried in the other: the checks still report it, with its depth.
- **Run an unfiltered intersection pass before the preview** and look at every pair it lists: no contact or parent
  exemptions, every ~10 frames plus each shot's last frame. Most pairs are intended touches; the ones that are not
  are artifacts. In Blender (the MCP, or a background Blender on blend/short.blend), for each shot's scene and
  frame: build a `mathutils.bvhtree.BVHTree.FromPolygons` from each rendered mesh's evaluated, world-space vertices
  and polygons, and list every pair whose trees `overlap()`. BVH overlap also reports pure touching, so read each
  pair: depth is what separates a touch from an artifact, and the full-size frames at the extremes of the motion
  show it.

Frame checks (`bsa/image_checks.py`): `BLENDER frame-check SHORT --preset preview|final` checks the rendered PNGs and
writes `review/frame-checks-<preset>.json`; it exits non-zero on errors.

| Check | Severity | What it means | Usual causes and fixes |
|---|---|---|---|
| `black_frame` | error | A frame's mean brightness is near zero. | No light in the scene, the camera inside an object, a world strength of 0, the wrong camera. Light it, move the camera. |
| `fireflies` | warning over 25, error over 200 | Isolated very bright pixels (noise). | Too few samples, caustics from glass or metal, tiny strong lights. Cycles: keep denoising on, raise samples, clamp indirect light (`scene.cycles.sample_clamp_indirect = 10`), turn off caustics, use larger lights. |
| `stuck_frame` | warning | A frame is identical to the one before it in the middle of motion: a stutter. Reported only where a hero of the shot (or, with no hero tagged, any object) or the camera is keyed to a different place than at the frame before, so a part at rest or a scripted freeze is not flagged; never inside the shot's `hold`. | A missing key, a frame rendered twice, motion on twos. Bake every frame; re-render the shot (the finding's `redo`). A deliberate freeze the heroes' keys don't show (an effect paused, say): give the shot `"hold": true` or `"hold": [[from_s, to_s], ...]` (seconds from the shot's start) in short.json. |

Other checks along the way: `CLI validate SHORT` (unsourced numbers, unknown ids, size and fps), the `length_drift`
from `timeline`, the mix `levels` (`06-build.md`), and `edit`, which probes the MP4 and fails if its size or frame
count is wrong.

## Look at the images yourself (always)

The code cannot see everything. **Read every storyboard still and a sample of preview frames yourself** before
moving on: the stills before the storyboard page, again after the final build, and after the preview render at
least the first, middle and last frame of every shot plus any frame where something important happens (a contact,
a label appearing, a cut) from `frames/preview/<shot>/####.png`. Hunt for:

- **Wrong motion:** parts passing through each other, floating, sliding instead of rolling, moving the wrong way,
  motion out of step with what the line says at that moment.
- **Untextured or pink surfaces:** default grey, missing-texture pink, flat plastic where there should be metal.
- **Flicker and noise:** grain, fireflies, blotchy denoising, z-fighting stripes; compare neighbouring frames for
  flicker.
- **Framing:** subject cut off, labels or the subject under where the platform buttons sit, a horizon that tilts by
  accident.
- **Faceting, harsh black shadows, blown-out highlights.**

Anything wrong is a check failure: fix it the same way (up to 3 attempts, then tell the user).
