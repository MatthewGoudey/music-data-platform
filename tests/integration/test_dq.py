"""Every acceptance check runs against a real schema and stores its result. Rolled back."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator

import asyncpg
import pytest

from musicdata.jobs.dq import CHECKS, run_checks

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


async def test_every_check_runs_and_is_stored(conn) -> None:
    results = await run_checks(conn, None)
    assert [r["check"] for r in results] == [c.name for c in CHECKS]
    stored = await conn.fetchval(
        "SELECT count(*) FROM dq_result WHERE run_id IS NULL AND checked_at = now()"
    )
    assert stored == len(CHECKS)


async def test_a_listen_without_artist_is_impossible_so_that_check_passes(conn) -> None:
    results = {r["check"]: r for r in await run_checks(conn, None)}
    assert results["listens_without_artist"]["passed"] is True
    assert results["duplicate_unmapped_keys"]["passed"] is True
