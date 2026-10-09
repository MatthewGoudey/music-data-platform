"""`musicdata lists copy-resolutions --source <env>`: reuse another environment's matches.

Matching a list entry to a MusicBrainz release group gives the same answer in every
environment, and the first pass in dev took about ten hours of searches. This copies,
for every pending entry of this database that the source has settled:

- a match to a mapped group: the group is found here by MBID or created through the
  identity rules (`ensure_release_group`), and the entry is resolved (`via: copied`);
- unresolved and ambiguous outcomes, with their detail, so hopeless searches are not
  repeated (`lists resolve --retry` can still revisit them).

Matches to unmapped groups stay pending: this database's own listens decide those, and
`lists resolve` settles them from the known albums at no request.
"""

from __future__ import annotations

import json
from collections import defaultdict

import asyncpg

from musicdata.db import connection
from musicdata.jobs.runs import JobFn, RunContext
from musicdata.lists.match import Candidate
from musicdata.lists.resolve import ensure_release_group, settle

SOURCE = """
SELECT l.slug, e.artist_key, e.album_key, e.resolve_status, e.resolve_detail,
       rg.mbid::text AS mbid, rg.title, rg.first_release_year, rg.primary_type,
       rg.secondary_types, a.mbid::text AS artist_mbid, a.name AS artist_name
  FROM list_entry e
  JOIN list l USING (list_id)
  LEFT JOIN release_group rg ON rg.release_group_id = e.release_group_id
  LEFT JOIN artist a ON a.artist_id = rg.artist_id
 WHERE e.resolve_status IN ('resolved', 'unresolved', 'ambiguous')
"""


def lists_copy(source_url: str) -> JobFn:
    async def _run(ctx: RunContext) -> None:
        src = await asyncpg.connect(source_url)
        try:
            rows = await src.fetch(SOURCE)
        finally:
            await src.close()
        settled = {(r["slug"], r["artist_key"], r["album_key"]): r for r in rows}
        async with connection(ctx.pool) as conn:
            pending = await conn.fetch(
                """SELECT e.entry_id, l.slug, e.artist_key, e.album_key
                     FROM list_entry e JOIN list l USING (list_id)
                    WHERE e.resolve_status = 'pending'"""
            )
        counts: dict[str, int] = defaultdict(int)
        for p in pending:
            r = settled.get((p["slug"], p["artist_key"], p["album_key"]))
            if r is None:
                counts["not_in_source"] += 1
                continue
            async with connection(ctx.pool) as conn, conn.transaction():
                if r["resolve_status"] != "resolved":
                    detail = json.loads(r["resolve_detail"] or "{}") | {"copied": True}
                    await settle(conn, [p["entry_id"]], r["resolve_status"], None, detail)
                    counts[r["resolve_status"]] += 1
                elif r["mbid"] and r["artist_mbid"]:
                    rg_id, _ = await ensure_release_group(
                        conn,
                        Candidate(
                            mbid=r["mbid"],
                            title=r["title"],
                            year=r["first_release_year"],
                            primary_type=r["primary_type"],
                            secondary_types=tuple(r["secondary_types"] or ()),
                            artist_mbid=r["artist_mbid"],
                            artist_name=r["artist_name"],
                        ),
                    )
                    await settle(
                        conn,
                        [p["entry_id"]],
                        "resolved",
                        rg_id,
                        {"via": "copied", "mbid": r["mbid"]},
                    )
                    counts["resolved"] += 1
                else:
                    counts["left_for_known"] += 1
        ctx.rows = counts["resolved"]
        ctx.notes.update(pending=len(pending), **counts)

    return _run
