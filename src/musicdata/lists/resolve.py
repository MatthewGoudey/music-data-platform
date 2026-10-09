"""`musicdata lists resolve`: list entries → release groups (docs/QUEUE_SPEC.md section 5).

Pending entries go in order of importance (the atlas, then heavier lists, Essentials,
ranked positions). Entries on several lists with the same keys and year are one album
and settle together. For each:

1. **Already known.** The artist by alias or key, then that artist's release group with
   the album key, whose first-release year (when both are known) is within ±1. One such
   group, preferring mapped ones, resolves it at no request.
2. **MusicBrainz search** by title and artist, then by the title cut before its edition
   words; the rules in `lists/match.py` choose. A winner not yet in the database is
   created with its artist through the identity rules resolve uses; the normal resolve
   job fetches its tracklist once the album has listens.

An entry whose release group disappears (an unmapped group merged or dropped) returns
to pending and resolves again.
"""

from __future__ import annotations

import asyncio
import json
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime

import asyncpg

from musicdata.clients.musicbrainz import MusicBrainzClient
from musicdata.config import get_settings
from musicdata.db import connection
from musicdata.identity import norm_key
from musicdata.jobs.runs import JobFn, RunContext
from musicdata.lists.match import (
    Candidate,
    artist_variants,
    candidate,
    choose,
    group_key,
    lead_artist,
    near_year,
    plain_title,
    title_variants,
)
from musicdata.lists.seed import base_album_key
from musicdata.log import get_logger
from musicdata.resolve.job import _ensure_artist
from musicdata.resolve.unmapped import search_titles

log = get_logger(__name__)

SEARCH_LIMIT = 15
EMPTY_RETRY_SECONDS = 3

PENDING = """
    FROM list_entry e JOIN list l USING (list_id)
   WHERE e.review_status = 'accepted'
     AND (e.resolve_status = 'pending'
          OR (e.resolve_status = 'resolved' AND e.release_group_id IS NULL)
          OR ($1::bool AND e.resolve_status = 'unresolved'))
"""


@dataclass
class Album:
    """Entries that name the same album: one lookup settles them all."""

    raw_artist: str
    raw_album: str
    artist_key: str
    key: str  # unmarked album key
    year: int | None
    entry_ids: list[int] = field(default_factory=list)


async def pending_albums(conn: asyncpg.Connection, retry: bool) -> list[Album]:
    rows = await conn.fetch(
        f"""SELECT e.entry_id, e.raw_artist, e.raw_album, e.artist_key, e.album_key, e.year
              {PENDING}
             ORDER BY CASE WHEN $1 THEN e.resolve_detail->>'tried_at' END NULLS FIRST,
                      (l.slug = 'v_atlas') DESC, l.weight DESC,
                      CASE e.priority WHEN 'Essential' THEN 0 WHEN 'Recommended' THEN 1
                                      WHEN 'Deep cut' THEN 2 ELSE 1 END,
                      e.position NULLS LAST, e.entry_id""",
        retry,
    )
    albums: dict[tuple[str, str, int | None], Album] = {}
    for r in rows:
        key = base_album_key(r["album_key"])
        a = albums.setdefault(
            (r["artist_key"], key, r["year"]),
            Album(r["raw_artist"], r["raw_album"], r["artist_key"], key, r["year"]),
        )
        a.entry_ids.append(r["entry_id"])
    return list(albums.values())


class Known:
    """The identity layer in memory: artists by alias and key, release groups by key."""

    def __init__(self) -> None:
        self.by_alias: dict[str, set[int]] = defaultdict(set)
        self.by_key: dict[str, set[int]] = defaultdict(set)
        self.groups: dict[tuple[int, str], list[tuple[int, bool, int | None]]] = defaultdict(list)

    @classmethod
    async def load(cls, conn: asyncpg.Connection) -> Known:
        k = cls()
        for r in await conn.fetch("SELECT raw_name, artist_id FROM artist_alias"):
            k.by_alias[r["raw_name"]].add(r["artist_id"])
        for r in await conn.fetch("SELECT artist_id, norm_key FROM artist"):
            k.by_key[r["norm_key"]].add(r["artist_id"])
        for r in await conn.fetch(
            """SELECT release_group_id, artist_id, norm_key, mbid IS NOT NULL AS mapped,
                      first_release_year
                 FROM release_group"""
        ):
            k.groups[(r["artist_id"], r["norm_key"])].append(
                (r["release_group_id"], r["mapped"], r["first_release_year"])
            )
        return k

    def find(self, album: Album) -> int | None:
        artists: set[int] = set()
        for name in artist_variants(album.raw_artist):
            artists |= self.by_alias.get(name, set()) | self.by_key.get(norm_key(name), set())
        artists |= self.by_key.get(album.artist_key, set())
        keys = {album.key} | {group_key(t) for t in title_variants(album.raw_album, subtitle=False)}
        found = list(
            {
                g
                for a in artists
                for k in keys
                for g in self.groups.get((a, k), [])
                if near_year(g[2], album.year)
            }
        )
        mapped = [g for g in found if g[1]]
        found = mapped or found
        return found[0][0] if len(found) == 1 else None

    def add(self, rg_id: int, artist_id: int, key: str, year: int | None) -> None:
        self.groups[(artist_id, key)].append((rg_id, True, year))


