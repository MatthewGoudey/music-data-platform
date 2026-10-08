"""POST /query: read-only SQL for the long tail of questions.

Three guards: the statement runs in a READ ONLY transaction (writes and DDL fail),
`statement_timeout` is 10 s, and at most ROW_CAP rows come back. One statement per
call (the extended protocol rejects a second). When DATABASE_URL_READONLY is set, the
query also runs as that read-only role through its own pool.
"""

from __future__ import annotations

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from musicdata.api.deps import Format, FormatParam, Pool, render, require_token
from musicdata.db import connection

router = APIRouter(tags=["query"], dependencies=[Depends(require_token)])

ROW_CAP = 1000
TIMEOUT = "10s"


class Sql(BaseModel):
    sql: str = Field(min_length=1, max_length=20_000, description="One SELECT statement.")


@router.post("/query")
async def query(body: Sql, request: Request, pool: Pool, format: FormatParam = Format.json):
    """Run one read-only statement; returns up to 1,000 rows."""
    target = request.app.state.readonly_pool or pool
    async with connection(target) as conn:
        try:
            async with conn.transaction(readonly=True):
                await conn.execute(f"SET LOCAL statement_timeout = '{TIMEOUT}'")
                stmt = await conn.prepare(body.sql)
                if stmt.get_attributes():
                    cursor = await stmt.cursor()
                    rows = await cursor.fetch(ROW_CAP + 1)
                else:  # a statement with no result columns
                    await stmt.fetch()
                    rows = []
        except (asyncpg.PostgresError, asyncpg.InterfaceError) as exc:
            raise HTTPException(status_code=400, detail=f"{type(exc).__name__}: {exc}") from exc
    truncated = len(rows) > ROW_CAP
    response = render(rows[:ROW_CAP], format)
    response.headers["X-Row-Count"] = str(min(len(rows), ROW_CAP))
    response.headers["X-Truncated"] = str(truncated).lower()
    return response
