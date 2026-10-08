"""The unmapped tail: release groups ListenBrainz could not map, settled by tracklist overlap.

For each unmapped group with enough listens (most-listened first):

1. Mapped groups of the same artist and key already in the database are the candidates
   (no request needed). This is the case ingest leaves apart on purpose.
2. Otherwise MusicBrainz search supplies up to three candidates whose title has the same key.
3. The candidate whose tracklist shares the most titles with what was actually played wins.
   A tie, or no overlap at all, is recorded as unresolved with the reason, for review.

A winner already in the database absorbs the unmapped group (listens and aliases move,
the unmapped row goes). A winner not yet in the database gives the unmapped row its MBID.
"""

from __future__ import annotations

from collections.abc import Hashable

import asyncpg

from musicdata.clients.musicbrainz import MusicBrainzClient, NotFoundError
from musicdata.db import connection
from musicdata.identity import album_key
from musicdata.resolve.canonical import Resolution, resolve_group
from musicdata.resolve.job import RETRY_AFTER, write_resolution, write_unresolved

MIN_SCORE = 90
MAX_CANDIDATES = 3


def pick_by_overlap[K: Hashable](
    listened: set[str], candidates: dict[K, set[str]]
) -> tuple[K | None, str]:
    """The candidate sharing the most track titles with `listened`, or None and why."""
    scores = {k: len(listened & titles) for k, titles in candidates.items()}
    best = max(scores.values(), default=0)
    if best == 0:
        return None, "no MusicBrainz candidate shares a track title with the listens"
    winners = [k for k, s in scores.items() if s == best]
    if len(winners) > 1:
        return None, f"ambiguous: {len(winners)} candidates each share {best} titles"
    return winners[0], ""


async def merge_release_group(conn: asyncpg.Connection, src: int, dst: int) -> None:
    """Move every listen and alias of `src` onto `dst`, then drop `src`."""
    await conn.execute(
        "UPDATE listen SET release_group_id = $2 WHERE release_group_id = $1", src, dst
    )
    await conn.execute(
        """INSERT INTO release_group_alias (artist_id, raw_album, release_group_id, source)
           SELECT artist_id, raw_album, $2, source FROM release_group_alias
            WHERE release_group_id = $1
           ON CONFLICT DO NOTHING""",
        src,
        dst,
    )
    await conn.execute("DELETE FROM release_group WHERE release_group_id = $1", src)
    await conn.execute(  # derive rebuilds the sessions of a group that changed
        "UPDATE release_group SET updated_at = now() WHERE release_group_id = $1", dst
    )


PENDING_UNMAPPED = f"""
    FROM release_group rg
    JOIN artist a USING (artist_id)
    JOIN listen l USING (release_group_id)
    LEFT JOIN release_group_tracklist t USING (release_group_id)
   WHERE rg.mbid IS NULL
     AND (t.release_group_id IS NULL
          OR (t.source = 'unresolved' AND t.resolved_at < now() - interval '{RETRY_AFTER}'))
     AND ($3::int[] IS NULL OR rg.release_group_id = ANY($3::int[]))
   GROUP BY rg.release_group_id, a.name, a.mbid
  HAVING count(*) >= $1
"""


async def _titles(conn: asyncpg.Connection, sql: str, *args: object) -> set[str]:
    return {r[0] for r in await conn.fetch(sql, *args)}


async def resolve_unmapped(
    pool: asyncpg.Pool,
    mb: MusicBrainzClient,
    *,
    limit: int,
    min_listens: int,
    release_group_ids: list[int] | None = None,
) -> dict[str, int]:
    counts = {"merged": 0, "mapped": 0, "unresolved": 0, "waiting": 0}
    async with connection(pool) as conn:
        todo = await conn.fetch(
            f"""SELECT rg.release_group_id, rg.title, rg.norm_key, rg.artist_id,
                       a.name AS artist_name, a.mbid::text AS artist_mbid
                  {PENDING_UNMAPPED}
                 ORDER BY count(*) DESC, rg.release_group_id
                 LIMIT $2""",
            min_listens,
            limit,
            release_group_ids,
        )
    for row in todo:
        rg_id = row["release_group_id"]
        async with connection(pool) as conn:
            listened = await _titles(
                conn, "SELECT DISTINCT norm_title FROM listen WHERE release_group_id = $1", rg_id
            )
            local = await conn.fetch(
                """SELECT rg.release_group_id, t.source
                     FROM release_group rg
                     LEFT JOIN release_group_tracklist t USING (release_group_id)
                    WHERE rg.mbid IS NOT NULL AND rg.artist_id = $1 AND rg.norm_key = $2""",
                row["artist_id"],
                row["norm_key"],
            )
            if any(r["source"] is None for r in local):
                counts["waiting"] += 1  # a namesake still awaits its own tracklist
                continue
            local_tracks = {
                r["release_group_id"]: await _titles(
                    conn,
                    "SELECT norm_title FROM release_group_track WHERE release_group_id = $1",
                    r["release_group_id"],
                )
                for r in local
                if r["source"] == "musicbrainz"
            }

        if local_tracks:
            winner, why = pick_by_overlap(listened, local_tracks)
            async with connection(pool) as conn:
                if winner is None:
                    await write_unresolved(conn, rg_id, why)
                    counts["unresolved"] += 1
                else:
                    async with conn.transaction():
                        await merge_release_group(conn, rg_id, winner)
                    counts["merged"] += 1
            continue

        found: dict[str, Resolution] = {}
        try:
            hits = await mb.search_release_groups(
                row["title"], artist_name=row["artist_name"], artist_mbid=row["artist_mbid"]
            )
            for hit in hits:
                if len(found) == MAX_CANDIDATES:
                    break
                if int(hit.get("score", 0)) < MIN_SCORE:
                    continue
                if album_key(hit.get("title", "")) != row["norm_key"]:
                    continue
                res = resolve_group(await mb.releases_of_group(hit["id"]))
                if res is not None:
                    found[hit["id"]] = res
        except NotFoundError:
            pass
        winner, why = pick_by_overlap(
            listened, {mbid: {t.norm_title for t in r.tracks} for mbid, r in found.items()}
        )
        async with connection(pool) as conn:
            if winner is None:
                await write_unresolved(conn, rg_id, why)
                counts["unresolved"] += 1
                continue
            async with conn.transaction():
                existing = await conn.fetchval(
                    "SELECT release_group_id FROM release_group WHERE mbid = $1", winner
                )
                if existing is not None:
                    await merge_release_group(conn, rg_id, existing)
                    await write_resolution(conn, existing, found[winner])
                    counts["merged"] += 1
                else:
                    await conn.execute(
                        "UPDATE release_group SET mbid = $2 WHERE release_group_id = $1",
                        rg_id,
                        winner,
                    )
                    await write_resolution(conn, rg_id, found[winner])
                    counts["mapped"] += 1
    return counts
