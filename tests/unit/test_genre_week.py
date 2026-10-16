from collections import Counter
from datetime import UTC, date, datetime

import pytest

from musicdata.queue import config
from musicdata.queue.genre_week import (
    CHICAGO,
    Index,
    candidates,
    choose,
    entries,
    kind_for,
    week_start,
    why_line,
)


@pytest.fixture(autouse=True)
def _small(monkeypatch):
    monkeypatch.setattr(config, "GENRE_WEEK_MIN_UNHEARD", 3)
    monkeypatch.setattr(config, "GENRE_WEEK_MAX_ALBUMS", 8)


def _index() -> Index:
    genres: dict[int, set[str]] = {}
    rg = 0

    def add(n: int, *keys: str) -> None:
        nonlocal rg
        for _ in range(n):
            rg += 1
            genres[rg] = set(keys)

    add(4, "altcountry", "folkrock")  # near: Matt finishes these
    add(4, "chamberfolk", "folkrock")  # adjacent: shares albums with a top genre
    add(4, "blackmetal")  # stretch: shares nothing
    add(20, "rock")  # umbrella: too big to be a week
    lists = {r: [("Rate Your Music: Top 5,000", r, 5000)] for r in genres}
    idx = Index(genres, {k: k for ks in genres.values() for k in ks}, lists, heard=set())
    idx.finished = Counter({"altcountry": 5, "folkrock": 6, "rock": 9})
    idx.finished_total = 12
    return idx


def test_week_starts_monday_in_chicago():
    # Monday 03:00 UTC is still Sunday evening in Chicago
    assert week_start(datetime(2026, 10, 19, 3, 0, tzinfo=UTC)) == date(2026, 10, 12)
    assert week_start(datetime(2026, 10, 21, 12, 0, tzinfo=CHICAGO)) == date(2026, 10, 19)


def test_kind_rotates_by_iso_week():
    kinds = {kind_for(date(2026, 10, 5 + 7 * i)) for i in range(3)}
    assert kinds == {"near", "adjacent", "stretch"}


def test_each_kind_finds_its_genre_and_umbrellas_stay_out():
    idx = _index()
    assert [k for k, _ in candidates(idx, "near", set())] == ["folkrock", "altcountry"]
    adjacent = candidates(idx, "adjacent", set())
    assert [k for k, _ in adjacent] == ["chamberfolk"]
    assert adjacent[0][1]["with"] == "folkrock"
    assert [k for k, _ in candidates(idx, "stretch", set())] == ["blackmetal"]
    assert all("rock" != k for kind in ("near", "adjacent", "stretch")
               for k, _ in candidates(idx, kind, set()))  # fmt: skip


def test_same_week_same_pick_and_exclusions_hold():
    idx = _index()
    week = date(2026, 10, 12)
    assert choose(idx, week, set()) == choose(idx, week, set())
    key, kind, _ = choose(idx, week, set(), "near")
    other, kind2, _ = choose(idx, week, {key}, "near")
    assert other != key and kind2 == "near"  # a reroll keeps the kind


def test_falls_back_to_another_kind():
    idx = _index()
    key, kind, _ = choose(idx, date(2026, 10, 12), {"chamberfolk"}, "adjacent")
    assert kind != "adjacent" and key != "chamberfolk"


def test_heard_albums_do_not_count_toward_the_minimum():
    idx = _index()
    idx.heard = {9, 10}  # two of black metal's four
    assert candidates(idx, "stretch", set()) == []


def test_entries_note_short_list_names():
    idx = _index()
    out = entries(idx, "blackmetal")
    assert out[0][1] == "Rate Your Music #9"


def test_why_lines():
    names = {"folkrock": "folk rock"}
    assert "finished 5 albums" in why_line("near", {"finished": 5}, names)
    assert "alongside folk rock" in why_line("adjacent", {"with": "folkrock", "shared": 4}, names)
    assert why_line("stretch", {}, names).startswith("A stretch")
