"""The album brief's pure parts: track credits, gaps and research questions."""

from __future__ import annotations

from musicdata.graph.queries import gaps, research_questions, track_notes


def _credit(name: str, tracks, **extra) -> dict:
    return {"name": name, "roles": ["guitar"], "qualifiers": {"tracks": tracks, **extra}}


def test_credits_land_on_their_tracks_and_album_wide_ones_stay_apart() -> None:
    tracks = [
        {"position": 1, "title": "Cinnamon Girl"},
        {"position": 2, "title": "Down by the River"},
    ]
    credits = [
        _credit("Danny Whitten", ["Cinnamon Girl", "Down by the River"]),
        _credit("Jack Nitzsche", ["cinnamon girl"], album_wide=True),
        _credit("David Briggs", "album"),
        _credit("Bobby Notkoff", 3),  # a count from an import before per-track lists
    ]
    lineage = [{"recording": "Crazy Horse – Down by the River", "predicate": "covers"}]
    out, album = track_notes(tracks, credits, lineage)
    assert [c["name"] for c in out[0]["credits"]] == ["Danny Whitten", "Jack Nitzsche"]
    assert [c["name"] for c in out[1]["credits"]] == ["Danny Whitten"]
    assert [c["name"] for c in album] == ["Jack Nitzsche", "David Briggs", "Bobby Notkoff"]
    assert out[1]["lineage"] and not out[0]["lineage"]


def test_gaps_and_their_questions() -> None:
    edges = {"people": [{"predicate": "credited_on"}], "label": [{"predicate": "released_by"}]}
    missing = gaps(edges)
    assert missing == ["recording", "people", "lineage"]
    qs = research_questions(missing, "MJ Lenderman", "Boat Songs")
    assert qs[0] == "Where and when was Boat Songs recorded?"
    assert any("compare Boat Songs" in q for q in qs)
    assert gaps({}) == ["credits", "recording", "label", "people", "lineage"]
