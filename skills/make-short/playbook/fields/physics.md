# Field: physics and engineering

Mechanisms, structures, forces and motion. Helpers live in `bsa/fields/physics/` (numpy only, SI units: m, kg, N,
s, rad). The rule for every physics short: **motion comes from a simulation or computed geometry, checked against
a sourced number before it is animated.** Never eyeball keyframes for a mechanism.

## Ready-made models (`bsa.fields.physics.systems`)

All return numpy arrays sampled at `t = k * dt` (`n + 1` samples).

- `pendulum(length_m, theta0_rad, damping=0.0, *, dt=1/300, n=6000)` → `t`, `theta`, `omega`. Full nonlinear
  pendulum released from rest.
- `mass_spring_damper(m, k, c, x0, v0=0.0, force=None, *, dt, n)` → `t`, `x`, `v`. `force` is `None`, a function
  `f(t)` in N, or an array sampled at `dt`.
- `tuned_mass_damper(M, K, zeta_s, mass_ratio, force, *, dt, n, tuned=True)` → `t`, `xs` (structure), `xd` (damper,
  absolute), `vr` (damper velocity relative to the structure), `pull` (force of the damper on the structure). Den
  Hartog tuning. With `tuned=False` the structure responds **bare, as if there were no damper at all** (the damper's
  mass is ignored); with `tuned=True` the damper's pull opposes the structure's *velocity* (it lags about a
  quarter cycle at tuning), so never say it "always moves the opposite way". A side-by-side comparison made with
  `tuned=False` must be labelled "without damper" or "no damper", **never "locked damper"** (a locked damper would
  add its mass and change the period).
- `integrate.rk4(f, y0, dt, n, t0=0.0)`: classic RK4 for any ODE `y' = f(t, y)`, for your own models.

## Collisions in a row (`bsa.fields.physics.contact`)

- `contact_chain(masses, x0, v0, radii, *, stiffness, restitution=1.0, t_end, external=None, sample_dt=1/600)` →
  `t`, `x` (n, N), `v`, `kinetic`, `impacts`. Bodies on one line colliding with their neighbours (a Newton's cradle,
  balls in a row, buffering carriages): Hertz contact `k d^1.5` with Hunt-Crossley losses solved so a two-body
  impact rebounds at exactly `restitution`. `external(t, x, v)` adds any other force in N (for pendulum balls at
  small angles: `-m g / L (x - rest)`). Each impact has its time, pair, closing and separating speeds, `energy`
  (1/2 mu v_in², for loudness), `energy_lost` and `peak_force`; feed them to `bsa.audio.place_clips` for the
  sound (`sound.md`). `hertz_stiffness(r1, r2, E, nu)` gives `k` for two spheres. Momentum is conserved to rounding;
  check `kinetic` against `1/2 m v²` before and after the first impact, and the far ball's exit speed against the
  arriving one. A real steel contact lasts about 0.1 ms: `stiffness=hertz_stiffness(...) * 1e-3` keeps every
  impact far shorter than a frame and runs much faster; say so in `notes.md`.

## Checks before animating (`bsa.fields.physics.verify`)

- `period_from_series(t, x)`: the mean period from zero crossings. Compare with the sourced period (a structure's
  measured period, `2π√(L/g)` for a small-swing pendulum) and with the pendulum length the source gives.
- `energy_drift(E)`: for an undamped model, energy must stay constant (drift well under 1%); if not, shrink `dt`.
- `peak_reduction(a, b)`: the fractional drop in peak between two runs (bare vs with damper). If the short states
  a sourced reduction ("up to N% less..."), the simulation must produce about that in the window you show, and you
  choose the window honestly (if you pick it from a longer record, pick one where the reduction matches the
  published figure, and say on screen that it is a simulation).

Write the comparison into `source/notes.md`: "simulated period 2.01 s vs 2π√(L/g) = 2.006 s for L = 1 m".

## From model to Blender

- Map simulation time to the short's frames explicitly: frame `f` ↔ `t0 + (f - f0) / 30` s, where `f0` is the
  shot's first frame from `timeline.json` and `t0` is the moment of the simulation you chose to show. Keep the
  mapping in one place in `build.py` (or in a small `timing.py` next to it).
- Resample the series at 30 fps (`numpy.interp`) and bake it with `bake.keyframe_series(obj, "location", 0, xs,
  frame0=f0)`; it sets linear interpolation so baked physics is not smoothed into something else.
