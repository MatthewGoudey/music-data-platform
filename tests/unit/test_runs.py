"""Whole sittings go to the album they played (derive/runs.py): the pure parts."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from musicdata.derive.runs import Run, choose, related, split_runs
from musicdata.derive.sessions import TrackRef, _Matcher

T0 = datetime(2026, 10, 17, 15, tzinfo=UTC)


def _listen(i: int, key: str, title: str, rg: int, *, minute: int | None = None, mbid=None):
    return {
        "listen_id": i,
        "listened_at": T0 + timedelta(minutes=4 * i if minute is None else minute),
        "artist_id": 1,
        "cur_artist": 1,
        "reported_key": key,
        "mbid": mbid,
        "norm_title": title,
        "track_name": title,
        "release_group_id": rg,
        "placed_by": None,
    }


def _group(rg: int, n: int, *, comp: bool = False):
    return {"release_group_id": rg, "artist_id": 1, "norm_key": f"g{rg}", "comp": comp,
            "track_count": n}  # fmt: skip


def _matcher(titles: list[str], mbids: list[str] | None = None) -> _Matcher:
    return _Matcher(
        [TrackRef(i, (mbids or [None] * len(titles))[i - 1], t) for i, t in enumerate(titles, 1)]
    )


def test_a_run_breaks_on_a_new_album_or_a_long_gap() -> None:
    listens = [
        _listen(1, "a", "x", 9),
        _listen(2, "a", "y", 9),
        _listen(3, "a", "z", 9),
        _listen(4, "b", "x", 9),  # another album
        _listen(5, "a", "w", 9, minute=200),  # an hour later
    ]
    runs = split_runs(listens)
    assert [len(r.listens) for r in runs] == [3]  # the short runs are dropped
    assert runs[0].titles == {"x", "y", "z"}


def test_related_keys() -> None:
    assert related(
        "cuttin grass vol 2 cowboy arms sessions", "cuttin grass vol 2 the cowboy arms sessions"
    )
    assert related("40oz to freedom", "40 oz to freedom")
    assert related("tangle", "tangle ep")
    assert related("microcastle", "microcastle weird era cont")
    assert not related("cuttin grass vol 1", "cuttin grass vol 2")  # numbers must agree
    assert not related("tangle", "tango in the night")
    assert related("tangle", "tangled")  # only a candidate: its tracklist still has to play the run
    assert related(  # Merle Haggard, 2026-10-20: no leading "A", no subtitle
        "tribute to the best damn fiddle player",
        "a tribute to the best damn fiddle player in the world or my salute to bob wills",
    )
    assert related(  # the player turned the title around
        "basement tapes complete the bootleg series vol 11",
        "bootleg series vol 11 the basement tapes complete",
    )
    assert not related("a", "an")


def test_the_scattered_album_goes_to_the_group_that_plays_it() -> None:
    """The Velvet Underground & Nico 45th Anniversary: four compilations held the plays."""
    titles = ["sunday morning", "femme fatale", "heroin", "venus in furs", "run run run"]
    run = Run("vu", [_listen(i, "vu", t, 100 + i % 3) for i, t in enumerate(titles)])
    groups = {1: _group(1, 11), 2: _group(2, 40, comp=True)}
    matchers = {1: _matcher(titles + ["european son"] * 6), 2: _matcher(titles + ["x"] * 35)}
    rg, ids = choose(run, {1, 2}, groups, matchers)
    assert rg == 1  # the compilation also places them, but the run covers 5 of its 40 tracks
    assert sorted(ids) == [0, 1, 2, 3, 4]


def test_a_compilation_played_through_is_the_compilation() -> None:
    titles = [f"hit {i}" for i in range(10)]
    run = Run("gh", [_listen(i, "gh", t, 50) for i, t in enumerate(titles[:9])])
    groups = {7: _group(7, 10, comp=True)}
    assert choose(run, {7}, groups, {7: _matcher(titles)})[0] == 7  # 9 of 10
    short = Run("gh", [_listen(i, "gh", t, 50) for i, t in enumerate(titles[:5])])
    assert choose(short, {7}, groups, {7: _matcher(titles)}) is None  # 5 of 10: home albums


def test_twins_tie_unless_the_recordings_tell_them_apart() -> None:
    titles = ["my boy", "beach life in death", "stop smoking", "sober to death"]
    run = Run(
        "twin fantasy",
        [_listen(i, "twin fantasy", t, 5, mbid=f"m{i}") for i, t in enumerate(titles)],
    )
    groups = {1: _group(1, 4), 2: _group(2, 4)}
    same = {1: _matcher(titles), 2: _matcher(titles)}
    assert choose(run, {1, 2}, groups, same) is None
    told = {1: _matcher(titles), 2: _matcher(titles, [f"m{i}" for i in range(4)])}
    assert choose(run, {1, 2}, groups, told)[0] == 2


def test_a_run_mostly_off_the_tracklist_stays() -> None:
    run = Run("mix", [_listen(i, "mix", f"t{i}", 5) for i in range(5)])
    assert choose(run, {1}, {1: _group(1, 12)}, {1: _matcher(["t0", "t1", "t2"])}) is None
