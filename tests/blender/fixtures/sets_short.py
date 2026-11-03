"""A two-shot Short for the set tests: shot "s1" in the main scene (the demo lever, a title card and a caption),
shot "s2" in "Close" (a close-up of the crate)."""

from bsa.short import Line, Short, Shot


def two_shot(main: str = "Scene", slug: str = "lever") -> Short:
    shots = [
        Shot(id="s1", title="The lever", picture="the lever rocks", scene=main, min_s=1.0, title_card="A lever"),
        Shot(id="s2", title="Close", picture="the crate up close", scene="Close", min_s=1.0),
    ]
    lines = [Line("l1", "A small weight lifts a heavy crate.", "s1"), Line("l2", "Look closer.", "s2")]
    return Short(slug=slug, title="Lever", length_s=2, shots=shots, lines=lines, style={})
