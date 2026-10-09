"""Listens on an album that can never get a tracklist fold into its namesake that can.

A group whose tracklist is `unresolved` can hold no session, so every listen on it is
lost to "heard". RAM had 63 listens on a MusicBrainz group with no tracks and 35 on an
unmapped "Paul McCartney, Linda McCartney" group, while the real RAM (on two lists) had
none. Each derive moves the listens (and any list entries) of such a group to the one
mapped group with the same album key by the same artist or the credit's lead artist,
whose tracklist is resolved or not yet asked for (resolve fetches it once it has
listens). With no single such group, nothing moves. Both groups are marked changed.
"""

from __future__ import annotations

from collections import defaultdict

import asyncpg

from musicdata.identity import norm_key
from musicdata.lists.match import lead_artist

STUCK = """
SELECT rg.release_group_id, rg.norm_key, a.name, a.norm_key AS akey
  FROM release_group rg
  JOIN artist a USING (artist_id)
  JOIN release_group_tracklist t USING (release_group_id)
 WHERE t.source = 'unresolved'
   AND EXISTS (SELECT 1 FROM listen l WHERE l.release_group_id = rg.release_group_id)
"""

HOMES = """
SELECT rg.release_group_id, rg.norm_key, a.norm_key AS akey
  FROM release_group rg
  JOIN artist a USING (artist_id)
  LEFT JOIN release_group_tracklist t USING (release_group_id)
 WHERE rg.mbid IS NOT NULL AND (t.source IS NULL OR t.source <> 'unresolved')
"""


def pairs(stuck: list, homes: list) -> list[tuple[int, int]]:
    """(from, to) for every stuck group with exactly one home."""
    by_key: dict[tuple[str, str], set[int]] = defaultdict(set)
    for h in homes:
        by_key[(h["akey"], h["norm_key"])].add(h["release_group_id"])
    out = []
    for s in stuck:
        artists = {s["akey"], norm_key(lead_artist(s["name"]))}
        found = {
            rg
            for a in artists
            for rg in by_key.get((a, s["norm_key"]), set())
            if rg != s["release_group_id"]
        }
        if len(found) == 1:
            out.append((s["release_group_id"], found.pop()))
    return out


async def fold_untracked(conn: asyncpg.Connection) -> dict[str, int]:
    moves = pairs(await conn.fetch(STUCK), await conn.fetch(HOMES))
    if not moves:
        return {"untracked_groups_folded": 0, "listens_folded": 0}
    src, dst = [m[0] for m in moves], [m[1] for m in moves]
    async with conn.transaction():
        status = await conn.execute(
            """UPDATE listen l SET release_group_id = u.dst
                 FROM unnest($1::int[], $2::int[]) AS u(src, dst)
                WHERE l.release_group_id = u.src""",
            src,
            dst,
        )
        await conn.execute(
            """UPDATE list_entry e SET release_group_id = u.dst
                 FROM unnest($1::int[], $2::int[]) AS u(src, dst)
                WHERE e.release_group_id = u.src""",
            src,
            dst,
        )
        await conn.execute(
            "UPDATE release_group SET updated_at = now() WHERE release_group_id = ANY($1::int[])",
            src + dst,
        )
    return {"untracked_groups_folded": len(moves), "listens_folded": int(status.split()[-1])}
