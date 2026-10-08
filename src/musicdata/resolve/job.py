"""The resolve job: release groups → MusicBrainz metadata and canonical tracklists.

Most-listened groups go first, so a partial pass (`--limit`) covers the albums that
matter. Each group is one MusicBrainz request and one transaction. A group MusicBrainz
cannot answer is stored as `unresolved` and retried after RETRY_AFTER. After the mapped
groups, the unmapped tail is settled by tracklist overlap (resolve/unmapped.py).
"""

from __future__ import annotations

import asyncpg

from musicdata.clients.musicbrainz import MusicBrainzClient, NotFoundError
from musicdata.config import get_settings
from musicdata.db import connection
from musicdata.identity import norm_key
from musicdata.jobs.runs import JobFn, RunContext
from musicdata.log import get_logger
from musicdata.resolve.canonical import Resolution, resolve_group

log = get_logger(__name__)

RETRY_AFTER = "30 days"

PENDING = f"""
    FROM release_group rg
    LEFT JOIN release_group_tracklist t USING (release_group_id)
   WHERE rg.mbid IS NOT NULL
     AND (t.release_group_id IS NULL
          OR (t.source = 'unresolved' AND t.resolved_at < now() - interval '{RETRY_AFTER}'))
"""


async def _ensure_artist(conn: asyncpg.Connection, mbid: str, name: str) -> int:
    """ADR 0015 for a credit seen on MusicBrainz: by MBID, else promote, else create."""
    artist_id = await conn.fetchval("SELECT artist_id FROM artist WHERE mbid = $1", mbid)
    key = norm_key(name)
    if artist_id is None:
        artist_id = await conn.fetchval(
            """UPDATE artist SET mbid = $1, name = $2
                WHERE mbid IS NULL AND norm_key = $3 RETURNING artist_id""",
            mbid,
            name,
            key,
        )
    if artist_id is None:
        artist_id = await conn.fetchval(
            "INSERT INTO artist (name, norm_key, mbid) VALUES ($1, $2, $3) RETURNING artist_id",
            name,
            key,
            mbid,
        )
    await conn.execute(
        """INSERT INTO artist_alias (raw_name, artist_id, source)
           VALUES ($1, $2, 'musicbrainz') ON CONFLICT DO NOTHING""",
        name,
        artist_id,
    )
    return artist_id


async def write_resolution(conn: asyncpg.Connection, rg_id: int, res: Resolution) -> None:
    async with conn.transaction():
        artist_id = None
        if res.artist_mbid and res.artist_name:
            artist_id = await _ensure_artist(conn, res.artist_mbid, res.artist_name)
        await conn.execute(
            """UPDATE release_group
                  SET title = $2, norm_key = $3, primary_type = $4, secondary_types = $5,
                      first_release_year = $6, is_compilation = $7, is_box_set = $8,
                      artist_id = COALESCE($9, artist_id), updated_at = now()
                WHERE release_group_id = $1""",
            rg_id,
            res.title,
            res.norm_key,
            res.primary_type,
            list(res.secondary_types),
            res.first_release_year,
            res.is_compilation,
            res.is_box_set,
            artist_id,
        )
        await conn.execute(
            """INSERT INTO release_group_tracklist
                      (release_group_id, track_count, release_mbid, source, resolved_at, note)
               VALUES ($1, $2, $3, 'musicbrainz', now(), NULL)
               ON CONFLICT (release_group_id) DO UPDATE
                  SET track_count = EXCLUDED.track_count, release_mbid = EXCLUDED.release_mbid,
                      source = 'musicbrainz', resolved_at = now(), note = NULL""",
            rg_id,
            len(res.tracks),
            res.release_mbid,
        )
        await conn.execute("DELETE FROM release_group_track WHERE release_group_id = $1", rg_id)
        await conn.execute(
            """INSERT INTO release_group_track
                      (release_group_id, position, title, norm_title, recording_mbid, length_ms)
               SELECT $1, * FROM unnest($2::int[], $3::text[], $4::text[], $5::uuid[], $6::int[])""",
            rg_id,
            [t.position for t in res.tracks],
            [t.title for t in res.tracks],
            [t.norm_title for t in res.tracks],
            [t.recording_mbid for t in res.tracks],
            [t.length_ms for t in res.tracks],
        )


async def write_unresolved(conn: asyncpg.Connection, rg_id: int, note: str) -> None:
    await conn.execute(
        """INSERT INTO release_group_tracklist (release_group_id, source, resolved_at, note)
           VALUES ($1, 'unresolved', now(), $2)
           ON CONFLICT (release_group_id) DO UPDATE
              SET source = 'unresolved', track_count = NULL, release_mbid = NULL,
                  resolved_at = now(), note = EXCLUDED.note
            WHERE release_group_tracklist.source = 'unresolved'""",
        rg_id,
        note,
    )


def resolve(
    *,
    limit: int,
    unmapped_limit: int = 100,
    min_listens: int = 3,
    retry_unresolved: bool = False,
) -> JobFn:
    async def _run(ctx: RunContext) -> None:
        settings = get_settings()
        async with connection(ctx.pool) as conn:
            todo = await conn.fetch(
                f"""SELECT rg.release_group_id, rg.mbid::text AS mbid {PENDING}
                     ORDER BY (SELECT count(*) FROM listen l
                                WHERE l.release_group_id = rg.release_group_id) DESC,
                              rg.release_group_id
                     LIMIT $1""",
                limit,
            )
        resolved = unresolved = 0
        async with MusicBrainzClient(user_agent=settings.musicbrainz_user_agent) as mb:
            for i, row in enumerate(todo, 1):
                rg_id, mbid = row["release_group_id"], row["mbid"]
                try:
                    res = resolve_group(await mb.releases_of_group(mbid))
                    note = "no release with tracks"
                except NotFoundError:
                    res, note = None, "not in MusicBrainz (merged or deleted)"
                async with connection(ctx.pool) as conn:
                    if res is None:
                        await write_unresolved(conn, rg_id, note)
                        unresolved += 1
                    else:
                        await write_resolution(conn, rg_id, res)
                        resolved += 1
                if i % 100 == 0:
                    log.info(
                        "resolve progress",
                        extra={"done": i, "of": len(todo), "unresolved": unresolved},
                    )
            from musicdata.resolve.unmapped import resolve_unmapped

            tail = await resolve_unmapped(
                ctx.pool,
                mb,
                limit=unmapped_limit,
                min_listens=min_listens,
                retry_unresolved=retry_unresolved,
            )
            requests = mb.requests
        async with connection(ctx.pool) as conn:
            remaining = await conn.fetchval(f"SELECT count(*) {PENDING}")
        ctx.rows = resolved
        ctx.notes.update(
            limit=limit,
            resolved=resolved,
            unresolved=unresolved,
            musicbrainz_requests=requests,
            remaining=remaining,
            unmapped=tail,
        )

    return _run
