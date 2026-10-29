# 04 Render engine (approval)

Goal: the user says yes to one engine knowing what it costs in time. This is the last question before you run to
the end on your own.

1. If `settings` has `render.engine` set to `cycles` or `eevee` (the user said "always"), use it: say which in one
   line and mark the stage done with that engine. Otherwise:
2. **Check the machine:** `CLI machine` (CPU, cores, RAM, each GPU with its video memory and how much is free).
3. **Estimate:** `BLENDER estimate SHORT` renders one frame per scene at final quality in each engine and returns
   `eevee_s` and `cycles_s` for the whole short. It takes a few minutes; say so first, and run it with
   `run_in_background: true` (the Bash tool would cut it off after 2 minutes).
4. **Show both times and recommend one**, in plain words:
   - **Cycles** (path tracing): the best light, glass, metal and shadows; slower. The default for a polished short
     when the time is reasonable (a few hours at most).
   - **EEVEE** (real-time): much faster; fine for stylised or matte looks, weaker on reflections and glass. Its
     presets turn ray tracing on, so water, glass and metal still reflect the scene.
   - Example: "Cycles: about 2 h 10 min. EEVEE: about 18 min. This short is mostly glossy metal and glass, so I'd use
     Cycles. Go with Cycles?"
5. **Video memory:** run `CLI machine --need-gb 6` before recommending a GPU render (use about 4 GB for EEVEE). If it
   returns a `vram_warning`, pass it on as it is ("another program is using 11 GB of video memory: closing it would
   help") and include it in the recommendation. Do the same before any later GPU render or GPU voice run.
6. **Wait for a yes.** Then `CLI state SHORT done engine --info '{"engine": "cycles"}'` (or `eevee`). If the user
   says "always" ("always use Cycles"), also `CLI settings set render.engine cycles`.

From here on, do not ask the user anything unless a check fails that you can't fix after 3 attempts.
