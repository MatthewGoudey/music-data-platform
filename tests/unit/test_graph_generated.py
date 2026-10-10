"""Generated lists (companion spec 5.4–5.6): the walk steps back in time only, leaves out the
album's own artist and counts a person once per step; the queue reads a generated list only under
the profile that names it, so default scores exactly as before."""

from __future__ import annotations

from datetime import UTC, datetime

from musicdata.graph.connections import Album, Graph, Link, Pointer, artist_parts
from musicdata.graph.generated import walk_scores
from musicdata.queue.score import Profile, score_candidates, why_part


def album(eid: int, title: str, artist: str, year: int) -> Album:
    return Album(eid, eid * 10, title, artist, artist_parts(artist), 0, year)


def test_walk_steps_back_through_people_and_lineage() -> None:
    start = album(1, "Boat Songs", "MJ Lenderman", 2022)
    older = album(2, "Rust Never Sleeps", "Neil Young", 1979)
    newer = album(3, "Rat Saw God", "Wednesday", 2023)
    own = album(4, "Ghost of Your Guitar Solo", "MJ Lenderman", 2021)
    hop2 = album(5, "Everybody Knows This Is Nowhere", "Neil Young with Crazy Horse", 1969)
    g = Graph(albums={a.entity_id: a for a in (start, older, newer, own, hop2)}, names={})
    g.names = {100: "Xandy Chelmis", 200: "Ralph Molina"}
    for a in (1, 3, 4):
        g.add(Link(100, a, "person", 0.6, "pedal steel", 0.9, a))
    g.pointers[1].append(Pointer(1, "lineage", 300, artist_parts("Neil Young"), None,
                                 "Uncut compares it to Neil Young ✓", 0.5, 9))  # fmt: skip
    g.add(Link(200, 2, "person", 0.6, "drums", 0.9, 20))
    g.add(Link(200, 5, "member", 1.0, "drums", 0.9, 21))
    scores = walk_scores(g, start)
    # Neil Young's older records (his joint credit included); not the newer Wednesday record
    # Xandy Chelmis also played on, and not Lenderman's own earlier record.
    assert set(scores) == {20, 50}
    assert scores[20][1] == "Uncut compares it to Neil Young"

    # Compared to one album only: the 1969 record is then a second step, through Ralph
    # Molina, at half weight.
    g.pointers[1][0] = Pointer(1, "lineage", 2, frozenset(), 2,
                               "Uncut compares it to Rust Never Sleeps ✓", 0.5, 9)  # fmt: skip
    scores = walk_scores(g, start)
    assert set(scores) == {20, 50}
    assert scores[50][1] == "Ralph Molina (drums) on both (via Rust Never Sleeps)"
    assert scores[50][0] < scores[20][0]


def entry(rg: int, slug: str, generated: bool) -> dict:
    return {
        "release_group_id": rg, "list_id": 1, "slug": slug, "list_name": slug, "goal": "depth",
        "weight": 1.0, "ranked": False, "default_priority": None, "position": None,
        "priority": None, "lane_id": None, "lane_name": None, "zone": None, "layer": None,
        "start_here": False, "year": 2000, "descriptors": None, "raw_artist": f"A{rg}",
        "raw_album": f"B{rg}", "status": "unheard", "tracks_heard": None, "track_count": None,
        "hidden": False, "pinned": False, "snoozed_until": None, "title": f"B{rg}", "mapped": True,
        "first_release_year": 2000, "primary_type": "Album", "artist_id": rg, "artist": f"A{rg}",
        "artist_listens": 0, "bumped_until": None, "note": "Following Ralph Molina · drums",
        "generated": generated,
    }  # fmt: skip


def test_generated_lists_only_count_where_named() -> None:
    entries = [entry(1, "canon_list", False), entry(2, "following", True)]
    kw = dict(list_sizes={1: 2}, lane_shares={}, tags={}, now=datetime(2026, 10, 10, tzinfo=UTC))
    default = score_candidates(entries, Profile("default"), **kw)
    assert [i.release_group_id for i in default] == [1]
    following = score_candidates(
        entries, Profile("following", filters={"lists": ["following"]}), **kw
    )
    assert [i.release_group_id for i in following] == [2]
    assert why_part(following[0].why[0]) == "Following Ralph Molina · drums"
