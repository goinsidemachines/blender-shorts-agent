# Honesty

An explainer that is wrong is worse than no explainer. These rules are not negotiable.

## Every number is sourced

- Every number on screen or in the narration comes from a fact in `short.json` with a source URL. `CLI validate`
  rejects a line with a digit that cites no fact, and a numeric label in a shot where no line cites one.
- Prefer primary and expert sources; confirm key numbers in two independent sources. When sources disagree, either
  find the authoritative one or leave the number out (if sources give a bridge's cable count three different ways
  and none fits the drawings, the short states none). Prefer the engineers' or a standards body's number to a
  vendor's marketing claim; if only a vendor gives it, say whose claim it is or leave it out.
- Numbers you compute yourself (a combination count, a simulated reduction) are facts too: cite the source of the
  inputs and keep the calculation in `source/notes.md` or `source/model.py`. Say "about" where the result is
  approximate.
- Say what a number means, precisely: "cuts peak vibration by up to half" (the peak, in the test the source
  describes), not "halves the vibration".

## Cut myths, even when they look good

If a popular picture is wrong, it does not go in. A bicycle short might want to say "the spinning wheels'
gyroscope effect keeps it upright"; experimental bikes with that effect cancelled still balance themselves, so
the line is a myth and is cut. Likewise the "equal transit time" picture of a wing (the air over the top does not
have to meet the air underneath). Check each striking claim against the physics and the sources before it
earns a shot. Say in the story-page chat what you cut and why: users like knowing.

## Motion is from simulation, never eyeballed

Mechanisms move the way the physics or the geometry says: a simulated pendulum, gear teeth meshing at the ratio
their tooth counts give, a piston following its crank's real geometry. Phase, amplitude and timing come out of the
model, and the model is checked against a sourced number before animating. Describe what the model shows
accurately: a crank-driven piston lingers longer near the bottom of its stroke than the top, so "it moves like a
sine wave" would be wrong.

## Badge every exaggeration or simplification

When the real motion is too small, too fast or too complex to show at true scale, you may exaggerate, slow or
simplify it, but the shot's `badge` says so on screen ("Motion exaggerated", "Tolerances exaggerated",
"Simplified", "Slowed down"). Note the factor in your notes (for example: exterior motion ×50; close-ups at true scale).
A comparison shows what it really compares: if one side has the part removed, label it "without the part" ("no damper"), never as a locked or disabled version
of it.

## Nothing dangerous taught as a technique

Explain why things work, not how to defeat or misuse them. A short on a car alarm explains what it detects, not how to
get round it: no tool close-ups and no technique. Refuse to make a short whose point is a dangerous
how-to, and say why.

## Clean credits

No plugin-author (goinsidemachines) branding or credit in a user's video, unless the user is that brand or asks for
it. By default the only identity on screen is the user's own: their handle, and their own line or site on the end
card (`look-and-brand.md`), or nothing. Downloaded assets are CC0 and recorded in `short.json` `assets` with their source and licence.