- Exaggerate only by a stated factor, applied in one place, with the shot's `badge` set ("Motion exaggerated"); keep
  close-ups at true scale where the motion is visible.
- Overlays that explain forces (arrows, a live plot) are drawn from the same data: an arrow for a damper's pull
  uses `pull`, not a guess. Explain the colours once in the narration or a label.

A forged part made of several meshes (a crank and its pin) is one body: join the meshes into one object
(`bpy.ops.object.join()`) before it goes into a simulation, or tag the parent `rigid` so the checks treat the group
as one solid (`07-checks.md`). Separate meshes left untagged read as overlapping parts.

## Blender rigid bodies (`bsa.blender.rigid`)

Blender's own Bullet engine handles bodies that hit, rest and pile up, which the numpy models above don't cover.

- **When Bullet is fine:** hinge pendulums, things dropped onto a still floor or stack, simple stacking and
  toppling, a few bodies knocking each other over. **When the numpy models are better:** anything whose number
  goes on screen (a period, a peak, a reduction: a closed-form or `rk4` model is exact and easy to check), damped
  or driven oscillations (a tuned mass damper, a forced structure), collisions in a row (`contact_chain` conserves momentum and
  gives each impact for the sound), and tiny or very fast motion.
- **The helper:** `rigid.rigid_body_sim(build, f0, f1, substeps=10)` makes a throwaway scene at the short's fps,
  adds a rigid-body world, calls `build(scene)` (which makes the bodies with `rigid.add_body` and joins them with
  `rigid.add_constraint`, then returns the ones to sample), frees the point cache, steps every frame from `f0` to
  `f1`, samples each body's `matrix_world`, and deletes the scene and the objects made only for it. It returns
  `{name: [matrix per frame]}`; `rigid.keyframe_matrices(obj, mats, frame0=f0)` keys the motion onto the render
  object with linear interpolation (`bake.keyframe_series` underneath). Build simple proxies in `build`, not the
  detailed render meshes. Check the result against a sourced number as for any model (a hinge pendulum's period
  against `2π√(L/g)`, with `verify.period_from_series`).

**Traps** (the helper handles the first two and the frame stepping; the rest are yours):

- **fps:** a scene from `bpy.data.scenes.new` is 24 fps. Simulated at 24 and sampled at 30, everything runs 25%
  slow (a 4.0 s pendulum came out at 3.2 s). `rigid_body_sim` sets the short's fps.
- **Point cache:** a second sim in the same scene silently replays the old cache (a new release angle came out at
  the old amplitude). The helper frees it, and the cache's end frame (250 by default) is set to `f1`; past it
  nothing moves.
- **Origin = centre of mass:** Bullet puts the mass at the object's origin and rotates the body about it. Put the
  origin at the centre of mass (`Set Origin > Origin to Center of Mass`) before `add_body`, or the body swings and
  tumbles about the wrong point.
- **Collision margin on thin parts:** Bullet pads every shape by a margin (0.04 m by default). On coins, plates or
  thin walls that is a large fraction of the thickness, so things float above them or hit early. Pass
  `margin=` well under the thickness (a coin 1.5 mm thick: about 0.0002) and prefer a primitive shape ("BOX",
  "CYLINDER") over "MESH".
- **Substeps for fast or small bodies:** at a few steps per frame a small or fast body passes through a thin part
  in one step (tunnelling) or jitters on contact. Raise `substeps` (20 to 60) until the result stops changing.
- **Millimetre mechanisms:** Bullet's tolerances (margins, contact thresholds) are fixed lengths, so at real
  millimetre size a mechanism of many tiny touching parts (a lock's pin stack) can jitter, sink or blow apart even
  when a single small part dropping on a floor behaves. If it does, build the proxies larger (10 to 50 times) with
  `gravity` multiplied by the same factor (the motion then keeps its timing) and scale the sampled locations back
  down before `keyframe_matrices`. Check the result against a number as usual.
- **BOX and CYLINDER shapes are centred on the origin:** a primitive shape is the object's bounding box (or the
  cylinder in it) centred on the object's origin, not on the mesh. A pin whose origin sits at its base gets a box
  half below it: it starts inside its neighbour and is thrown out on the first frame. Put the origin at the
  mesh's centre (which is also the centre of mass for a uniform part), or use "CONVEX_HULL".
