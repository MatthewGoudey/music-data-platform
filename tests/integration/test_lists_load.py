"""Loading a list into a real database: idempotent, and a reload keeps resolutions.
Rolled back after each test."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from pathlib import Path

import asyncpg
import pytest

from musicdata.jobs.dq import _lists_match_files
from musicdata.lists.load import load_list
from musicdata.lists.seed import ListSpec, read_list

pytestmark = pytest.mark.integration


@pytest.fixture
async def conn() -> AsyncIterator[asyncpg.Connection]:
    c = await asyncpg.connect(os.environ["DATABASE_URL"])
    tx = c.transaction()
    await tx.start()
    try:
        yield c
    finally:
        await tx.rollback()
        await c.close()


def _file(tmp_path: Path, body: str) -> ListSpec:
    f = tmp_path / "seeds" / "lists" / "zz.csv"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text("position,artist,album,year,priority,genre,descriptors,note\n" + body, "utf-8")
    return ListSpec(
        "zz_test_list", "Zz Test", "canon", 1.0, True, "Recommended", "seeds/lists/zz.csv", None
    )


async def test_a_reload_is_idempotent_and_keeps_resolution(conn, tmp_path: Path) -> None:
    spec = _file(tmp_path, "1,Zz Band,Zz Album,2001,,,,\n2,Zz Band,Zz Other,2003,,,,\n")
    first = await load_list(conn, read_list(spec, tmp_path))
    assert (first["entries"], first["added"]) == (2, 2)
    await conn.execute(
        """UPDATE list_entry SET resolve_status = 'resolved'
            WHERE raw_album = 'Zz Album'
              AND list_id = (SELECT list_id FROM list WHERE slug = 'zz_test_list')"""
    )
    spec = _file(tmp_path, "1,Zz Band,Zz Album,2001,Essential,,,\n2,Zz Band,Zz Other,2003,,,,\n")
    again = await load_list(conn, read_list(spec, tmp_path))
    assert (again["added"], again["no_longer_in_file"]) == (0, 0)
    row = await conn.fetchrow(
        "SELECT priority, resolve_status FROM list_entry WHERE raw_album = 'Zz Album'"
    )
    assert dict(row) == {"priority": "Essential", "resolve_status": "resolved"}
    _, passed, details = await _lists_match_files(conn)
    assert "zz_test_list" not in details["mismatched"]


async def test_an_entry_dropped_from_the_file_is_counted_not_deleted(conn, tmp_path: Path) -> None:
    spec = _file(tmp_path, "1,Zz Band,Zz Album,2001,,,,\n2,Zz Band,Zz Other,2003,,,,\n")
    await load_list(conn, read_list(spec, tmp_path))
    spec = _file(tmp_path, "1,Zz Band,Zz Album,2001,,,,\n")
    result = await load_list(conn, read_list(spec, tmp_path))
    assert result["no_longer_in_file"] == 1
    n = await conn.fetchval(
        """SELECT count(*) FROM list_entry
            WHERE list_id = (SELECT list_id FROM list WHERE slug = 'zz_test_list')"""
    )
    assert n == 2
    observed, passed, details = await _lists_match_files(conn)
    assert details["mismatched"]["zz_test_list"] == [2, 1] and not passed
