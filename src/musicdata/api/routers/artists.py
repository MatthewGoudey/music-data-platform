"""Artists: top and search, the one-stop artist page, and bulk lookup by name."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from musicdata.api.deps import Format, FormatParam, Pool, render, render_object, require_token
from musicdata.db import connection
from musicdata.identity import norm_key

router = APIRouter(prefix="/artists", tags=["artists"], dependencies=[Depends(require_token)])


@router.get("")
async def top_artists(
    pool: Pool,
    q: Annotated[str | None, Query(description="Name contains (any spelling seen).")] = None,
    limit: Annotated[int, Query(ge=1, le=1000)] = 50,
    format: FormatParam = Format.compact,
):
    """Artists by listens, most first; `q` narrows by name."""
    async with connection(pool) as conn:
        rows = await conn.fetch(
            """SELECT a.artist_id, a.name, s.listens, s.distinct_tracks, s.release_groups AS albums,
                      s.full_sessions, s.first_listened_at, s.last_listened_at
                 FROM artist a JOIN artist_stat s USING (artist_id)
                WHERE $1::text IS NULL
                   OR a.norm_key LIKE '%' || $2 || '%'
                   OR EXISTS (SELECT 1 FROM artist_alias x
                               WHERE x.artist_id = a.artist_id AND x.raw_name ILIKE '%' || $1 || '%')
                ORDER BY s.listens DESC LIMIT $3""",
            q,
            norm_key(q) if q else "",
            limit,
        )
    return render(rows, format)


@router.get("/{artist_id}")
async def artist_page(artist_id: int, pool: Pool, format: FormatParam = Format.json):
    """Stats, spellings, and every album with completion and sessions."""
    async with connection(pool) as conn:
        artist = await conn.fetchrow(
            """SELECT a.artist_id, a.name, a.mbid, s.listens, s.distinct_tracks,
                      s.release_groups AS albums, s.full_sessions,
                      s.first_listened_at, s.last_listened_at
                 FROM artist a LEFT JOIN artist_stat s USING (artist_id)
                WHERE a.artist_id = $1""",
            artist_id,
        )
        if artist is None:
            raise HTTPException(status_code=404, detail="no such artist")
        albums = await conn.fetch(
            """SELECT rg.release_group_id, rg.title, rg.primary_type, rg.first_release_year,
                      s.listens, s.tracks_heard, s.track_count, s.best_completion,
                      s.full_sessions, s.partial_sessions, s.last_listened_at
                 FROM release_group rg JOIN release_group_stat s USING (release_group_id)
                WHERE rg.artist_id = $1
                ORDER BY s.listens DESC""",
            artist_id,
        )
        aliases = await conn.fetch(
            "SELECT raw_name FROM artist_alias WHERE artist_id = $1 ORDER BY raw_name", artist_id
        )
    if format is Format.compact:
        return render(albums, format)
    body = dict(artist) | {
        "aliases": [r["raw_name"] for r in aliases],
        "albums": [dict(r) for r in albums],
    }
    return render_object(body)


class Names(BaseModel):
    names: list[str] = Field(min_length=1, max_length=500)


@router.post("/batch")
async def batch_lookup(body: Names, pool: Pool, format: FormatParam = Format.json):
    """Resolve names: an alias seen at ingest, then the key, then up to three
    trigram candidates (`match = candidate`) so nothing is guessed."""
    out: list[dict[str, object]] = []
    async with connection(pool) as conn:
        for name in body.names:
            rows = await conn.fetch(
                """SELECT a.artist_id, a.name, coalesce(s.listens, 0) AS listens
                     FROM artist_alias x JOIN artist a USING (artist_id)
                     LEFT JOIN artist_stat s USING (artist_id)
                    WHERE x.raw_name = $1""",
                name,
            )
            match = "alias"
            if not rows:
                match = "key"
                rows = await conn.fetch(
                    """SELECT a.artist_id, a.name, coalesce(s.listens, 0) AS listens
                         FROM artist a LEFT JOIN artist_stat s USING (artist_id)
                        WHERE a.norm_key = $1""",
                    norm_key(name),
                )
            if not rows:
                match = "candidate"
                rows = await conn.fetch(
                    """SELECT a.artist_id, a.name, coalesce(s.listens, 0) AS listens
                         FROM artist a LEFT JOIN artist_stat s USING (artist_id)
                        WHERE a.norm_key % $1
                        ORDER BY similarity(a.norm_key, $1) DESC LIMIT 3""",
                    norm_key(name),
                )
            if not rows:
                out.append(
                    {
                        "query": name,
                        "match": "none",
                        "artist_id": None,
                        "name": None,
                        "listens": None,
                    }
                )
            for r in rows:
                out.append({"query": name, "match": match} | dict(r))
    return render(out, format)
