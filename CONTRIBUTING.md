# Contributing

Thanks for helping. Bug reports, new checks, field helpers (physics, biology, anything with motion) and playbook
improvements are all welcome.

## Set up

You need [uv](https://docs.astral.sh/uv/) and Blender 4.5 or newer.

```
uv run pytest        # the full suite, Blender scene tests included
uv run ruff check .
```

Run both before you open a pull request. Slow tests (the end-to-end pendulum short, the local voice install) are
marked `slow`; run them with `uv run pytest -m slow` when you touch the render, edit or voice code.

## How the code is laid out

- `skills/make-short/SKILL.md` and `skills/make-short/playbook/`: what the agent reads. A change in behaviour needs a
  change here too, in plain short sentences.
- `bsa/`: runs inside Blender's bundled Python, so standard library and numpy only. No third-party packages.
- `bsa_cli.py` and `bsa_blender.py`: the commands. Each prints one JSON result; failures are one plain line.
- `tests/`: one test per behaviour. Write the failing test first.

## Rules the plugin keeps

- No music in any short, ever. Sound effects only.
- No plugin-author branding in a user's video, unless the user asks for it.
- Never download anything without showing the user what, from where, its licence and its size first.
- The user's hard rules are kept in their own words: never widened, narrowed or reinterpreted.
- Nothing in the repo is specific to one person's machine (paths, settings, voices): `tests/test_install.py`
  checks paths.

## Pull requests

- Use [Conventional Commits](https://www.conventionalcommits.org/) (`fix(checks): ...`, `feat(sound): ...`).
- Keep each pull request to one change, with its tests and playbook update.
- If you made a short with the plugin, a link in the description is the best demo there is.
