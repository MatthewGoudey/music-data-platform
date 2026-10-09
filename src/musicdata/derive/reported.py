"""Listens go home to the album the player reported (ADR 0015 amendment, 2026-10-09).

ListenBrainz maps each track on its own, so a live album played front to back can land
on a dozen compilations that share its recordings (At Folsom Prison: thirteen groups),
and no group ever holds enough of it for a session. The player's reported release name
is the better witness. A listen whose `reported_key` differs from its group's key moves
to the group that is that album's home, when all of these hold:

- the home is a MusicBrainz release group (mapped) with the reported key, by the
  listen's artist, its current group's artist, or the album artist recorded for this
  very listen (release_group_alias); exactly one such group (Weezer's two self-titled
  albums stay where they are);
- the reported album is not an expanded form of the current one ("Play & Play: The B
  Sides" on Play, "Transistor … Extended" on Transistor stay on the album);
- the move does not take a listen off a session-eligible album or EP onto a
  compilation or box set (Cap'n Jazz EPs played from the anthology keep their EPs).

Both groups are marked changed, so derive rebuilds their sessions. Listens at home are
never moved again by the stray-track redirect (derive/redirects.py).
"""

from __future__ import annotations

import asyncpg

from musicdata.identity import album_key

FILL_BATCH = 5000

ELIGIBLE = """(g.primary_type IN ('Album', 'EP') AND NOT g.is_compilation AND NOT g.is_box_set
                AND EXISTS (SELECT 1 FROM release_group_tracklist t
                             WHERE t.release_group_id = g.release_group_id
                               AND t.source <> 'unresolved'))"""

GO_HOME = f"""
    WITH away AS (
        SELECT l.listen_id, l.release_group_id AS from_rg, l.artist_id, l.release_name,
               l.reported_key, cur.artist_id AS cur_artist, cur.norm_key AS cur_key,
               {ELIGIBLE.replace("g.", "cur.")} AS cur_eligible
          FROM listen l
          JOIN release_group cur ON cur.release_group_id = l.release_group_id
         WHERE l.reported_key IS NOT NULL AND cur.norm_key <> l.reported_key
           AND l.reported_key NOT LIKE cur.norm_key || ' %'
    ), cand AS (
        SELECT a.listen_id, a.from_rg, a.cur_eligible, h.release_group_id AS to_rg
          FROM away a
          JOIN release_group h ON h.norm_key = a.reported_key AND h.mbid IS NOT NULL
                              AND h.artist_id IN (a.artist_id, a.cur_artist)
        UNION
        SELECT a.listen_id, a.from_rg, a.cur_eligible, h.release_group_id
          FROM away a
          JOIN release_group_alias ra ON ra.raw_album = a.release_name
                                     AND ra.release_group_id = a.from_rg
          JOIN release_group h ON h.artist_id = ra.artist_id AND h.norm_key = a.reported_key
                              AND h.mbid IS NOT NULL
    ), pick AS (
        SELECT c.listen_id, c.from_rg, min(c.to_rg) AS to_rg
          FROM cand c JOIN release_group g ON g.release_group_id = c.to_rg
         WHERE NOT c.cur_eligible OR NOT (g.is_compilation OR g.is_box_set)
         GROUP BY c.listen_id, c.from_rg
        HAVING count(DISTINCT c.to_rg) = 1
    ), moved AS (
        UPDATE listen l SET release_group_id = p.to_rg
          FROM pick p
         WHERE l.listen_id = p.listen_id
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
