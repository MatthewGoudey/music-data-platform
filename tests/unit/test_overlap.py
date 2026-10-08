from __future__ import annotations

from musicdata.resolve.unmapped import pick_by_overlap

ALBUM = {"new threats", "mister bluebird", "better be strange"}
SINGLE = {"new threats"}


def test_the_album_wins_when_album_tracks_were_played() -> None:
    winner, _ = pick_by_overlap(
        {"mister bluebird", "new threats"}, {"album": ALBUM, "single": SINGLE}
    )
    assert winner == "album"


def test_a_tie_goes_to_review() -> None:
    winner, why = pick_by_overlap({"new threats"}, {"album": ALBUM, "single": SINGLE})
    assert winner is None and why.startswith("ambiguous")


def test_no_overlap_goes_to_review() -> None:
    winner, why = pick_by_overlap({"something else"}, {"album": ALBUM})
    assert winner is None and "no MusicBrainz candidate" in why


def test_no_candidates_goes_to_review() -> None:
    assert pick_by_overlap({"x"}, {})[0] is None
