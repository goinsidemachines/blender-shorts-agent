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
