"""Acceptance checks: one query and one threshold each, results stored in dq_result.

The table is the plan's "Acceptance checks" section, restricted to what exists in
Phase 2 (show and list checks arrive with their phases). A check returns
(observed, passed, details); `musicdata dq` stores every result and exits 1 when any
check fails, which turns the Actions run red and pushes to ntfy.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from decimal import Decimal

import asyncpg

from musicdata.db import connection
from musicdata.jobs.runs import JobFn, RunContext

Result = tuple[float | None, bool, dict[str, object]]


@dataclass(frozen=True)
class Check:
    name: str
    threshold: str
    run: Callable[[asyncpg.Connection], Awaitable[Result]]


async def _listen_count(conn: asyncpg.Connection) -> Result:
    notes = await conn.fetchval(
        """SELECT notes FROM pipeline_run
            WHERE job = 'ingest' AND status = 'ok' AND notes ? 'listenbrainz_count'
            ORDER BY started_at DESC LIMIT 1"""
    )
    if notes is None:
        return None, False, {"reason": "no successful ingest has recorded a ListenBrainz count"}
    n = json.loads(notes)
    lb, db = int(n["listenbrainz_count"]), int(n["database_count"])
    gap = abs(lb - db) / lb if lb else 0.0
    return round(gap * 100, 3), gap <= 0.005, {"listenbrainz": lb, "database": db}


async def _beyond_listenbrainz(conn: asyncpg.Connection) -> Result:
    """More stored listens than ListenBrainz has means duplicates (or deletions upstream);
    the 0.5% band above would hide a few dozen, so this one is exact."""
    observed, _, details = await _listen_count(conn)
    if observed is None:
        return None, False, details
    extra = int(details["database"]) - int(details["listenbrainz"])
    return float(max(extra, 0)), extra <= 0, details


async def _zero(conn: asyncpg.Connection, sql: str) -> Result:
    n = await conn.fetchval(sql)
    return float(n), n == 0, {}


async def _null_artist(conn: asyncpg.Connection) -> Result:
    return await _zero(conn, "SELECT count(*) FROM listen WHERE artist_id IS NULL")


async def _duplicate_keys(conn: asyncpg.Connection) -> Result:
    artists = await conn.fetchval(
        """SELECT count(*) FROM (SELECT norm_key FROM artist WHERE mbid IS NULL
                                  GROUP BY norm_key HAVING count(*) > 1) d"""
    )
    groups = await conn.fetchval(
        """SELECT count(*) FROM (SELECT artist_id, norm_key FROM release_group WHERE mbid IS NULL
                                  GROUP BY artist_id, norm_key HAVING count(*) > 1) d"""
    )
    review = await conn.fetchval(
        """SELECT count(*) FROM (SELECT artist_id, norm_key FROM release_group
                                  GROUP BY artist_id, norm_key
                                 HAVING count(*) > count(mbid) AND count(mbid) > 0) d"""
    )
    return (
        float(artists + groups),
        artists + groups == 0,
        {"artists": artists, "release_groups": groups, "unmapped_beside_mapped": review},
    )


HEAVY = """
    WITH heavy AS (
        SELECT rg.release_group_id, rg.mbid, t.source
          FROM release_group rg
          JOIN release_group_stat s USING (release_group_id)
          LEFT JOIN release_group_tracklist t USING (release_group_id)
         WHERE s.listens >= 10
    )
