"""Firecrawl request and response handling, page depth, and facts claims (no network)."""

from __future__ import annotations

from musicdata.clients.firecrawl import parse, redact, request_body
from musicdata.graph.facts import facts_claims
from musicdata.graph.fetch import plan

KEY = "fc-" + "a1b2c3d4" * 4


def test_request_bodies_follow_the_v2_shape() -> None:
    facts = request_body("https://en.wikipedia.org/wiki/Zuma", "facts")
    assert facts["formats"][0] == "markdown" and facts["formats"][1]["type"] == "json"
    assert "musicians" in facts["formats"][1]["schema"]["properties"]
    assert facts["onlyMainContent"] is True
    band = request_body("https://x.bandcamp.com/album/y", "bandcamp")
    assert ".tralbum-credits" in band["includeTags"] and "onlyMainContent" not in band
    assert request_body("https://example.com", "plain")["formats"] == ["markdown"]


def test_parse_success_failure_and_redaction() -> None:
    ok = parse("u", "facts", 200, {"success": True, "data": {
        "markdown": "# Zuma", "json": {"producers": ["David Briggs"]},
        "metadata": {"title": "Zuma - Wikipedia", "creditsUsed": 5}}}, "")  # fmt: skip
    assert (ok.ok, ok.credits, ok.title, ok.facts) == (
        True,
        5,
        "Zuma - Wikipedia",
        {"producers": ["David Briggs"]},
    )
    no_credits = parse("u", "plain", 200, {"success": True, "data": {"markdown": "x"}}, "")
    assert no_credits.credits == 1  # the mode's cost when the API reports none
    failed = parse("u", "facts", 403, {"success": False, "error": f"bad key {KEY}"}, "")
    assert (failed.ok, failed.credits) == (False, 1)
    assert KEY not in failed.error and "fc-***" in failed.error
    assert redact(f"Bearer {KEY} rejected") == "Bearer fc-*** rejected"
    assert parse("u", "plain", 429, {"error": "rate limited"}, "").error == "HTTP 429: rate limited"


def _link(kind: str, url: str) -> dict:
    return {"kind": kind, "url": url}


LINKS = [
    _link("wikipedia", "https://en.wikipedia.org/wiki/Zuma"),
    _link("wikipedia", "https://de.wikipedia.org/wiki/Zuma"),
    _link("bandcamp", "https://x.bandcamp.com/album/zuma"),
    _link("review", "https://pitchfork.com/zuma"),
    _link("other", "https://blog.example/zuma"),
    _link("discogs", "https://www.discogs.com/master/1"),
    _link("streaming", "https://open.spotify.com/album/1"),
]


def test_depth_follows_the_atlas_priority() -> None:
    assert plan(LINKS, 0) == [
        ("https://en.wikipedia.org/wiki/Zuma", "facts"),
        ("https://x.bandcamp.com/album/zuma", "bandcamp"),
        ("https://pitchfork.com/zuma", "plain"),
    ]
    assert plan(LINKS, 2) == plan(LINKS, 0)[:2]
    assert plan(LINKS, 3) == [("https://en.wikipedia.org/wiki/Zuma", "facts")]
    assert plan(LINKS[2:], 3) == [("https://x.bandcamp.com/album/zuma", "bandcamp")]
    assert plan([_link("discogs", "https://www.discogs.com/master/1")], 0) == []


def test_the_albums_own_wikipedia_page_beats_an_atlas_citation() -> None:
    links = [
        {
            "kind": "wikipedia",
            "url": "https://en.wikipedia.org/wiki/MJ_Lenderman",
            "source": "map:v_atlas",
        },
        {
            "kind": "wikipedia",
            "url": "https://en.wikipedia.org/wiki/Boat_Songs",
            "source": "musicbrainz",
        },
    ]
    assert plan(links, 3, "Boat Songs") == [("https://en.wikipedia.org/wiki/Boat_Songs", "facts")]
    atlas_only = links[:1]  # Lucky has no article; its note cites the artist's page
    assert plan(atlas_only, 3, "Lucky") == []
    cited_album = [{**links[0], "url": "https://en.wikipedia.org/wiki/Crazy_Horse_(album)"}]
    assert plan(cited_album, 3, "Crazy Horse") == [(cited_album[0]["url"], "facts")]


def test_facts_merge_roles_per_person_and_name_places() -> None:
    facts = {
        "producers": ["David Briggs", "Neil Young"],
        "engineers_and_mixers": [{"name": "David Briggs", "role": "engineer, mixing"}],
        "musicians": [{"name": "Neil Young", "roles": ["guitar", "vocals"]}],
        "recording_locations": [{"studio_or_place": "Wally Heider Studios", "city": "Los Angeles"}],
        "recording_dates": "January 1969",
    }
    claims = facts_claims(facts, "https://en.wikipedia.org/wiki/X")
    briggs = next(c for c in claims if c.subject.name == "David Briggs")
    assert briggs.qualifiers == {"role": ["engineer", "mixing", "producer"]}
    assert briggs.evidence == "Wikipedia personnel: David Briggs — engineer, mixing, producer"
    (place,) = (c for c in claims if c.predicate == "recorded_at")
    assert place.obj.name == "Wally Heider Studios"
    assert place.qualifiers == {"dates": "January 1969", "city": "Los Angeles"}
    assert place.evidence == "Wikipedia: recorded at Wally Heider Studios, Los Angeles"
