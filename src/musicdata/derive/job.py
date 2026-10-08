"""The derive job: album sessions (incremental) and the two stat tables (rebuilt).

A release group is rebuilt when anything under it changed since the last derive run:
new listens, a new tracklist, or new metadata (a merge bumps `updated_at`). `full`
rebuilds every group. Each chunk of groups is replaced in one transaction, so a reader
never sees a group half-rebuilt. Manual sessions are never touched.
"""

from __future__ import annotations

from collections import defaultdict

import asyncpg

from musicdata.db import connection
from musicdata.derive.sessions import Play, Session, TrackRef, detect_sessions, eligible
from musicdata.jobs.runs import JobFn, RunContext
from musicdata.log import get_logger

log = get_logger(__name__)

CHUNK = 1000

LAST_RUN = """
    SELECT max(started_at) FROM pipeline_run
     WHERE job = 'derive' AND status = 'ok' AND notes ? 'sessions_written'
"""

CHANGED_SINCE = """
    SELECT release_group_id FROM listen
     WHERE loaded_at >= $1 AND release_group_id IS NOT NULL
    UNION SELECT release_group_id FROM release_group_tracklist WHERE resolved_at >= $1
    UNION SELECT release_group_id FROM release_group WHERE updated_at >= $1
"""

REBUILD_STATS = """
    TRUNCATE release_group_stat, artist_stat;

    INSERT INTO release_group_stat
    WITH heard AS (
        SELECT l.release_group_id, count(DISTINCT t.position) AS tracks_heard
          FROM listen l
          JOIN release_group_track t
            ON t.release_group_id = l.release_group_id
           AND (t.recording_mbid = l.recording_mbid OR t.norm_title = l.norm_title)
         GROUP BY l.release_group_id
    ), sess AS (
        SELECT release_group_id,
               max(completion)                                    AS best_completion,
               count(*) FILTER (WHERE session_type = 'full')      AS full_sessions,
               count(*) FILTER (WHERE session_type = 'partial')   AS partial_sessions,
               max(started_at) FILTER (WHERE session_type = 'full') AS last_full_session_at
          FROM album_session GROUP BY release_group_id
    )
    SELECT l.release_group_id,
           count(*), count(DISTINCT l.norm_title),
           CASE WHEN tl.track_count IS NOT NULL THEN coalesce(h.tracks_heard, 0) END,
           tl.track_count,
           s.best_completion,
           coalesce(s.full_sessions, 0), coalesce(s.partial_sessions, 0),
           min(l.listened_at), max(l.listened_at),
           s.last_full_session_at
      FROM listen l
      LEFT JOIN release_group_tracklist tl
             ON tl.release_group_id = l.release_group_id AND tl.source <> 'unresolved'
      LEFT JOIN heard h ON h.release_group_id = l.release_group_id
      LEFT JOIN sess s ON s.release_group_id = l.release_group_id
     WHERE l.release_group_id IS NOT NULL
     GROUP BY l.release_group_id, tl.track_count, h.tracks_heard, s.best_completion,
              s.full_sessions, s.partial_sessions, s.last_full_session_at;

    INSERT INTO artist_stat
    SELECT l.artist_id, count(*), count(DISTINCT l.norm_title),
           count(DISTINCT l.release_group_id),
           coalesce((SELECT count(*) FROM album_session s
                       JOIN release_group rg USING (release_group_id)
                      WHERE rg.artist_id = l.artist_id AND s.session_type = 'full'), 0),
           min(l.listened_at), max(l.listened_at)
      FROM listen l
     GROUP BY l.artist_id;
"""


async def _rebuild_chunk(conn: asyncpg.Connection, rg_ids: list[int]) -> int:
    meta = await conn.fetch(
        """SELECT rg.release_group_id, rg.primary_type, rg.is_compilation, rg.is_box_set,
                  t.track_count
             FROM release_group rg
             LEFT JOIN release_group_tracklist t
                    ON t.release_group_id = rg.release_group_id AND t.source <> 'unresolved'
            WHERE rg.release_group_id = ANY($1::int[])""",
        rg_ids,
    )
    ok = [
        m["release_group_id"]
        for m in meta
        if eligible(m["primary_type"], m["is_compilation"], m["is_box_set"], m["track_count"])
    ]
    tracks: dict[int, list[TrackRef]] = defaultdict(list)
    for r in await conn.fetch(
        """SELECT release_group_id, position, recording_mbid::text, norm_title
             FROM release_group_track WHERE release_group_id = ANY($1::int[])
            ORDER BY release_group_id, position""",
        ok,
    ):
        tracks[r["release_group_id"]].append(
            TrackRef(r["position"], r["recording_mbid"], r["norm_title"])
        )
    plays: dict[int, list[Play]] = defaultdict(list)
    for r in await conn.fetch(
        """SELECT release_group_id, listened_at, recording_mbid::text, norm_title
             FROM listen WHERE release_group_id = ANY($1::int[])
            ORDER BY release_group_id, listened_at""",
        ok,
    ):
        plays[r["release_group_id"]].append(
            Play(r["listened_at"], r["recording_mbid"], r["norm_title"])
        )
    found: list[tuple[int, Session]] = [
        (rg, s) for rg in ok for s in detect_sessions(plays[rg], tracks[rg])
    ]
    async with conn.transaction():
        await conn.execute(
            """DELETE FROM album_session
                WHERE source = 'scrobbled' AND release_group_id = ANY($1::int[])""",
            rg_ids,
        )
        await conn.execute(
            """INSERT INTO album_session (release_group_id, started_at, ended_at, tracks_played,
                                          track_count, completion, session_type, source,
                                          listen_count)
               SELECT rg, s, e, tp, tc, c, k, 'scrobbled', n
                 FROM unnest($1::int[], $2::timestamptz[], $3::timestamptz[], $4::int[],
                             $5::int[], $6::numeric[], $7::text[], $8::int[])
                      AS u(rg, s, e, tp, tc, c, k, n)
               ON CONFLICT (release_group_id, started_at, source) DO NOTHING""",
            [rg for rg, _ in found],
            [s.started_at for _, s in found],
            [s.ended_at for _, s in found],
            [s.tracks_played for _, s in found],
            [s.track_count for _, s in found],
            [s.completion for _, s in found],
            [s.session_type for _, s in found],
            [s.listen_count for _, s in found],
        )
    return len(found)


def derive(*, full: bool = False) -> JobFn:
    async def _run(ctx: RunContext) -> None:
        async with connection(ctx.pool) as conn:
            since = None if full else await conn.fetchval(LAST_RUN)
            if since is None:
                rows = await conn.fetch("SELECT release_group_id FROM release_group")
            else:
                rows = await conn.fetch(CHANGED_SINCE, since)
        rg_ids = sorted(r["release_group_id"] for r in rows)
        written = 0
        for i in range(0, len(rg_ids), CHUNK):
            async with connection(ctx.pool) as conn:
                written += await _rebuild_chunk(conn, rg_ids[i : i + CHUNK])
        async with connection(ctx.pool) as conn:
            async with conn.transaction():
                await conn.execute(REBUILD_STATS)
            totals = await conn.fetchrow(
                """SELECT count(*) FILTER (WHERE session_type = 'full') AS full,
                          count(*) FILTER (WHERE session_type = 'partial') AS partial
                     FROM album_session"""
            )
        ctx.rows = written
        ctx.notes.update(
            mode="full" if since is None else "incremental",
            since=since,
            release_groups_rebuilt=len(rg_ids),
            sessions_written=written,
            sessions_full=totals["full"],
            sessions_partial=totals["partial"],
        )

    return _run
