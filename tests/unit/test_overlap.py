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


def test_near_titles_with_equal_numbers_match() -> None:
    from musicdata.resolve.unmapped import titles_match

    assert titles_match("luv sic pt3", "luv sic part 3")
    assert titles_match("feather", "feather")
    assert not titles_match("song part 1", "song part 2")
    assert not titles_match("intro", "outro")
    assert not titles_match("abc", "abd")


def test_a_near_title_counts_as_overlap() -> None:
    winner, _ = pick_by_overlap({"luv sic pt3"}, {"modal soul": {"feather", "luv sic part 3"}})
    assert winner == "modal soul"


def test_no_candidates_says_so() -> None:
    _, why = pick_by_overlap({"x"}, {})
    assert why.startswith("no MusicBrainz search hit")


def test_edition_titles_are_searched_again_without_the_edition() -> None:
    from musicdata.resolve.unmapped import search_titles

    assert search_titles("DAMN. COLLECTORS EDITION.") == [
        "DAMN. COLLECTORS EDITION.",
        "DAMN. COLLECTORS",
        "DAMN.",
    ]
    assert search_titles("Rumours (Super Deluxe)") == ["Rumours (Super Deluxe)", "Rumours"]
    assert search_titles("Modal Soul") == ["Modal Soul"]
    assert search_titles("Deluxe") == ["Deluxe"]  # an edition word alone is the title


def test_a_title_only_hit_needs_two_shared_titles() -> None:
    winner, why = pick_by_overlap({"pop song"}, {"other pop": {"pop song", "b"}}, min_shared=2)
    assert winner is None and why.startswith("weak")
    winner, _ = pick_by_overlap({"a", "b"}, {"comp": {"a", "b", "c"}}, min_shared=2)
    assert winner == "comp"


def test_artist_searches_narrow_then_widen() -> None:
    from musicdata.resolve.unmapped import artist_searches

    assert artist_searches("Rav, Kill Bill: The Rapper", None) == [
        ("Rav, Kill Bill: The Rapper", None),
        ("Rav", None),
        (None, None),
    ]
    assert artist_searches("Nujabes", "mbid-1") == [("Nujabes", "mbid-1"), (None, None)]