"""


async def _heavy_without_tracks(conn: asyncpg.Connection) -> Result:
    total, missing = await conn.fetchrow(
        HEAVY
        + """SELECT count(*), count(*) FILTER (WHERE source IS NULL OR source = 'unresolved')
               FROM heavy"""
    )
    share = missing / total if total else 0.0
    return round(share * 100, 2), share <= 0.05, {"heavy": total, "without_tracklist": missing}


async def _heavy_resolved(conn: asyncpg.Connection) -> Result:
    total, mapped = await conn.fetchrow(
        HEAVY + "SELECT count(*), count(*) FILTER (WHERE mbid IS NOT NULL) FROM heavy"
    )
    share = mapped / total if total else 1.0
    return round(share * 100, 2), share >= 0.90, {"heavy": total, "resolved": mapped}


async def _split_sessions(conn: asyncpg.Connection) -> Result:
    return await _zero(
        conn,
        """SELECT count(*)
             FROM album_session a JOIN release_group ra USING (release_group_id),
                  album_session b JOIN release_group rb USING (release_group_id)
            WHERE ra.release_group_id < rb.release_group_id
              AND ra.artist_id = rb.artist_id AND ra.norm_key = rb.norm_key
              AND a.started_at <= b.ended_at AND b.started_at <= a.ended_at""",
    )


async def _split_runs(conn: asyncpg.Connection) -> Result:
    """Reported, not gated: places where one listening run switches between two groups
    with the same album key, the trace an album split across groups leaves."""
    flips, pairs = await conn.fetchrow(
        """WITH l AS (
               SELECT l.release_group_id, rg.norm_key,
                      lag(l.release_group_id) OVER w AS prev_rg,
                      lag(rg.norm_key) OVER w AS prev_key,
                      l.listened_at - lag(l.listened_at) OVER w AS gap
                 FROM listen l JOIN release_group rg USING (release_group_id)
               WINDOW w AS (ORDER BY l.listened_at))
           SELECT count(*),
                  count(DISTINCT least(release_group_id, prev_rg) || '-'
                                 || greatest(release_group_id, prev_rg))
             FROM l
            WHERE gap < interval '30 minutes' AND norm_key = prev_key
              AND release_group_id <> prev_rg"""
    )
    return float(flips), True, {"switches": flips, "group_pairs": pairs}


async def _overshoot(conn: asyncpg.Connection) -> Result:
    resolved, over = await conn.fetchrow(
        """SELECT count(*), count(*) FILTER (WHERE distinct_tracks > 1.5 * track_count)
             FROM release_group_stat WHERE track_count IS NOT NULL"""
    )
    share = over / resolved if resolved else 0.0
    return round(share * 100, 2), share <= 0.02, {"resolved": resolved, "overshoot": over}


async def _artist_count(conn: asyncpg.Connection) -> Result:
    stat, listened = await conn.fetchrow(
        """SELECT (SELECT count(*) FROM artist_stat),
                  (SELECT count(DISTINCT artist_id) FROM listen)"""
    )
    return float(stat), stat == listened, {"artist_stat": stat, "distinct_in_listen": listened}


async def _sync_wall_time(conn: asyncpg.Connection) -> Result:
    rows = await conn.fetch(
        """SELECT DISTINCT ON (job) job, extract(epoch FROM finished_at - started_at) AS secs
             FROM pipeline_run
            WHERE trigger = 'schedule' AND status = 'ok' AND job <> 'dq'
            ORDER BY job, started_at DESC"""
    )
    if not rows:
        return None, True, {"reason": "no scheduled run yet"}
    minutes = sum(float(r["secs"]) for r in rows) / 60
    return round(minutes, 1), minutes < 15, {r["job"]: round(float(r["secs"])) for r in rows}


CHECKS = (
    Check("listen_count_vs_listenbrainz_pct", "<= 0.5", _listen_count),
    Check("listens_beyond_listenbrainz", "= 0", _beyond_listenbrainz),
    Check("listens_without_artist", "= 0", _null_artist),
    Check("duplicate_unmapped_keys", "= 0", _duplicate_keys),
    Check("heavy_albums_without_tracklist_pct", "<= 5", _heavy_without_tracks),
    Check("heavy_albums_resolved_pct", ">= 90", _heavy_resolved),
    Check("sessions_split_across_aliases", "= 0", _split_sessions),
    Check("listening_runs_split_across_groups", "report", _split_runs),
    Check("completion_overshoot_pct", "<= 2", _overshoot),
    Check("artist_stat_matches_listens", "exact", _artist_count),
    Check("daily_sync_minutes", "< 15", _sync_wall_time),
)


async def run_checks(conn: asyncpg.Connection, run_id: int | None) -> list[dict[str, object]]:
    results = []
    for check in CHECKS:
        observed, passed, details = await check.run(conn)
        await conn.execute(
            """INSERT INTO dq_result (run_id, check_name, passed, observed, threshold, details)
               VALUES ($1, $2, $3, $4, $5, $6::jsonb)""",
            run_id,
            check.name,
            passed,
            None if observed is None else Decimal(str(observed)),  # 0.2, not 0.2000…0111
            check.threshold,
            json.dumps(details, default=str),
        )
        results.append({"check": check.name, "passed": passed, "observed": observed})
    return results


def dq() -> JobFn:
    async def _run(ctx: RunContext) -> None:
        async with connection(ctx.pool) as conn:
            results = await run_checks(conn, ctx.run_id)
        failing = [r["check"] for r in results if not r["passed"]]
        ctx.rows = len(results)
        ctx.notes.update(checks=len(results), failing=failing)
        if failing:
            raise RuntimeError(f"{len(failing)} acceptance check(s) failed: {', '.join(failing)}")

    return _run
