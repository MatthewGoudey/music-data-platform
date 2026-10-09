"""Lists and progress (docs/QUEUE_SPEC.md section 12): `GET /lists`, `GET /lists/{slug}`,
`GET /gaps`."""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query

from musicdata.api.deps import Format, FormatParam, Pool, render, require_token
from musicdata.db import connection
from musicdata.lists.progress import By, progress

router = APIRouter(tags=["lists"], dependencies=[Depends(require_token)])

Status = Literal["heard", "started", "unheard", "needs_matching"]


@router.get("/lists")
async def lists(pool: Pool, format: FormatParam = Format.compact):
    """Every list with its weight and progress: total, resolved, heard, started, unheard."""
    async with connection(pool) as conn:
        rows = await progress(conn, "list")
        meta = {
            r["slug"]: r
            for r in await conn.fetch("SELECT slug, weight, ranked, default_priority FROM list")
        }
    return render(
        [
            dict(r)
            | {
                "weight": float(meta[r["list"]]["weight"]),
                "ranked": meta[r["list"]]["ranked"],
            }
            for r in rows
        ],
        format,
    )


@router.get("/lists/{slug}")
async def list_entries(
    slug: str,
    pool: Pool,
    status: Status | None = None,
    limit: Annotated[int, Query(ge=1, le=5000)] = 100,
    format: FormatParam = Format.compact,
):
    """One list's entries in list order, with their heard status; `needs_matching` lists
    the entries that did not resolve to an album."""
    async with connection(pool) as conn:
        if not await conn.fetchval("SELECT 1 FROM list WHERE slug = $1", slug):
            raise HTTPException(status_code=404, detail="no such list")
        rows = await conn.fetch(
            """SELECT e.position, e.raw_artist AS artist, e.raw_album AS album, e.year,
                      e.priority, e.lane_id AS lane, e.zone,
                      coalesce(st.status,
                               CASE e.resolve_status WHEN 'resolved' THEN 'needs_matching'
                                    ELSE e.resolve_status END) AS status,
                      st.full_sessions, st.partial_sessions, e.release_group_id, e.entry_id
                 FROM list_entry e JOIN list l USING (list_id)
                 LEFT JOIN list_entry_status st USING (entry_id)
                WHERE l.slug = $1 AND e.review_status = 'accepted'
                  AND ($2::text IS NULL
                       OR ($2 = 'needs_matching' AND st.entry_id IS NULL)
                       OR st.status = $2)
                ORDER BY e.position NULLS LAST, e.entry_id
                LIMIT $3""",
            slug,
            status,
            limit,
        )
    return render(rows, format)


@router.get("/gaps")
async def gaps(
    pool: Pool,
    by: Annotated[By, Query(description="list, lane (atlas) or zone (atlas)")] = "list",
    list_: Annotated[str | None, Query(alias="list", description="A list slug.")] = None,
    goal: Annotated[str | None, Query(description="canon, depth, breadth or personal.")] = None,
    lane: Annotated[str | None, Query(description="An atlas lane id, e.g. C1.")] = None,
    zone: Annotated[str | None, Query(description="An atlas zone, e.g. Core.")] = None,
    format: FormatParam = Format.compact,
):
    """Progress per list, atlas lane or atlas zone: total, resolved, heard, started,
    unheard, needs matching, % heard."""
    async with connection(pool) as conn:
        rows = await progress(conn, by, slug=list_, goal=goal, lane=lane, zone=zone)
    return render(rows, format)
