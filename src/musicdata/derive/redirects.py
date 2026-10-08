"""Stray tracks: listens ListenBrainz filed under the wrong album, moved to the right one.

A listen is a stray when the listens just before and after it (each within 30 minutes)
are on album D, it is on another group S, and

- D can hold sessions (an album or EP with a tracklist, not a compilation or box set)
  and D's standard tracklist has the listen's recording (by MBID or title), and
- S cannot place it: S's tracklist lacks it, or S cannot hold sessions (a single, a
  group without a tracklist).

Each (recording, S) → D is learned once into release_group_redirect, then every listen
of that recording on S moves to D, including ones that arrive later. Both groups are
marked changed, so derive rebuilds their sessions.
"""

from __future__ import annotations

import asyncpg

ELIGIBLE = """
    (SELECT 1 FROM release_group g
       JOIN release_group_tracklist t USING (release_group_id)
      WHERE g.release_group_id = {rg} AND t.source <> 'unresolved'
        AND g.primary_type IN ('Album', 'EP') AND NOT g.is_compilation AND NOT g.is_box_set)
"""

ON_TRACKLIST = """
    (SELECT 1 FROM release_group_track t
      WHERE t.release_group_id = {rg}
        AND (t.recording_mbid = r.recording_mbid OR t.norm_title = r.norm_title))
"""

LEARN = f"""
    WITH r AS (
        SELECT listened_at, release_group_id, recording_mbid, norm_title,
               lag(release_group_id) OVER w AS prev_rg,
               lead(release_group_id) OVER w AS next_rg,
               listened_at - lag(listened_at) OVER w AS gap_before,
               lead(listened_at) OVER w - listened_at AS gap_after
          FROM listen
        WINDOW w AS (ORDER BY listened_at)
    ), strays AS (
        SELECT r.recording_mbid, r.release_group_id AS from_rg, r.prev_rg AS to_rg,
               count(*) AS evidence
          FROM r
         WHERE r.recording_mbid IS NOT NULL
           AND r.prev_rg = r.next_rg AND r.prev_rg <> r.release_group_id
           AND r.gap_before < interval '30 minutes' AND r.gap_after < interval '30 minutes'
           AND EXISTS {ELIGIBLE.format(rg="r.prev_rg")}
           AND EXISTS {ON_TRACKLIST.format(rg="r.prev_rg")}
           AND (NOT EXISTS {ON_TRACKLIST.format(rg="r.release_group_id")}
                OR NOT EXISTS {ELIGIBLE.format(rg="r.release_group_id")})
         GROUP BY 1, 2, 3
    )
    INSERT INTO release_group_redirect
           (recording_mbid, from_release_group_id, to_release_group_id, evidence)
    SELECT DISTINCT ON (recording_mbid, from_rg) recording_mbid, from_rg, to_rg, evidence
      FROM strays
     ORDER BY recording_mbid, from_rg, evidence DESC
    ON CONFLICT (recording_mbid, from_release_group_id) DO NOTHING
"""

APPLY = """
    WITH moved AS (
        UPDATE listen l SET release_group_id = d.to_release_group_id
          FROM release_group_redirect d
         WHERE l.recording_mbid = d.recording_mbid
           AND l.release_group_id = d.from_release_group_id
        RETURNING d.from_release_group_id AS a, d.to_release_group_id AS b
    ), touched AS (
        SELECT a AS rg FROM moved UNION SELECT b FROM moved
    ), bumped AS (
        UPDATE release_group SET updated_at = now()
         WHERE release_group_id IN (SELECT rg FROM touched)
        RETURNING 1
    )
    SELECT (SELECT count(*) FROM moved) AS listens, (SELECT count(*) FROM touched) AS groups
"""


async def apply_redirects(conn: asyncpg.Connection) -> dict[str, int]:
    """Learn new strays, then move every listen that a redirect covers. One transaction."""
    async with conn.transaction():
        learned = int((await conn.execute(LEARN)).split()[-1])
        row = await conn.fetchrow(APPLY)
    return {"redirects_learned": learned, "listens_moved": row["listens"]}
