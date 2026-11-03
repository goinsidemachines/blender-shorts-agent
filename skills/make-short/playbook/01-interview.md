# 01 Interview

Goal: agree on the topic, the audience, the length, and anything the user already wants. Keep it to a few quick
questions; offer sensible defaults so the user can just say "yes".

0. **Offer to start from an earlier short.** Run `CLI shorts`. When `last_finished` is not empty, ask once: "Make it
   like an earlier one? The last three were ..." (use its titles). If the user says no, go on to step 1 as usual.
   On a pick, step 1 uses `CLI new-short "<working title>" --like OTHER` (OTHER is the earlier short's number, slug or
   folder). It copies only what differs from the user's settings and brand: the look, length, pace, closing and
   voice choices, never the story, and never the render engine. Its result has `said` (one line: say it as it is),
   `notes` (anything it could not copy: say each in plain words) and `skipped` (the questions the copied values
   already answer). Then ask only the questions that are not in `skipped`. A copied value can be changed in chat, or
   by a note on its row under "Started from" on the Interview stage of the page. Never ask for or set the engine
   because of it: the engine stays the user's own choice (`playbook/04-engine.md`).
1. **Make the short's folder:** `CLI new-short "<working title>"`. It returns the folder and the slug. Say where it
   is in one line. When the user (or their content plan) wants a particular number, add `--number N`: it refuses a
   number another short already has (or another session is making), and says which. When the brief already gives
   the topic or the length, pass them too: `--topic "<what a viewer should understand>"` and `--length <seconds>`
   (15 to 90) go into `short.json` `topic` and `length_s` (the length is 45 without it; with `--like` it is the earlier
   short's, unless you pass `--length`). Then only ask what is left.
2. **Open the review page:** `CLI page SHORT`. It is the one page the user follows this short on, from the
   interview to the post text; it updates itself as you work. Say in one line what it is for. Its result has `url`
   and `opened`: when `opened` is false the page did not open (or never checked in), so tell the user it didn't
   open and give the `url` as a link they can click. Never carry on as if it opened. Relay anything in `health`
   in plain words. Later stages switch the open tab with `CLI page SHORT --stage STAGE`; don't open a second one.
3. **Ask, briefly:**
   - **Topic and angle.** What exactly should a viewer understand at the end? One idea, not three. "How a mechanical
     watch keeps time" is a short; "the history of skyscrapers" is not. If the request is broad, propose a sharp angle.
   - **Audience.** Default: curious general viewers (no jargon, no equations on screen).
   - **Length.** Suggest about 45 s. Allowed: roughly 15 to 90 s. Shorter is usually better; every second must earn
     its place. Ask about pace too: shorts are fast-paced by default (each shot about as long as its line, short
     holds). If a brief fixes long beats (15 s each, say) but the lines are short, say up front that it will feel
     slow, and offer shorter beats or more narration.
   - **Must-haves.** A shot they picture, a fact they want in, a comparison, a tone.
   - **Hard rules.** Read `CLI settings rules` now. If the topic or a must-have conflicts with one (a short about
     how a hand grips under a no-people rule), say so and ask before going on: another angle, or the user changes
     their rule. Rules for this short only go in `short.json` `rules` (`02-story.md`).
4. **Never refuse a topic** because no ready-made helper fits it. The plugin is not limited to physics: for a topic
   no field helper covers, you write the model yourself (`playbook/06-build.md`, "No helper fits"), check it against
   a sourced number, and badge anything simplified. Do refuse dangerous how-to content (see `playbook/honesty.md`)
   and say why.
5. Write what you agreed into `source/short.json` (`topic`, `audience`, `field`, `length_s`, `title`) and keep your
   own notes in `source/notes.md`.
6. `CLI state SHORT done interview`, then go straight on to the story. It refuses (one plain line saying what is
   missing) until `short.json` has a `title`, `topic`, `audience` and a `length_s` from 15 to 90.
