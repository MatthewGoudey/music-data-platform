"""Acceptance checks: one query and one threshold each, results stored in dq_result.

The table is the plan's "Acceptance checks" section, restricted to what exists so far
(listening, then shows; list checks arrive with Phase 4). A check returns
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
from musicdata.graph import checks as graph
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


UPCOMING_HEADLINERS = """
    FROM show_artist sa JOIN show s USING (show_id)
   WHERE sa.role = 'headliner' AND s.starts_at > now() AND NOT s.cancelled AND NOT s.non_artist
"""


async def _headliner_residue(conn: asyncpg.Connection) -> Result:
    """Audit E1: listing debris left in a headliner's name."""
    n = await conn.fetchval(
        rf"""SELECT count(*) {UPCOMING_HEADLINERS}
               AND sa.clean_name ~* '["“”]|\sw/\s|\s/\s|\mpresents\M|\svs\.?\s|\mtribute\M'"""
    )
    return float(n), n == 0, {}


async def _support_unsplit(conn: asyncpg.Connection) -> Result:
    """Audit E3: a support act that is really two acts: " / " always splits, and " & "
    should have split when both halves are artists in the listening history."""
    n = await conn.fetchval(
        r"""SELECT count(*) FROM show_artist sa JOIN show s USING (show_id)
             WHERE sa.role = 'support' AND s.starts_at > now()
               AND (sa.clean_name LIKE '% / %'
                    OR (sa.clean_name LIKE '% & %'
                        AND EXISTS (SELECT 1 FROM artist_alias x
                                     WHERE x.raw_name = split_part(sa.clean_name, ' & ', 1))
                        AND EXISTS (SELECT 1 FROM artist_alias x
                                     WHERE x.raw_name = split_part(sa.clean_name, ' & ', 2))))"""
    )
    return float(n), n == 0, {}


async def _headliners_resolved(conn: asyncpg.Connection) -> Result:
    """Audit E2: upcoming headliners spelled like an artist you listen to resolve to one."""
    total, resolved = await conn.fetchrow(
        f"""SELECT count(*), count(*) FILTER (WHERE sa.artist_id IS NOT NULL)
              {UPCOMING_HEADLINERS}
              AND EXISTS (SELECT 1 FROM artist_alias x JOIN artist_stat st USING (artist_id)
                           WHERE x.raw_name = sa.clean_name)"""
    )
    share = resolved / total if total else 1.0
    return round(share * 100, 2), share >= 0.95, {"known": total, "resolved": resolved}


async def _lists_match_files(conn: asyncpg.Connection) -> Result:
    """L1 (QUEUE_SPEC.md section 14): every list holds as many entries as its file had
    distinct rows at the last load. Passes trivially before any list is loaded."""
    rows = await conn.fetch(
        """SELECT l.slug, l.file_rows, count(e.entry_id) AS entries
             FROM list l LEFT JOIN list_entry e USING (list_id)
            WHERE l.file_rows IS NOT NULL
            GROUP BY l.slug, l.file_rows"""
    )
    off = {
        r["slug"]: [r["entries"], r["file_rows"]] for r in rows if r["entries"] != r["file_rows"]
    }
    return float(len(off)), not off, {"lists": len(rows), "mismatched": off}


RESOLVED_SHARE = """
    SELECT l.slug, count(*) AS total,
           count(*) FILTER (WHERE e.resolve_status = 'resolved'
                              AND e.release_group_id IS NOT NULL) AS resolved
      FROM list l JOIN list_entry e USING (list_id)
     WHERE e.review_status = 'accepted' AND {where}
     GROUP BY l.slug
"""


async def _atlas_resolved(conn: asyncpg.Connection) -> Result:
    """L2: atlas entries resolved to a release group. Passes before the atlas is loaded."""
    row = await conn.fetchrow(RESOLVED_SHARE.format(where="l.slug = 'v_atlas'"))
    if row is None:
        return None, True, {"reason": "v_atlas not loaded"}
    share = row["resolved"] / row["total"]
    return (
        round(share * 100, 2),
        share >= 0.95,
        {"total": row["total"], "resolved": row["resolved"]},
    )


