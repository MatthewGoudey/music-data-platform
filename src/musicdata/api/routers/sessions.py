"""Album sessions: the browser, and manual sessions for vinyl and shows."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from musicdata.api.deps import Format, FormatParam, Pool, WindowParam, render, require_token
from musicdata.db import connection
from musicdata.derive.sessions import FULL_AT

router = APIRouter(prefix="/sessions", tags=["sessions"], dependencies=[Depends(require_token)])


@router.get("")
async def sessions(
    pool: Pool,
    w: WindowParam,
    session_type: Annotated[Literal["full", "partial"] | None, Query()] = None,
    source: Annotated[Literal["scrobbled", "manual"] | None, Query()] = None,
    artist_id: int | None = None,
    format: FormatParam = Format.compact,
):
    """Sessions newest first, filtered by the shared window, type, source and artist."""
    async with connection(pool) as conn:
        rows = await conn.fetch(
            """SELECT s.session_id, s.started_at, a.name AS artist, rg.title AS album,
                      s.session_type, s.source, s.tracks_played, s.track_count, s.completion,
                      s.release_group_id
                 FROM album_session s
                 JOIN release_group rg USING (release_group_id)
                 JOIN artist a ON a.artist_id = rg.artist_id
                WHERE ($1::timestamptz IS NULL OR s.started_at >= $1)
                  AND ($2::timestamptz IS NULL OR s.started_at < $2)
                  AND ($3::text IS NULL OR s.session_type = $3)
                  AND ($4::text IS NULL OR s.source = $4)
                  AND ($5::int IS NULL OR rg.artist_id = $5)
                ORDER BY s.started_at DESC LIMIT $6""",
            w.start,
            w.end,
            session_type,
            source,
            artist_id,
            w.limit,
        )
    return render(rows, format)


class ManualSession(BaseModel):
    release_group_id: int
    listened_at: datetime = Field(description="When it was heard (a vinyl play, a show).")
    completion: float = Field(1.0, ge=0.25, le=1.0)
    note: str | None = Field(None, max_length=500)


@router.post("", status_code=201)
async def add_manual_session(body: ManualSession, pool: Pool):
    """Record a session the scrobbles cannot see. Derive never rebuilds manual sessions."""
    at = body.listened_at if body.listened_at.tzinfo else body.listened_at.replace(tzinfo=UTC)
    async with connection(pool) as conn:
        track_count = await conn.fetchval(
            """SELECT t.track_count FROM release_group rg
                 LEFT JOIN release_group_tracklist t USING (release_group_id)
                WHERE rg.release_group_id = $1""",
            body.release_group_id,
        )
        exists = await conn.fetchval(
            "SELECT 1 FROM release_group WHERE release_group_id = $1", body.release_group_id
        )
        if not exists:
            raise HTTPException(status_code=404, detail="no such album")
        count = track_count or 1
        row = await conn.fetchrow(
            """INSERT INTO album_session (release_group_id, started_at, ended_at, tracks_played,
                                          track_count, completion, session_type, source, note)
               VALUES ($1, $2, $2, $3, $4, $5, $6, 'manual', $7)
               ON CONFLICT (release_group_id, started_at, source) DO UPDATE
                  SET completion = EXCLUDED.completion, note = EXCLUDED.note,
                      session_type = EXCLUDED.session_type
               RETURNING session_id, session_type""",
            body.release_group_id,
            at,
            round(body.completion * count),
            count,
            round(body.completion, 3),
            "full" if body.completion >= FULL_AT else "partial",
            body.note,
        )
    return dict(row)
