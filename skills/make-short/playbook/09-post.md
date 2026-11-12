# 09 Post text and hand-over

You prepare everything for posting; the user uploads. Never upload or post anything yourself.

The post text is not part of the video: writing or changing it never makes the renders, the checks or the edit
stale, so write it whenever it suits, best while the final renders (`08-render.md`), and change it freely after the
final edit. Edit only the `post` block (keep the rest of short.json as it is, UTF-8).

1. **Write the post text into `short.json` `post`** (skip if you wrote it during the final render; re-read it
   against the finished video):
   - `title`: under about 60 characters, the hook as a question or a surprising statement, no clickbait the video
     does not deliver. Same honesty rules as the video.
   - `description`: two or three plain sentences: what the viewer learns, the key sourced fact, and a "Sources:"
     line with the main source links from `facts`. Add asset credits if any asset's licence asks for one (CC0 does
     not, but naming Poly Haven is a kind touch). Add "Made in Blender."
   - `hashtags`: 3 to 6, topic first (`#physics`, `#engineering`, the subject), then `#shorts` style tags. No
     plugin-author (goinsidemachines) tags unless the user is that brand or asks for them. The user's own handle
     only if they asked for it.
   - `tags`: the words people search for, most specific first.
   - `pinned_comment` and `link`: follow the user's post format. A link must be https and should be on the user's
     own website (`CLI settings show`); the pinned comment should contain it.
   - `related`: `CLI post related SHORT` lists the user's other posted shorts (`slug`, `title`, `topic_words`,
     `url`). Pick the closest by topic words and set `related` to `{"title", "url"}` with a one-line why the
     viewer would want it. Leave it `null` when nothing is close or the list is empty.
   The post text follows the user's hard rules too (`CLI settings rules`).

   **The user's post format.** The first time, run `CLI settings post-format`. When `asked` is false, ask once: "Do
   you have a usual format for your posts? Paste an earlier post and I'll save its pattern." Save what you find
   with `CLI settings set post_format '{...}'` (keys: `title_suffix`, `title_style` question or statement,
   `link_line` with `{link}`, `pinned_comment`, `always_hashtags`). If they have none, run `CLI settings set
   post_format_asked true` and never ask again. When a format is saved, follow it when writing every field.
2. `CLI validate SHORT` (the post must not break the spec), then `CLI post SHORT`: it writes `out/post-pack.md`
   with every field and file in one place and lists the problems. Fix the errors; mention the warnings.

   **Thumbnails.** Write `post.thumbnail`: `title` (one or two short lines, about 6 words), `kicker` (small text
   above, optional) and `fact` (one line, optional). A number in any of them needs a fact in `facts` that states
   it, or validate refuses it. Then run `BLENDER thumbs SHORT` with `run_in_background: true`; the candidates show
   on the Post stage in both sizes, with how each looks in the feed and any warnings. A warning "the text cards overlap" means a card sits over
   another one: shorten the title or put a line break in it. The user may leave a note on
   a candidate: change the text, or `heroes[N-1]` (`{"shot", "frame"}`), then `BLENDER thumbs SHORT --only N`.
   `--only N` remakes candidate N from short.json's Nth hero only when the candidates were made from
   `post.thumbnail.heroes`; otherwise from the candidate's own hero. To give a candidate a new frame directly, run
   `BLENDER thumbs SHORT --only N --hero <shot>@<frame>`. A frame note "Use this frame for the thumbnail" (from the
   Final timeline): pick the candidate N it replaces, run `BLENDER thumbs SHORT --only N --hero <shot>@<frame>` with
   the note's shot and frame, and answer the note. The user's pick is recorded by the page; when they say it in
   chat, run `CLI page decide SHORT thumbnail --choice N`.

   **Caption files.** The final edit writes `out/<slug>.srt`, `.vtt` and `-transcript.txt` (`08-render.md`). A
   post-only change never makes them stale, so `CLI captions SHORT` is not needed again. If the page says they are
   stale, run the final edit again.
   **Subtitles in other languages.** Ask once per short, only while `captions_translations` in short.json is None
   (nobody asked yet): "Want subtitles in other languages?" Record a no as `{}` and never ask again. For a yes,
   write each language into `captions_translations` (`{"es": {"lines": {line id: text}, "closing": text, "from":
   {line id: the English it was made from, "closing": ...}}}`, closing only when the short has a closing voice
   line). Say that the translations are your own and should be checked by a speaker for anything important. Keep
   every number (validate refuses a translation that leaves one out); right-to-left languages are fine, write the
   text as it reads. Then run `CLI validate SHORT` and `CLI captions SHORT`: it writes `out/<slug>.<lang>.srt` and
   `.vtt` with the English timing, and says why it skipped a language with problems or lines made for older
   English. If the English changes, update that language's translation (the Post stage flags it as made for the
   old English); when only the wording changed and the meaning did not, set the language's `from` to the new
   English instead of translating again, and say so. The user uploads each language in YouTube Studio →
   Subtitles; the checklist on the Post stage lists the files.

