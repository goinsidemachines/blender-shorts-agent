# Contributing to blender-shorts-agent

- Run `uv run pytest` and `uv run ruff check .` before committing.
- `bsa/` runs inside Blender's bundled Python: stdlib + numpy only. No third-party packages.
- No plugin-author (goinsidemachines) branding in anything a user's video shows, unless the user is that brand or asks
  for it. By default only the user's own handle and site (their end card), or nothing.
- Commits use Conventional Commits. Never add a `Co-Authored-By` trailer or any other attribution line.
- Never push, and never create anything on GitHub, without the owner's explicit yes.
- Installing locally with `/plugin marketplace add <folder>` copies the whole folder, untracked files included (scratch
  files, local shorts, design notes): keep it clean before testing that way. Public installs come from the git repo,
  so they only ever get tracked files.
