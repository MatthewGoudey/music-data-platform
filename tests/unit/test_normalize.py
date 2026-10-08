"""Golden-file test for the identity normalizers.

If this fails you changed identity semantics. That may be right, but it means
existing keys in the database no longer match, so plan a re-key before merging.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from musicdata.identity import album_key, norm_key, split_featured, title_key

GOLDEN = json.loads((Path(__file__).parent / "golden" / "normalize.json").read_text("utf-8"))


@pytest.mark.parametrize(("raw", "expected"), GOLDEN["norm_key"], ids=lambda v: repr(v)[:40])
def test_norm_key(raw: str, expected: str) -> None:
    assert norm_key(raw) == expected


@pytest.mark.parametrize(("raw", "expected"), GOLDEN["album_key"], ids=lambda v: repr(v)[:40])
def test_album_key(raw: str, expected: str) -> None:
    assert album_key(raw) == expected


@pytest.mark.parametrize(("raw", "expected"), GOLDEN["title_key"], ids=lambda v: repr(v)[:40])
def test_title_key(raw: str, expected: str) -> None:
    assert title_key(raw) == expected


@pytest.mark.parametrize(("raw", "expected"), GOLDEN["split_featured"], ids=lambda v: repr(v)[:40])
def test_split_featured(raw: str, expected: list) -> None:
    primary, featured = split_featured(raw)
    assert [primary, featured] == expected


def test_key_is_never_empty_for_real_input() -> None:
    for raw in ["!!!", "@", "¥$", "...", "---", "?!"]:
        assert norm_key(raw) != "", raw


def test_keys_are_idempotent() -> None:
    for raw, _ in GOLDEN["norm_key"]:
        k = norm_key(raw)
        assert norm_key(k) == k or raw == "", raw