CANON_RESOLVED_BAR = 90
# Lists with a lower bar (QUEUE_SPEC v6): the old pipeline's AI-written canon names some
# songs as albums and some albums that do not exist, so a tenth of it can never resolve.
CANON_RESOLVED_BARS = {"claude_canon": 80}


async def _canon_resolved(conn: asyncpg.Connection) -> Result:
    """L3: each canon list resolved to its bar; observed is the lowest list's share."""
    rows = await conn.fetch(RESOLVED_SHARE.format(where="l.goal = 'canon'"))
    if not rows:
        return None, True, {"reason": "no canon list loaded"}
    shares = {r["slug"]: round(100 * r["resolved"] / r["total"], 2) for r in rows}
    below = {s: v for s, v in shares.items() if v < CANON_RESOLVED_BARS.get(s, CANON_RESOLVED_BAR)}
    return min(shares.values()), not below, {"shares": shares, "below_bar": below}


async def _heard_matches_sessions(conn: asyncpg.Connection) -> Result:
    """L4: the view's heard count equals a recount straight from album_session."""
    view, recount = await conn.fetchrow(
        """SELECT (SELECT count(*) FROM list_entry_status WHERE status = 'heard'),
                  (SELECT count(*) FROM list_entry e
                    WHERE e.review_status = 'accepted' AND e.resolve_status = 'resolved'
                      AND EXISTS (SELECT 1 FROM album_session s
                                   WHERE s.release_group_id = e.release_group_id
                                     AND s.session_type = 'full'))"""
    )
    return float(abs(view - recount)), view == recount, {"view": view, "recount": recount}


async def _atlas_heard_share(conn: asyncpg.Connection) -> Result:
    """L5: the share of the atlas heard, a sanity band around the 9% name-match estimate."""
    total, heard = await conn.fetchrow(
        """SELECT count(*), count(*) FILTER (WHERE st.status = 'heard')
             FROM list_entry e JOIN list l USING (list_id)
             LEFT JOIN list_entry_status st USING (entry_id)
            WHERE l.slug = 'v_atlas' AND e.review_status = 'accepted'"""
    )
    if not total:
        return None, True, {"reason": "v_atlas not loaded"}
    share = 100 * heard / total
    return round(share, 2), 5 <= share <= 40, {"total": total, "heard": heard}


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
    Check("show_headliner_residue", "= 0", _headliner_residue),
    Check("show_support_unsplit", "= 0", _support_unsplit),
    Check("upcoming_headliners_resolved_pct", ">= 95", _headliners_resolved),
    Check("L1_list_entries_match_files", "= 0 mismatched", _lists_match_files),
    Check("L2_atlas_resolved_pct", ">= 95", _atlas_resolved),
    Check("L3_canon_lists_resolved_min_pct", ">= 90 (claude_canon >= 80)", _canon_resolved),
    Check("L4_heard_matches_sessions", "exact", _heard_matches_sessions),
    Check("L5_atlas_heard_pct", "5 to 40", _atlas_heard_share),
    Check("G1_claim_types_fit", "= 0", graph.g1_types),
    Check("G2_accepted_evidence_on_page", "= 0", graph.g2_evidence),
    Check("G3_lineage_object_not_newer", "= 0", graph.g3_direction),
    Check("G4_accepted_claude_claims_read", "= 0", graph.g4_reader),
    Check("G5_verified_batches_settled", "= 0", graph.g5_batches_settled),
    Check("G6_no_firecrawl_keys_stored", "= 0", graph.g6_no_keys),
    Check("G7_database_mb", "< 800", graph.g7_database_size),
    Check("G8_month_firecrawl_credits", "<= GRAPH_MONTHLY_CREDITS", graph.g8_credits),
    Check(
        "C3_connections_fresh",
        "nightly within 36 h; threads after derive",
        graph.c3_connections_fresh,
    ),
    Check("C5_connections_artists_disjoint", "= 0", graph.c5_connections_disjoint),
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
