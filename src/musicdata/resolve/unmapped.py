"""The unmapped tail: release groups ListenBrainz could not map, settled by tracklist overlap.

For each unmapped group with enough listens (most-listened first):

1. Mapped groups of the same artist and key already in the database are the candidates
   (no request needed). This is the case ingest leaves apart on purpose.
2. Otherwise MusicBrainz search supplies up to three candidates whose title has the same key.
   A title that finds nothing is searched again cut before its edition words
   ("DAMN. COLLECTORS EDITION." → "DAMN. COLLECTORS" → "DAMN."), since an edition is
   a release inside the group, not a group of its own.
3. The candidate whose tracklist shares the most titles with what was actually played wins.
   Titles match exactly or nearly ("luv sic pt3" ~ "luv sic part 3") with equal numbers.
   A tie, or no overlap at all, is recorded as unresolved with the reason, for review.

A winner already in the database absorbs the unmapped group (listens and aliases move,
the unmapped row goes). A winner not yet in the database gives the unmapped row its MBID.
"""

from __future__ import annotations

import re
from collections.abc import Hashable
from difflib import SequenceMatcher

import asyncpg

from musicdata.clients.musicbrainz import MusicBrainzClient, NotFoundError
from musicdata.db import connection
from musicdata.identity import album_key, has_edition_marker, strip_edition_markers
from musicdata.resolve.canonical import Resolution, resolve_group
from musicdata.resolve.job import RETRY_AFTER, write_resolution, write_unresolved

MIN_SCORE = 90
MAX_CANDIDATES = 3
NEAR = 0.85
_DIGITS = re.compile(r"\d+")


def titles_match(a: str, b: str) -> bool:
    """Equal, or nearly equal with the same numbers ("pt3" ~ "part 3", never "1" ~ "2")."""
    if a == b:
        return True
    if _DIGITS.findall(a) != _DIGITS.findall(b) or min(len(a), len(b)) < 4:
        return False
    return SequenceMatcher(None, a, b).ratio() >= NEAR


def _shared(listened: set[str], titles: set[str]) -> int:
    return sum(1 for t in listened if t in titles or any(titles_match(t, x) for x in titles))


def search_titles(title: str) -> list[str]:
    """The title, then shorter forms without its edition words: at most three searches."""
    out = [title]
    stripped = strip_edition_markers(title)
    if stripped and stripped != title:
        out.append(stripped)
    words = stripped.split()
    first = next((i for i, w in enumerate(words) if has_edition_marker(w)), None)
    if first:
        out.append(" ".join(words[:first]))
        if first > 1:
            out.append(" ".join(words[: first - 1]))
    return out[:3]


def pick_by_overlap[K: Hashable](
    listened: set[str], candidates: dict[K, set[str]]
) -> tuple[K | None, str]:
    """The candidate sharing the most track titles with `listened`, or None and why."""
    if not candidates:
        return None, "no MusicBrainz search hit with this title and artist"
    scores = {k: _shared(listened, titles) for k, titles in candidates.items()}
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
          OR (t.source = 'unresolved'
              AND ($4::bool OR t.resolved_at < now() - interval '{RETRY_AFTER}')))
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
    retry_unresolved: bool = False,
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
            retry_unresolved,
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
            for title in search_titles(row["title"]):
                hits = await mb.search_release_groups(
                    title, artist_name=row["artist_name"], artist_mbid=row["artist_mbid"]
                )
                for hit in hits:
                    if len(found) == MAX_CANDIDATES:
                        break
                    if int(hit.get("score", 0)) < MIN_SCORE:
                        continue
                    if album_key(hit.get("title", "")) != album_key(title):
                        continue
                    res = resolve_group(await mb.releases_of_group(hit["id"]))
                    if res is not None:
                        found[hit["id"]] = res
                if found:
                    break
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
