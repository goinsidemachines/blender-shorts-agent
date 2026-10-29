# 01 Interview

Goal: agree on the topic, the audience, the length, and anything the user already wants. Keep it to a few quick
questions; offer sensible defaults so the user can just say "yes".

1. **Make the short's folder:** `CLI new-short "<working title>"`. It returns the folder and the slug. Say where it
   is in one line. When the user (or their content plan) wants a particular number, add `--number N`: it refuses a
   number another short already has (or another session is making), and says which.
2. **Ask, briefly:**
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
3. **Never refuse a topic** because no ready-made helper fits it. The plugin is not limited to physics: for a topic
   no field helper covers, you write the model yourself (`playbook/06-build.md`, "No helper fits"), check it against
   a sourced number, and badge anything simplified. Do refuse dangerous how-to content (see `playbook/honesty.md`)
   and say why.
4. Write what you agreed into `source/short.json` (`topic`, `audience`, `field`, `length_s`, `title`) and keep your
   own notes in `source/notes.md`.
5. `CLI state SHORT done interview`, then go straight on to the story. It refuses (one plain line saying what is
   missing) until `short.json` has a `title`, `topic`, `audience` and a `length_s` from 15 to 90.
