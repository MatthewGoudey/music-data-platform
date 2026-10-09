"""Listens go home to the album the player reported (ADR 0015 amendment, 2026-10-09).

ListenBrainz maps each track on its own, so a live album played front to back can land
on a dozen compilations that share its recordings (At Folsom Prison: thirteen groups),
and no group ever holds enough of it for a session. The player's reported release name
is the better witness. A listen whose `reported_key` differs from its group's key moves
to the group that is that album's home:

- a group with the reported key, by the listen's artist, its current group's artist, or
  an album artist the reported name was seen under (release_group_alias);
- exactly one such group, or exactly one mapped one among several (Weezer's two
  self-titled albums stay where they are).

Both groups are marked changed, so derive rebuilds their sessions. Listens at home are
never moved again by the stray-track redirect (derive/redirects.py).
"""

from __future__ import annotations

import asyncpg

from musicdata.identity import album_key

FILL_BATCH = 5000

GO_HOME = """
    WITH cand AS (
        SELECT l.listen_id, l.release_group_id AS from_rg, h.release_group_id AS to_rg,
               h.mbid IS NOT NULL AS mapped
          FROM listen l
          JOIN release_group cur ON cur.release_group_id = l.release_group_id
          JOIN release_group h ON h.norm_key = l.reported_key
                              AND h.artist_id IN (l.artist_id, cur.artist_id)
         WHERE l.reported_key IS NOT NULL AND cur.norm_key <> l.reported_key
        UNION
        SELECT l.listen_id, l.release_group_id, h.release_group_id, h.mbid IS NOT NULL
          FROM listen l
          JOIN release_group cur ON cur.release_group_id = l.release_group_id
          JOIN release_group_alias ra ON ra.raw_album = l.release_name
          JOIN release_group h ON h.artist_id = ra.artist_id AND h.norm_key = l.reported_key
         WHERE l.reported_key IS NOT NULL AND cur.norm_key <> l.reported_key
    ), pick AS (
        SELECT listen_id, from_rg,
               CASE WHEN count(DISTINCT to_rg) = 1 THEN min(to_rg)
                    WHEN count(DISTINCT to_rg) FILTER (WHERE mapped) = 1
                         THEN min(to_rg) FILTER (WHERE mapped)
               END AS to_rg
          FROM cand GROUP BY listen_id, from_rg
    ), moved AS (
        UPDATE listen l SET release_group_id = p.to_rg
          FROM pick p
         WHERE l.listen_id = p.listen_id AND p.to_rg IS NOT NULL
        RETURNING p.from_rg AS a, p.to_rg AS b
    ), touched AS (
        SELECT a AS rg FROM moved UNION SELECT b FROM moved
    ), bumped AS (
        UPDATE release_group SET updated_at = now()
         WHERE release_group_id IN (SELECT rg FROM touched)
        RETURNING 1
    )
    SELECT (SELECT count(*) FROM moved) AS listens, (SELECT count(*) FROM touched) AS groups
"""


async def fill_reported_keys(conn: asyncpg.Connection) -> int:
    """Give listens stored before 0015 their reported_key, a batch of names at a time."""
    filled = 0
    while True:
        names = [
            r["release_name"]
            for r in await conn.fetch(
                """SELECT DISTINCT release_name FROM listen
                    WHERE reported_key IS NULL AND release_name IS NOT NULL
                    LIMIT $1""",
                FILL_BATCH,
            )
        ]
        if not names:
            return filled
        keys = [album_key(n) or n.casefold() for n in names]
        status = await conn.execute(
            """UPDATE listen l SET reported_key = u.k
                 FROM unnest($1::text[], $2::text[]) AS u(name, k)
                WHERE l.release_name = u.name AND l.reported_key IS NULL""",
            names,
            keys,
        )
        filled += int(status.split()[-1])


async def go_home(conn: asyncpg.Connection) -> dict[str, int]:
    """Fill missing keys, then move listens to their reported album. One transaction."""
    async with conn.transaction():
        filled = await fill_reported_keys(conn)
        row = await conn.fetchrow(GO_HOME)
    return {"reported_keys_filled": filled, "listens_sent_home": row["listens"]}
