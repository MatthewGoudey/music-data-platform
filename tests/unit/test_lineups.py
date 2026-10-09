"""Golden-file test for show lineup parsing. A failure means the parsing rules changed:
update tests/unit/golden/shows.json in the same commit, on purpose."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from musicdata.identity import clean_performer, non_artist_event, split_lineup

GOLDEN = json.loads((Path(__file__).parent / "golden" / "shows.json").read_text("utf-8"))


@pytest.mark.parametrize(("title", "expected"), GOLDEN["split_lineup"], ids=lambda v: repr(v)[:40])
def test_split_lineup(title: str, expected: list[str]) -> None:
    assert split_lineup(title) == expected


@pytest.mark.parametrize(
    ("name", "expected"), GOLDEN["clean_performer"], ids=lambda v: repr(v)[:40]
)
def test_clean_performer(name: str, expected: list) -> None:
    assert list(clean_performer(name)) == expected


@pytest.mark.parametrize(
    ("title", "expected"), GOLDEN["non_artist_event"], ids=lambda v: repr(v)[:40]
)
def test_non_artist_event(title: str, expected: bool) -> None:
    assert non_artist_event(title) is expected