3. `CLI page SHORT --stage final` switches the page to the Final stage: the final video, the checks summary and the
   user's hard rules with how this short kept each (`rules_check`: re-read it against the finished video first);
   each finding says its shot, and a frame finding its redo command. Under the video is its timeline (shots,
   captions, sounds, findings, holds and the closing card, each at its frame; "Note at this moment" leaves a frame
   note), the fact panel "Facts behind every line" (each line's facts and sources, and every number on screen with
   the fact behind it; it opens by itself when something is flagged), and, once the user has seen an earlier final,
   "Compare with version N" to play that video beside this one. Notes from these tools are handled as SKILL.md rule
   4 says (a frame note: grab and look at that frame; "Needs a source": add a sourced fact or reword the line). If
   the page says the video was cut from an older timing, run the final edit again (`08-render.md`) before you hand
   over. Say "the page has switched to the final video"
   (`opened` false: say so and give the `url` as a link). Final is not a gate: its "Looks good" is the user's
   acknowledgement, so never wait for it. The Post stage next to it has the post text with copy buttons and the
   asset credits, the thumbnail candidates, the files to upload and a "Before you post" checklist. When the user
   wants the post text changed (in chat, or a note on the page), edit `post`, validate, and answer the note (`CLI
   page note SHORT ID done --reply "..."`): the page shows the new text by itself, and nothing else needs to run.
4. `CLI state SHORT done post`.
   After the user has posted, they tell you the link. Record it with `CLI post SHORT --posted URL --platform
   youtube` (`instagram`, `tiktok` or `other` for the others). It is only stored, so later shorts can link to this
   one (`CLI post related`). Never open the link or fetch it.
5. **Tell the user**, briefly: where the MP4 is (`out/<slug>.mp4`), that the review page has the title, description
   and hashtags ready to copy, any warnings you accepted and why, and (if the short is captions-only because a voice
   failed) that a voice can be added later (`05-narration.md`). The same file works for YouTube Shorts, Instagram
   Reels and TikTok.
6. **Plugin feedback, at the very end.** Run `CLI feedback list SHORT`. Only when `ask` is true (the setting is
   `ask`, there are notes nobody has seen yet, and this short has not been asked before), ask once, in one line:
   "I noted 2 problems in the plugin itself while making this (1 blocking, 1 rough edge). Want to see them and
   maybe send them to the plugin's authors?"
   - **Yes:** `CLI feedback report SHORT`: from now on the page's Post stage shows the feedback panel (it only
     appears after this yes). Then `CLI page SHORT --stage post` and say the page has switched to it (`opened`
     false: say so and give the `url` as a link). Say that the panel shows exactly what would be sent, with paths,
     keys and their settings left out; "Open on GitHub" opens a prefilled issue, and they review it and click
     Submit themselves (or just close it). If `shortened` is true, say the full text is on the page to copy and
     paste.
   - **No:** `CLI feedback decline SHORT`, and leave it there.
   Never submit for them, never open GitHub yourself, and never ask again for this short.
