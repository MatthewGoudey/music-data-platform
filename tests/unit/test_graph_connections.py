"""Connection rules (companion spec 4 and 12): shared-artist albums left out (joint credits too),
each node counted once, busy nodes damped, the card lines' shapes."""

from __future__ import annotations

import pytest

from musicdata.graph.connections import (
    Album,
    Graph,
    Link,
    Pointer,
    artist_parts,
    connect,
    damp,
    role_text,
    role_weight,
)


def album(eid: int, title: str, artist: str, sessions: int = 0) -> Album:
    return Album(eid, eid * 10, title, artist, artist_parts(artist), sessions)


def graph(*albums: Album) -> Graph:
    return Graph(albums={a.entity_id: a for a in albums}, names={})


def test_artist_parts_split_joint_credits() -> None:
    assert artist_parts("Neil Young & Crazy Horse") >= {"neil young", "crazy horse"}
    assert artist_parts("Neil Young and the Stray Gators") >= {"neil young", "stray gators"}
    assert artist_parts(None) == frozenset()


@pytest.mark.parametrize(
    ("roles", "weight", "shown"),
    [
        (["producer", "engineer"], 1.0, "producer"),
        (["engineer"], 0.6, "engineer"),
        (["drums"], 0.6, "drums"),
        (["mastering"], 0.3, "mastering"),
        (["songwriter"], 1.0, "songwriter"),
    ],
)
def test_role_weights(roles, weight, shown) -> None:
    assert role_weight(roles) == (weight, shown)


def test_a_person_connects_and_shared_artists_are_left_out() -> None:
    cand = album(1, "Boat Songs", "MJ Lenderman")
    heard = album(2, "Keeper", "Spencer Radcliffe", sessions=3)
    own = album(3, "Ghost of Your Guitar Solo", "MJ Lenderman", sessions=5)
    joint = album(4, "Live", "MJ Lenderman & the Wind", sessions=2)
    g = graph(cand, heard, own, joint)
    g.names = {100: "Alex Farrar"}
    for a in (1, 2, 3, 4):
        g.add(Link(100, a, "person", 1.0, "producer", 0.9, a))
    score, ranked = connect(g, cand, {a.entity_id: a for a in (heard, own, joint)})
    assert [x["heard"] for x in ranked] == [20]  # only the album by another artist
    assert ranked[0]["text"] == "Alex Farrar (producer) · also on Keeper ✓"
    assert score == ranked[0]["weight"] > 0


def test_a_node_counts_once_with_its_best_role() -> None:
    cand = album(1, "Record", "Band A")
    h1, h2 = album(2, "One", "Band B", 1), album(3, "Two", "Band C", 1)
    g = graph(cand, h1, h2)
    g.names = {100: "Ralph Molina"}
    g.add(Link(100, 1, "person", 0.6, "drums", 0.9, 1))
    g.add(Link(100, 1, "person", 1.0, "producer", 0.9, 2))
    g.add(Link(100, 2, "member", 1.0, "drums", 0.9, 3))
    g.add(Link(100, 3, "person", 0.6, "drums", 0.9, 4))
    score, ranked = connect(g, cand, {2: h1, 3: h2})
    assert len(ranked) == 1 and "producer" in ranked[0]["text"]
    # listen(n) sums both heard albums' sessions: 0.5 + 0.1 × 2
    assert score == pytest.approx(1.0 * 0.9 * damp(3) * 0.7, abs=1e-3)


def test_busy_nodes_are_damped() -> None:
    assert damp(0) == 1.0
    assert damp(500) < damp(20) < damp(2) < 1.0


def test_lineage_and_cover_lines() -> None:
    cand = album(1, "Boat Songs", "MJ Lenderman")
    heard = album(2, "Harvest", "Neil Young", sessions=4)
    g = graph(cand, heard)
    g.pointers[1].append(Pointer(1, "lineage", 200, artist_parts("Neil Young"), None,
                                 "Uncut compares it to Neil Young ✓", 0.5, 7))  # fmt: skip
    g.pointers[1].append(Pointer(1, "cover", 201, artist_parts("Neil Young"), None,
                                 'Covers "Harvest Moon" (Neil Young) ✓', 0.7, 8))  # fmt: skip
    score, ranked = connect(g, cand, {2: heard})
    assert [x["kind"] for x in ranked] == ["cover", "lineage"]
    assert all(x["heard"] == 20 for x in ranked)
    assert score == pytest.approx(0.8 * 0.7 * 0.9 + 1.0 * 0.5 * 0.9, abs=1e-3)


def test_the_candidates_own_artist_is_not_a_connection() -> None:
    cand = album(1, "Solo", "Neil Young")
    heard = album(2, "American Dream", "Crosby, Stills, Nash & Young", 2)
    g = graph(cand, heard)
    g.names = {300: "Neil Young"}
    g.add(Link(300, 1, "person", 1.0, "producer", 0.9, 1))
    g.add(Link(300, 2, "person", 1.0, "vocals", 0.9, 2))
    assert connect(g, cand, {2: heard}) == (0.0, [])


def test_role_text_reads_plainly() -> None:
    assert role_text(["guitar", "lead vocals"]) == "guitar, lead vocals"
    assert role_text("Performer, Piano, Mellotron") == "piano, mellotron"
    assert role_text("Written-By") == "songwriter"
    assert role_text(None) == "credited"
