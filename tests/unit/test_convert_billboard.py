"""Turning weekly Billboard 200 rows into a ranked list (scripts/convert_billboard.py)."""

from __future__ import annotations

import importlib.util
from pathlib import Path

SPEC = importlib.util.spec_from_file_location(
    "convert_billboard", Path(__file__).parents[2] / "scripts" / "convert_billboard.py"
)
cb = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(cb)


def _week(date: str, artist: str, album: str, rank: int) -> dict[str, str]:
    return {"Date": date, "Artist": artist, "Song": album, "Rank": str(rank)}


def test_lifetime_weeks_outrank_old_weeks_and_compilations_are_dropped() -> None:
    rows = [_week(f"2012-01-{d:02d}", "Adele", "21", 1) for d in range(1, 6)]
    rows += [_week(f"1990-01-{d:02d}", "Old Band", "Old Album", 50) for d in range(1, 11)]
    rows += [_week("2015-01-01", '"10,000 Maniacs"', "MTV Unplugged", 90)]
    rows += [_week("2015-01-01", "Queen", "Greatest Hits", 1)]
    rows += [_week("2015-01-01", "Soundtrack", "West Side Story", 3)]
    ranked = cb.rank_albums(rows, top=10)
    assert [(r["artist"], r["album"]) for r in ranked] == [
        ("Adele", "21"),  # 5 recent + 2 × 5 top-10 weeks = 15
        ("Old Band", "Old Album"),  # 0.3 × 10 = 3
        ("10,000 Maniacs", "MTV Unplugged"),  # 1
    ]
    first = ranked[0]
    assert (first["position"], first["year"], first["priority"]) == (1, "2012", "Essential")
    assert first["note"] == "peak #1 · 5 weeks (5 since 2005)"


def test_priority_tiers() -> None:
    assert [cb.priority(n) for n in (1, 250, 251, 1000, 1001)] == [
        "Essential",
        "Essential",
        "Recommended",
        "Recommended",
        "Deep cut",
    ]


def test_compilation_titles() -> None:
    for title in (
        "Greatest Hits",
        "The Very Best Of Sting",
        "Legend: The Best Of",
        "1",
        "Curtain Call: The Hits",
        "Now That's What I Call Music! 5",
        "Gold",
    ):
        assert cb.COMPILATION.search(title), title
    for title in ("1989", "21", "Rumours", "Goldfinger", "Abbey Road"):
        assert not cb.COMPILATION.search(title), title
