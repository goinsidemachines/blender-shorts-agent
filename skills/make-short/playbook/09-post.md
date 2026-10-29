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
   The post text follows the user's hard rules too (`CLI settings rules`).
2. `CLI validate SHORT` (the post must not break the spec).
3. `CLI review final SHORT --open`: the final video, the checks summary, the user's hard rules with how this short
   kept each (`rules_check`: re-read it against the finished video first), the post text with copy buttons, and the
   asset credits (each finding says its shot, and a frame finding its redo command). When the user wants the post
   text changed, edit `post`, validate and run this again: nothing else needs to run.
4. `CLI state SHORT done post`.
5. **Tell the user**, briefly: where the MP4 is (`out/<slug>.mp4`), that the review page has the title, description
   and hashtags ready to copy, any warnings you accepted and why, and (if the short is captions-only because a voice
   failed) that a voice can be added later (`05-narration.md`). The same file works for YouTube Shorts, Instagram
   Reels and TikTok.