async def search(mb: MusicBrainzClient, album: Album) -> tuple[str, list[Candidate], list[str]]:
    """The first title form that finds candidates decides; returns the forms searched."""
    searched: list[str] = []
    status, chosen = "unresolved", []
    names = artist_variants(album.raw_artist)
    # MusicBrainz may join a credit differently: the lead artist comes last
    artists = names + [lead for n in names if (lead := lead_artist(n)) and lead not in names]
    artist_keys = {album.artist_key} | {norm_key(a) for a in artists}
    titles = title_variants(album.raw_album)
    keys = {album.key} | {group_key(t) for t in titles}
    forms = list(dict.fromkeys(f for t in titles for f in search_titles(t)))
    forms += [p for t in titles if (p := plain_title(t)) and p not in forms]
    for artist in artists:
        for title in forms:
            searched.append(f"{artist} / {title}")
            hits = await mb.search_release_groups(title, artist_name=artist, limit=SEARCH_LIMIT)
            if not hits and len(searched) == 1:
                # an overloaded MusicBrainz answers some searches with nothing: ask once more
                await asyncio.sleep(EMPTY_RETRY_SECONDS)
                hits = await mb.search_release_groups(title, artist_name=artist, limit=SEARCH_LIMIT)
            cands = [
                c for h in hits for ak in artist_keys for k in keys if (c := candidate(h, ak, k))
            ]
            status, chosen = choose(cands, album.year)
            if status != "unresolved":
                return status, chosen, searched
    # Last resorts, still held to the artist and title checks: the title alone (an artist
    # filter sometimes hides the album), then its words instead of the exact phrase.
    for label, kwargs in (
        ("title only", {}),
        ("words", {"artist_name": artists[-1], "loose": True}),
    ):
        searched.append(f"{label} / {titles[0]}")
        hits = await mb.search_release_groups(titles[0], limit=SEARCH_LIMIT * 2, **kwargs)
        cands = [c for h in hits for ak in artist_keys for k in keys if (c := candidate(h, ak, k))]
        status, chosen = choose(cands, album.year)
        if status != "unresolved":
            return status, chosen, searched
    return status, chosen, searched


async def ensure_release_group(conn: asyncpg.Connection, c: Candidate) -> tuple[int, int]:
    """(release_group_id, artist_id) for a MusicBrainz release group, created if new."""
    row = await conn.fetchrow(
        "SELECT release_group_id, artist_id FROM release_group WHERE mbid = $1", c.mbid
    )
    if row:
        return row["release_group_id"], row["artist_id"]
    if not c.artist_mbid or not c.artist_name:
        raise ValueError(f"release group {c.mbid} has no artist credit")
    artist_id = await _ensure_artist(conn, c.artist_mbid, c.artist_name)
    rg_id = await conn.fetchval(
        """INSERT INTO release_group (mbid, artist_id, title, norm_key, primary_type,
                                      secondary_types, first_release_year, is_compilation)
           VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
           ON CONFLICT (mbid) DO NOTHING RETURNING release_group_id""",
        c.mbid,
        artist_id,
        c.title,
        group_key(c.title),
        c.primary_type,
        list(c.secondary_types),
        c.year,
        "Compilation" in c.secondary_types,
    )
    if rg_id is None:  # created meanwhile
        rg_id = await conn.fetchval(
            "SELECT release_group_id FROM release_group WHERE mbid = $1", c.mbid
        )
    return rg_id, artist_id


async def settle(
    conn: asyncpg.Connection, ids: list[int], status: str, rg_id: int | None, detail: dict
) -> None:
    await conn.execute(
        """UPDATE list_entry
              SET resolve_status = $2, release_group_id = $3, resolve_detail = $4::jsonb
            WHERE entry_id = ANY($1::bigint[])""",
        ids,
        status,
        rg_id,
        _json(detail),
    )


def _json(d: dict) -> str:
    return json.dumps(d, ensure_ascii=False, default=str)


def lists_resolve(*, limit: int, retry: bool = False) -> JobFn:
    """Settle every pending album found in the database, then search up to `limit`."""

    async def _run(ctx: RunContext) -> None:
        settings = get_settings()
        async with connection(ctx.pool) as conn:
            albums = await pending_albums(conn, retry)
            known = await Known.load(conn)
        counts: dict[str, int] = defaultdict(int)
        to_search: list[Album] = []
        async with connection(ctx.pool) as conn:
            for a in albums:
                rg_id = known.find(a)
                if rg_id is None:
                    to_search.append(a)
                    continue
                await settle(conn, a.entry_ids, "resolved", rg_id, {"via": "known"})
                counts["known"] += len(a.entry_ids)
        async with MusicBrainzClient(user_agent=settings.musicbrainz_user_agent) as mb:
            for i, a in enumerate(to_search[:limit], 1):
                status, cands, searched = await search(mb, a)
                detail: dict[str, object] = {
                    "via": "search",
                    "searched": searched,
                    "tried_at": datetime.now(UTC).isoformat(timespec="seconds"),
                }
                async with connection(ctx.pool) as conn, conn.transaction():
                    rg_id = None
                    if status == "resolved":
                        rg_id, artist_id = await ensure_release_group(conn, cands[0])
                        known.add(rg_id, artist_id, group_key(cands[0].title), cands[0].year)
                        detail["mbid"] = cands[0].mbid
                    elif status == "ambiguous":
                        detail["candidates"] = [c.detail() for c in cands[:8]]
                    await settle(conn, a.entry_ids, status, rg_id, detail)
                counts[status] += len(a.entry_ids)
                if i % 200 == 0:
                    log.info("lists resolve progress", extra={"done": i, **counts})
            requests = mb.requests
        async with connection(ctx.pool) as conn:
            remaining = await conn.fetchval(f"SELECT count(*) {PENDING}", False)
        ctx.rows = counts["known"] + counts["resolved"]
        ctx.notes.update(
            limit=limit,
            albums_pending=len(albums),
            searched=min(limit, len(to_search)),
            entries=dict(counts),
            musicbrainz_requests=requests,
            remaining_entries=remaining,
        )

    return _run
