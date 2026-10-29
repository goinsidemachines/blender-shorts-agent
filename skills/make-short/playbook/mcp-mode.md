# MCP mode and script mode

Both modes use the same `source/build.py`, the same helpers and the same checks, so a scene looks the same either
way. The only difference is how the build code reaches Blender.

- **Script mode** (default): you run Blender in the background (`blender -b ... --python build.py`). Repeatable,
  needs no open window, no MCP.
- **MCP mode**: you drive an open Blender live through the official Blender MCP, and the user watches the scene
  appear.

## Which one

If the Blender MCP is connected (you have a tool like `execute_blender_code`), ask once per short: "MCP or script
mode? In MCP mode you can watch the scene being built in your open Blender." Remember the answer in
`source/notes.md`. If the MCP is not connected, use script mode and do not ask. If the user wants MCP mode but it
isn't connected, point them to the plugin's `docs/mcp-setup.md` (install, start the server, matching ports) and
continue in script mode meanwhile.

Only the official Blender MCP is supported (the one exposing `execute_blender_code`, scene summaries and
screenshots). The community `blender-mcp` add-on is not supported.

## Building in MCP mode

1. Ask the user to start from a new, empty Blender file (File > New > General), or confirm it's fine to replace
   what is open: the build replaces the scenes it creates and saves the file as the short's `blend/short.blend`.
   `build_short` refuses (a RuntimeError saying why) when the open file is another short's `.blend` (or any other
   saved file), or holds scenes this short does not name (the default "Scene" of File > New is fine): building would
   save them into this short. Relay that, and once the user has opened a new file, build again; only if they say
   the open file is fine, call `build_short(r"<short>", force=True)`.
2. Run the build through `execute_blender_code` with this one line, every time (never `import bsa` or `exec` the
   build file yourself):

   ```python
   import runpy; runpy.run_path(r"<root>/bsa_mcp_build.py")["build_short"](r"<short>")
   ```

   The open Blender keeps every module it has imported, so after a plugin update a plain `exec(build.py)` would run
   the old `bsa` code (an old `bake.tag`, say). `bsa_mcp_build.py` drops every `bsa` / `bsa.*` module, puts the
   plugin root first on `sys.path`, runs `source/build.py` and calls its `build(SHORT_DIR)`. `build()` saves `blend/short.blend`, the same file script mode makes. In MCP mode you build in the same Blender
   more than once (the storyboard, then again after narration), so `build()` must start with
   `bsa_scene.remove_scenes(names)` as in the skeleton: removing a scene leaves its objects behind with no users,
   and without the purge the rebuilt objects come back as `Stand.001`. The `contact:<name>` tags and the checks
   refer to objects by name, so the intended contacts would then be reported as overlaps. The untouched factory
   scene ("Scene" with only the default Cube, Light and Camera) is removed too; a "Scene" the user changed is kept.

   After building it switches the open window to the first shot's scene and puts a 3D viewport in camera view, so
   the user sees the shot instead of the empty default scene (its result's `showing` names the scene; nothing
   changes when Blender has no window).

   Large builds can be run one function at a time if the MCP call times out (split build.py into
   `build_<scene>()` functions that `build()` calls).
3. After an edit, rebuild through the MCP and then run `BLENDER preview SHORT --no-build` (in the background) for
   the rest of the way to a checked preview (`08-render.md`); it never runs build.py itself, so it cannot overwrite
   the file the user has open.
4. Use the MCP's scene summaries and viewport screenshots to show progress and to check things live, but the
   stills, checks, renders and edit still go through `BLENDER ...` commands on the saved `.blend`, exactly as in
   script mode. Save first (the build does), so the background Blender sees the latest scene.
5. If an MCP call fails or the connection drops, say so once and switch to script mode for the rest of the short.
