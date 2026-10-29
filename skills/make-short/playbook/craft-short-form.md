# Short-form craft

What made the reference shorts work, as rules.

## Hook in the first ~3 seconds

- The first line and the first picture carry the most surprising true thing. "A mechanical watch ticks
  28,800 times an hour." "A zipper is two rows of hooks that a slider forces together." (Examples of the
  form only: research every topic's own facts fresh.)
- Start on motion or a striking picture, never on a title card, a logo or "In this video".
- The hook makes a promise the rest pays off: if you open with the ticking, the short is about why it ticks.

## One idea per beat

- Each shot says one thing and shows that thing. If a line needs "and", it may be two shots.
- Order beats so each needs only the ones before it: problem → mechanism → proof → payoff.
- The core mechanism gets the most time (in a 45 s short, "how it works" gets about a quarter of
  the time or more).

## Each idea lands on its visual

- The picture that proves a line is on screen while the line is spoken. Lines start a moment after their shot
  starts (the timeline leaves a short lead-in), so the eye arrives first.
- Labels appear one at a time, each with a soft sound, when the narration reaches them; never a wall of text.
- When a number is said, show what it measures (a 2 m flywheel next to a person).

## Captions carry the story with the sound off

Most viewers start muted. The captions alone must tell the whole story, so the lines are the script: short
sentences, concrete words, no "as you can see". Captions are chunked (at most about six words, 32 characters a card)
and timed to the voice, or to reading pace with no voice. Keep captions and key visuals inside the safe area: the
bottom fifth and the right edge are covered by platform buttons. The captions themselves sit about 61-73% down the
frame (rows 1165-1395 of 1920, `06-build.md`), so keep the key visual above them while they show.

## Pacing

- Aim for about 45 s; cut anything that does not serve the one idea. If the voice runs long, cut words rather than
  speed the voice up. A script tightened after the first take can still come out longer (say 36 to 44 s),
  because better pacing beats cramming.
- **Fast-paced by default: short holds.** A shot runs about as long as its line plus a breath (speech + about 25%);
  a quick silent beat is 1.5 to 2.5 s. Long narration-free stretches read as slow even with good motion: a viewer
  who rejected a 90 s cut as "very slow" approved the same short re-timed to 60 s with the holds trimmed. `min_s`
  is a floor, so a large one keeps a slow script slow.
- `CLI validate SHORT` and `CLI timeline SHORT` report `pacing`: spoken and silent seconds per shot, and warn on a
  shot more than 60% silent with a length to try. The story page draws it as a bar under each beat. A long hold is
  fine when it is the point (the money shot, a reveal); say so, don't just accept it.
- Vary shot length; hold on the money shot.
- Camera moves are slow and motivated (a push-in to reveal, an orbit to show depth); never shake for energy.

## A closing line that lands

The last line is short, rhythmic and states the idea in a new way: "It doesn't store time. It spends it, one tick at a time."
"The slider holds nothing. It only lines the teeth up." No "thanks for watching", no "like and subscribe" in the
narration or the captions (the user can add a call to action in the description if they want). The user's own
end-card line above their handle ("Follow for more", the `end_card` setting) is their choice and stays. Hold the final
picture a beat after the line ends (with a handle, the timeline already holds the last shot 1.5 s for the end card).

## Preview stills of every shot before rendering

The storyboard is not optional polish: the user sees a real Blender still of every shot before the long render,
and so do you. Problems found on a still cost seconds; found in a final render, hours.
