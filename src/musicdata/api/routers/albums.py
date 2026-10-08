"""Albums (release groups): completion and sessions, and the one-stop album page."""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query

from musicdata.api.deps import Format, FormatParam, Pool, render, render_object, require_token
from musicdata.db import connection
from musicdata.identity import album_key

router = APIRouter(prefix="/albums", tags=["albums"], dependencies=[Depends(require_token)])

SORTS = {
    "listens": "s.listens DESC",
    "recent": "s.last_listened_at DESC",
    "completion": "s.best_completion DESC NULLS LAST, s.listens DESC",
}


@router.get("")
async def albums(
    pool: Pool,
    q: Annotated[str | None, Query(description="Title contains.")] = None,
    artist_id: int | None = None,
    min_completion: Annotated[float | None, Query(ge=0, le=1)] = None,
    max_completion: Annotated[float | None, Query(ge=0, le=1)] = None,
    primary_type: Annotated[str | None, Query(description="Album, EP, Single, ...")] = None,
    sort: Literal["listens", "recent", "completion"] = "listens",
    limit: Annotated[int, Query(ge=1, le=1000)] = 50,
    format: FormatParam = Format.compact,
):
    """Albums with listens, tracks heard of the standard tracklist, and best completion."""
    async with connection(pool) as conn:
        rows = await conn.fetch(
            f"""SELECT rg.release_group_id, a.name AS artist, rg.title, rg.primary_type,
                       rg.first_release_year, s.listens, s.tracks_heard, s.track_count,
                       s.best_completion, s.full_sessions, s.partial_sessions,
                       s.last_listened_at
                  FROM release_group rg
                  JOIN release_group_stat s USING (release_group_id)
                  JOIN artist a ON a.artist_id = rg.artist_id
                 WHERE ($1::text IS NULL OR rg.norm_key LIKE '%' || $2 || '%')
                   AND ($3::int IS NULL OR rg.artist_id = $3)
                   AND ($4::float IS NULL OR coalesce(s.best_completion, 0) >= $4)
                   AND ($5::float IS NULL OR coalesce(s.best_completion, 0) <= $5)
                   AND ($6::text IS NULL OR rg.primary_type = $6)
                 ORDER BY {SORTS[sort]} LIMIT $7""",
            q,
            album_key(q) if q else "",
            artist_id,
            min_completion,
            max_completion,
            primary_type,
            limit,
        )
    return render(rows, format)


@router.get("/{release_group_id}")
async def album_page(release_group_id: int, pool: Pool, format: FormatParam = Format.json):
    """Metadata, the standard tracklist with plays per track, sessions, and spellings seen."""
    async with connection(pool) as conn:
        album = await conn.fetchrow(
            """SELECT rg.release_group_id, rg.mbid, rg.title, a.artist_id, a.name AS artist,
                      rg.primary_type, rg.secondary_types, rg.first_release_year,
                      rg.is_compilation, rg.is_box_set, t.source AS tracklist_source,
                      t.release_mbid, t.note AS tracklist_note,
                      s.listens, s.tracks_heard, s.track_count, s.best_completion,
                      s.full_sessions, s.partial_sessions, s.first_listened_at,
                      s.last_listened_at
                 FROM release_group rg
                 JOIN artist a ON a.artist_id = rg.artist_id
                 LEFT JOIN release_group_tracklist t USING (release_group_id)
                 LEFT JOIN release_group_stat s USING (release_group_id)
                WHERE rg.release_group_id = $1""",
            release_group_id,
        )
        if album is None:
            raise HTTPException(status_code=404, detail="no such album")
        tracks = await conn.fetch(
            """SELECT t.position, t.title, t.length_ms,
                      (SELECT count(*) FROM listen l
                        WHERE l.release_group_id = t.release_group_id
                          AND (l.recording_mbid = t.recording_mbid
                               OR l.norm_title = t.norm_title)) AS plays
                 FROM release_group_track t
                WHERE t.release_group_id = $1 ORDER BY t.position""",
            release_group_id,
        )
        sessions = await conn.fetch(
            """SELECT session_id, started_at, ended_at, session_type, source, tracks_played,
                      track_count, completion
                 FROM album_session WHERE release_group_id = $1 ORDER BY started_at DESC""",
            release_group_id,
        )
        aliases = await conn.fetch(
            "SELECT DISTINCT raw_album FROM release_group_alias WHERE release_group_id = $1",
            release_group_id,
        )
    if format is Format.compact:
        return render(tracks, format)
    body = dict(album) | {
        "tracks": [dict(r) for r in tracks],
        "sessions": [dict(r) for r in sessions],
        "aliases": [r["raw_album"] for r in aliases],
    }
    return render_object(body)
