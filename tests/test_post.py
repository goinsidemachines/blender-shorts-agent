"""The post block: fields, platform limits, thumbnail text and the user's own post format."""

import copy
from pathlib import Path

import pytest

from bsa import post
from bsa.short import Fact, load_short

SOURCE = Path(__file__).parent / "e2e" / "pendulum" / "source" / "short.json"
GOOD_HASHTAGS = ["#physics", "#pendulum", "#blender"]


def short_with(**fields):
    short = load_short(SOURCE)
    short.post = {**copy.deepcopy(short.post), **fields}
    return short


def test_old_post_blocks_still_validate():
    assert post.problems(load_short(SOURCE), {}) == ([], [])


def test_fields_fill_every_field():
    p = post.fields(load_short(SOURCE))
    assert p["tags"] == [] and p["link"] == "" and p["related"] is None and p["pinned_comment"] == ""


def test_title_limits():
    errors, _ = post.problems(short_with(title="x" * 101), {})
    assert any("YouTube allows 100" in e for e in errors)
    errors, warnings = post.problems(short_with(title="x" * 61), {})
    assert errors == [] and any("cut off in the feed" in w for w in warnings)


def test_description_fits_by_platform():
    assert post.fits("x" * 2201) == ["YouTube"] and post.fits("x" * 10) == ["YouTube", "Instagram", "TikTok"]
    assert post.problems(short_with(description="x" * 5001), {})[0]
    _, warnings = post.problems(short_with(description="x" * 2201), {})
    assert any("Instagram and TikTok (2,200)" in w for w in warnings)


def test_youtube_tag_length_counts_quotes_and_commas():
    assert post.tags_length(["pendulum", "how clocks work"]) == 26 and post.tags_length([]) == 0
    errors, _ = post.problems(short_with(tags=["a" * 250, "b" * 251]), {})
    assert any("post tags take 502 of YouTube's 500 characters" in e for e in errors)


def test_hashtags_are_single_words_three_to_six():
    assert post.problems(short_with(hashtags=["#a b"]), {})[0]
    errors, warnings = post.problems(short_with(hashtags=["#a", "#b"]), {})
    assert errors == [] and len(warnings) == 1
    assert post.problems(short_with(hashtags=["#a", "#b", "#c", "#d"]), {}) == ([], [])


def test_pinned_comment_without_the_link_warns():
    s = {"website": "example.com"}
    link = "https://example.com/p"
    _, w = post.problems(short_with(link=link, pinned_comment="Nothing here"), s)
    assert any("pinned comment" in x for x in w)
    assert post.problems(short_with(link=link, pinned_comment=f"See {link}"), s) == ([], [])


def test_link_must_be_https_and_on_the_users_site():
    assert post.problems(short_with(link="http://x.com"), {})[0]
    s = {"website": "example.com"}
    assert post.problems(short_with(link="https://www.example.com/p"), s) == ([], [])
    assert post.problems(short_with(link="https://blog.example.com/p"), s) == ([], [])
    assert post.problems(short_with(link="https://other.org"), s)[1]
    assert post.problems(short_with(link="https://example.com"), {})[1]  # no website set
    assert post.problems(short_with(related={"title": "t", "url": "http://x"}), {})[0]


def test_post_format_is_checked_mechanically():
    s = {"post_format": {"title_suffix": " #shorts", "title_style": "question", "always_hashtags": ["#shorts"]}}
    assert len(post.problems(short_with(title="Why does it swing"), s)[1]) == 3
    ok = short_with(title="Why does it swing? #shorts", hashtags=[*GOOD_HASHTAGS, "#Shorts"])
    assert post.problems(ok, s) == ([], [])


def with_thumbnail(**over):
    short = load_short(SOURCE)
    base = {"title": ["Two rows,", "one pull"], "kicker": "How it works", "fact": None,
            "heroes": [{"shot": short.shots[0].id, "frame": 10, "crop": None}], "chosen": None}  # fmt: skip
    short.post = {**short.post, "thumbnail": {**base, **over}}
    return short


def test_thumbnail_text_shape_words_and_numbers():
    assert post.problems(with_thumbnail(), {}) == ([], [])
    assert post.problems(with_thumbnail(title=["a", "b", "c"]), {})[0]
    errors, warnings = post.problems(with_thumbnail(title=["one two three four", "five six seven eight"]), {})
    assert errors == [] and any("words" in w for w in warnings)
    short = with_thumbnail(fact="Teeth made to 0.01 mm")
    errors, _ = post.problems(short, {})
    assert any("post thumbnail fact shows 0.01, which no fact states" in e for e in errors)
    short.facts.append(Fact(id="f99", claim="spaced 0.01 mm apart", source="x"))
    assert post.problems(short, {})[0] == []
    assert post.problems(with_thumbnail(heroes=[{"shot": "s99", "frame": 1, "crop": None}]), {})[0]
    hero = {"shot": load_short(SOURCE).shots[0].id, "frame": 1, "crop": [0, 0, 1200, 10]}
    assert post.problems(with_thumbnail(heroes=[hero]), {})[0]
    assert post.problems(with_thumbnail(heroes=[{**hero, "crop": [0, 0, 1080, 10]}]), {})[0] == []
    assert post.problems(with_thumbnail(heroes=[{**hero, "frame": 0}]), {})[0]
    assert post.problems(with_thumbnail(kicker=3), {})[0]


def test_trailing_newline_is_not_a_hashtag_and_https_is_case_blind():
    assert post.problems(short_with(hashtags=["#a\n", "#b", "#c"]), {})[0]
    with pytest.raises(ValueError):
        post.format_value({"always_hashtags": ["#a\n"]})
    assert post.problems(short_with(link="HTTPS://example.com/p"), {"website": "example.com"}) == ([], [])
    assert post.problems(short_with(related={"title": "t", "url": "HTTPS://x.org"}), {})[0] == []


def test_posted_must_be_platform_url_at():
    assert post.problems(short_with(posted=[{"platform": "youtube"}]), {})[0]
    entry = {"platform": "youtube", "url": "https://y.example/1", "at": "2026-11-03"}
    assert post.problems(short_with(posted=[entry]), {}) == ([], [])


def test_counts_say_ok_warn_or_over():
    c = post.counts(short_with(title="x" * 70))["title"]
    assert c == {"count": 70, "max": 100, "warn_over": 60, "state": "warn"}
    assert post.counts(short_with(title="x" * 101))["title"]["state"] == "over"
    assert post.counts(short_with(title="x"))["title"]["state"] == "ok"
    assert post.counts(short_with(hashtags=["#a", "#b"]))["hashtags"]["state"] == "warn"
    assert post.counts(short_with(hashtags=[f"#h{i}" for i in range(7)]))["hashtags"]["state"] == "warn"
    assert post.counts(short_with(hashtags=GOOD_HASHTAGS))["hashtags"]["state"] == "ok"


def test_format_value_rejects_bad_values():
    for bad in ({"bogus": 1}, {"title_style": "shout"}, {"link_line": 3}, {"always_hashtags": ["nohash"]}, []):
        with pytest.raises(ValueError):
            post.format_value(bad)
    assert post.format_value({"always_hashtags": ["#shorts"]}) == {"always_hashtags": ["#shorts"]}


def test_thumbnail_texts_and_rule_places():
    short = with_thumbnail(fact="A fact")
    assert post.thumbnail_texts(short.post) == [
        ("post thumbnail title", "Two rows, one pull"),
        ("post thumbnail kicker", "How it works"),
        ("post thumbnail fact", "A fact"),
    ]
    assert ("post title", short.post["title"]) in post.rule_places(short)