- **A MOTOR drives object2 the opposite way:** a MOTOR constraint with a positive `motor_lin_target_velocity`
  moves `object2` along the constraint empty's **negative** X (checked in Blender 5.2: +1 m/s moved it 0.95 m
  towards -X in a second), and a positive angular target turns it the negative way about X (+1 rad/s turned
  it -0.49 rad in half a second). Give the velocity you
  want with its sign flipped, and look at which way the part goes before baking a whole shot.
- **The world only steps one frame at a time:** Bullet advances only from the frame before. Jumping frames (a
  debug loop with `frame_set` every 6th frame) shows nothing moving, and a sim sampled out of order replays the
  cache wrongly. `rigid_body_sim` steps every frame from `f0`; when you step a world yourself, do the same.
- **Don't bake with `bpy.ops.rigidbody.bake_to_keyframes`:** in script mode (`blender -b`) it fails with
  "Operator bpy.ops.anim.keyframe_insert_by_name.poll() failed, context is incorrect", whatever context override
  you give it. Sample with `rigid.rigid_body_sim` and key with `rigid.keyframe_matrices` instead. They also remove
  the throwaway scene, so no `RigidBodyWorld` / `RigidBodyConstraints` collections are left in the short.
- **Damping:** Blender's defaults (0.04 linear, 0.1 angular) slowly stop a swing. `add_body` sets 0; set a real
  value only when you mean it.

**Resting on a moving body (a tray that swings, a deck that rolls):** don't make the carrier an Animated (kinematic)
passive body keyed per frame and drop active bodies on it. Bullet moves a kinematic body by teleporting it each
step, so friction never drags what rests on it along: coins on a swinging tray slid off sideways. Simulate in the
carrier's own frame instead:

1. In `build`, keep the carrier **still and passive** at its pose at the moment you start, with the bodies on it.
2. Tilt gravity into that frame: pass `gravity=` as world gravity expressed in the carrier's axes,
   `R.transposed() @ Vector((0, 0, -9.81))` where `R` is the carrier's rotation (as a 3x3 matrix) relative to its
   rest pose at that moment. For a swing, use the angle at the drop, or run short segments with the angle at each.
3. Sample the bodies' matrices in that frame and transform them back per frame:
   `world = carrier.matrix_world(at frame f) @ carrier_rest.inverted() @ local`, then `keyframe_matrices`.

This leaves out the fictitious forces of an accelerating carrier (centrifugal and Euler terms). They are small
for a slow swing (a few % of g); estimate them (`ω² r` and `α r` against `g`) and write the figure in `notes.md`.
When they are not small, model the contact yourself in numpy.

## Lessons that carry across topics

- **Tiny real motion:** a structure's real movement is often invisible at true scale (millimetres or centimetres on
  something hundreds of metres tall). Exaggerate the wide shot by one stated factor with a badge, and keep close-ups
  at true scale where the motion is visible.
- **Research the purpose, not the folklore:** check what a device is actually for (comfort or safety, wind or
  earthquake, wear or noise) in the engineers' sources; popular one-liners about it are often wrong.
- **Computed contact:** parts that push each other (a cam and its follower, gear teeth, a pawl on a ratchet) move
  from computed contact with the real profile, never from keyframes that only look close. A near-miss (one part a
  fraction of a millimetre off) shows precision better than a wildly wrong part; exaggerated tolerances carry a badge.
- **Security topics:** explain why a mechanism can fail, never how to make it fail.
- **Contacts:** where parts touch on purpose (a foot on a floor, a ball in its cradle, a cable at its anchor), tag
  `contact:<other>` so the overlap check knows it is intended, and write why next to the tag; deep-by-design overlaps
  use `contact:<other>:<max_depth_m>` (`06-build.md`, "Quality bar"). Parts that touch and then part, slide or
  roll by design (colliding balls, a pawl on a ratchet, a wheel on a moving floor) use `contact:<other>:moves`.
  Everything else keeps its clearance.

## Topics no helper covers

Gears, linkages, fluids, heat, optics, orbits, electrical circuits...: write the model yourself in
`source/model.py` (closed-form geometry for linkages and gears, `rk4` for dynamics, a simple grid or particle model
for flows), verify it against a sourced number, and badge simplifications (`06-build.md`, "No helper fits"). Never
refuse a topic because no helper exists.
