"""Listening history: totals, a timeline, and the latest plays, over one shared window."""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query

from musicdata.api.deps import Format, FormatParam, Pool, WindowParam, render, require_token
from musicdata.db import connection

router = APIRouter(prefix="/listens", tags=["listens"], dependencies=[Depends(require_token)])

IN_WINDOW = "($1::timestamptz IS NULL OR l.listened_at >= $1) AND ($2::timestamptz IS NULL OR l.listened_at < $2)"


@router.get("/summary")
async def summary(pool: Pool, w: WindowParam, format: FormatParam = Format.compact):
    """Totals in the window: listens, artists, albums, tracks, and album sessions."""
    async with connection(pool) as conn:
        row = await conn.fetchrow(
            f"""SELECT count(*) AS listens,
                       count(DISTINCT l.artist_id) AS artists,
                       count(DISTINCT l.release_group_id) AS albums,
                       count(DISTINCT (l.artist_id, l.norm_title)) AS tracks,
                       min(l.listened_at) AS first_listen,
                       max(l.listened_at) AS last_listen,
                       (SELECT count(*) FROM album_session s
                         WHERE s.session_type = 'full'
                           AND ($1::timestamptz IS NULL OR s.started_at >= $1)
                           AND ($2::timestamptz IS NULL OR s.started_at < $2)) AS full_sessions
                  FROM listen l WHERE {IN_WINDOW}""",
            w.start,
            w.end,
        )
    return render([row], format)


@router.get("/timeline")
async def timeline(
    pool: Pool,
    w: WindowParam,
    period: Annotated[Literal["day", "week", "month", "year"], Query()] = "month",
    format: FormatParam = Format.compact,
):
    """Listens, artists and albums per period, oldest first."""
    async with connection(pool) as conn:
        rows = await conn.fetch(
            f"""SELECT date_trunc($3, l.listened_at AT TIME ZONE 'UTC')::date AS period,
                       count(*) AS listens,
                       count(DISTINCT l.artist_id) AS artists,
                       count(DISTINCT l.release_group_id) AS albums
                  FROM listen l WHERE {IN_WINDOW}
                 GROUP BY 1 ORDER BY 1""",
            w.start,
            w.end,
            period,
        )
    return render(rows, format)


@router.get("/recent")
async def recent(pool: Pool, w: WindowParam, format: FormatParam = Format.compact):
    """The latest plays, newest first."""
    async with connection(pool) as conn:
        rows = await conn.fetch(
            f"""SELECT l.listened_at, a.name AS artist, l.track_name AS track,
                       rg.title AS album, l.artist_id, l.release_group_id
                  FROM listen l
                  JOIN artist a USING (artist_id)
                  LEFT JOIN release_group rg USING (release_group_id)
                 WHERE {IN_WINDOW}
                 ORDER BY l.listened_at DESC LIMIT $3""",
            w.start,
            w.end,
            w.limit,
        )
    return render(rows, format)
