"""Choosing a release group for a list entry from MusicBrainz search hits."""

from __future__ import annotations

from musicdata.lists.match import candidate, choose, credit_matches
from musicdata.resolve.manual import pick


def _credit(name: str, joinphrase: str = "", artist: str | None = None) -> dict:
    return {"name": name, "joinphrase": joinphrase, "artist": {"id": "a1", "name": artist or name}}


def _hit(
    mbid: str, title: str, year: str | None, kind: str = "Album", secondary=(), artist="Weezer"
):
    return {
        "id": mbid,
        "title": title,
        "first-release-date": year,
        "primary-type": kind,
        "secondary-types": list(secondary),
        "artist-credit": [_credit(artist)],
    }


def test_the_credit_names_the_artist() -> None:
    assert credit_matches([_credit("The Beatles")], "beatles")
    assert credit_matches([_credit("Prince", " & "), _credit("The Revolution")], "prince")
    assert credit_matches([_credit("Lou Reed", " & "), _credit("Metallica")], "metallica")
    assert not credit_matches([_credit("Princess Chelsea")], "prince")


def test_a_hit_needs_the_type_artist_and_title() -> None:
    assert candidate(_hit("1", "Weezer", "1994"), "weezer", "weezer")
    assert candidate(_hit("1", "Weezer", "1994", kind="Single"), "weezer", "weezer") is None
    assert candidate(_hit("1", "Pinkerton", "1996"), "weezer", "weezer") is None
    assert candidate(_hit("1", "Weezer", "1994", artist="Rivers"), "weezer", "weezer") is None


def _cands(*hits: dict, key: str = "weezer"):
    return [c for h in hits if (c := candidate(h, "weezer", key))]


def test_the_year_picks_between_self_titled_albums() -> None:
    hits = (_hit("blue", "Weezer", "1994-05-10"), _hit("green", "Weezer", "2001-05-15"))
    assert choose(_cands(*hits), 2001)[1][0].mbid == "green"
    assert choose(_cands(*hits), 1995)[1][0].mbid == "blue"
    status, tied = choose(_cands(*hits), None)
    assert status == "ambiguous" and {c.mbid for c in tied} == {"blue", "green"}


def test_a_plain_album_beats_a_live_one_and_an_ep() -> None:
    hits = (
        _hit("live", "Weezer", "1994", secondary=("Live",)),
        _hit("ep", "Weezer", "1994", kind="EP"),
        _hit("album", "Weezer", "1994"),
    )
    assert choose(_cands(*hits), 1994) == ("resolved", [_cands(hits[2])[0]])


def test_a_live_album_still_resolves_when_it_is_the_only_one() -> None:
    hits = (_hit("live", "Weezer", "1994", secondary=("Live",)),)
    assert choose(_cands(*hits), 1994)[0] == "resolved"


def test_nothing_matching_is_unresolved() -> None:
    assert choose([], 1994) == ("unresolved", [])


def test_manual_rows_prefer_the_plain_title_on_a_conflict() -> None:
    rows = [
        {"album": "Magnolia Electric Co.", "track_count": "9"},
        {"album": "Magnolia Electric Co. (Deluxe Edition)", "track_count": "19"},
    ]
    assert pick(rows)["track_count"] == "9"
    assert pick([{"album": "A", "track_count": "9"}, {"album": "B", "track_count": "10"}]) is None
