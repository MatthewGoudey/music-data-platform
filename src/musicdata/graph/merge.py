"""Merging duplicate entities, and resolving names posted as written (`graph verify --pending`,
companion spec 3.4; the merge rule is graph spec v10).

An entity without an MBID merges into one with an MBID only when both

- share a normalised name (`norm_key`) and a kind (artist and person count as one kind), and
- are linked to at least one same album: some claim with each of them has that album as its
  `album_context`.

A name that matches more than one entity with an MBID, or whose two entities share no album,
stays apart and is reported. Every merge is logged on the kept entity (`attrs.merged`) with the
merged row, every claim it repointed and every link it moved, so `graph unmerge` can undo it.
"""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import UTC, datetime

import asyncpg

from musicdata.clients.musicbrainz import MusicBrainzClient
from musicdata.graph import names
from musicdata.graph.claims import claim_key
from musicdata.graph.entities import ART

PAIRS = """
WITH alb AS (
    SELECT subject_id AS e, album_context AS a FROM assertion
     WHERE album_context IS NOT NULL AND status <> 'superseded'
    UNION
    SELECT object_id, album_context FROM assertion
     WHERE object_id IS NOT NULL AND album_context IS NOT NULL AND status <> 'superseded'
)
SELECT l.entity_id AS loose, f.entity_id AS firm, l.name AS loose_name, f.name AS firm_name,
       l.type AS loose_type, f.type AS firm_type, l.norm_key,
       EXISTS (SELECT 1 FROM alb x JOIN alb y ON x.a = y.a
                WHERE x.e = l.entity_id AND y.e = f.entity_id) AS shared_album
  FROM entity l
  JOIN entity f ON f.norm_key = l.norm_key AND f.mbid IS NOT NULL AND f.entity_id <> l.entity_id
   AND (f.type = l.type OR (f.type IN ('artist', 'person') AND l.type IN ('artist', 'person')))
 WHERE l.mbid IS NULL AND l.local_key IS NULL
"""


async def candidates(conn: asyncpg.Connection) -> tuple[list[dict], list[dict]]:
    """(merges to make, name matches left apart with the reason)."""
    by_loose: dict[int, list[asyncpg.Record]] = defaultdict(list)
    for r in await conn.fetch(PAIRS):
        by_loose[r["loose"]].append(r)
    merges, apart = [], []
    for rows in by_loose.values():
        sharing = [r for r in rows if r["shared_album"]]
        if len(sharing) == 1:
            merges.append(dict(sharing[0]))
            continue
        reason = (
            "matches several entities with an MBID that share an album" if sharing
            else "shares no album with the entity that has an MBID"
        )  # fmt: skip
        for r in rows:
            apart.append(dict(r) | {"reason": reason})
    return merges, apart


async def merge(conn: asyncpg.Connection, loose: int, firm: int, run_id: int | None) -> dict:
    """Fold `loose` into `firm` in one transaction; returns the log entry."""
    async with conn.transaction():
        row = await conn.fetchrow(
            """SELECT entity_id, type, name, norm_key, context_key, release_group_id, artist_id,
                      local_key, attrs::text AS attrs, resolve_status, created_at
                 FROM entity WHERE entity_id = $1""",
            loose,
        )
        if row is None:
            return {}
        log: dict = {
            "entity": dict(row) | {"attrs": json.loads(row["attrs"] or "{}"),
                                   "created_at": row["created_at"].isoformat()},
            "subject_of": [], "object_of": [], "context_of": [], "superseded": [],
            "links": [], "fetches": [], "at": datetime.now(UTC).isoformat(timespec="seconds"),
            "run_id": run_id,
        }  # fmt: skip
        claims = await conn.fetch(
            """SELECT assertion_id, subject_id, predicate, object_id, object_value, source,
                      source_url, evidence
                 FROM assertion WHERE subject_id = $1 OR object_id = $1""",
            loose,
        )
        for c in claims:
            s = firm if c["subject_id"] == loose else c["subject_id"]
            o = firm if c["object_id"] == loose else c["object_id"]
            key = claim_key(
                s, c["predicate"], o if o is not None else c["object_value"],
                c["source"], c["source_url"], c["evidence"],
            )  # fmt: skip
            taken = await conn.fetchval(
                "SELECT assertion_id FROM assertion WHERE claim_key = $1 AND assertion_id <> $2",
                key,
                c["assertion_id"],
            )
            if taken:  # the same claim already exists on the kept entity
                await conn.execute(
                    """UPDATE assertion SET subject_id = $2, object_id = $3, status = 'superseded',
                              replaces = NULL, updated_at = now() WHERE assertion_id = $1""",
                    c["assertion_id"], s, o,
                )  # fmt: skip
                log["superseded"].append(c["assertion_id"])
            else:
                await conn.execute(
                    """UPDATE assertion SET subject_id = $2, object_id = $3, claim_key = $4,
                              updated_at = now() WHERE assertion_id = $1""",
                    c["assertion_id"], s, o, key,
                )  # fmt: skip
            if c["subject_id"] == loose:
                log["subject_of"].append(c["assertion_id"])
            if c["object_id"] == loose:
                log["object_of"].append(c["assertion_id"])
        log["context_of"] = [
            r[0]
            for r in await conn.fetch(
                "UPDATE assertion SET album_context = $2 WHERE album_context = $1 RETURNING assertion_id",
                loose,
                firm,
            )
        ]
        links = await conn.fetch(
            "SELECT kind, url, source, status FROM entity_link WHERE entity_id = $1", loose
        )
        log["links"] = [dict(r) for r in links]
        await conn.executemany(
            """INSERT INTO entity_link (entity_id, kind, url, source, status)
               VALUES ($1, $2, $3, $4, $5) ON CONFLICT (entity_id, url) DO NOTHING""",
            [(firm, r["kind"], r["url"], r["source"], r["status"]) for r in links],
        )
        log["fetches"] = [
            r[0]
            for r in await conn.fetch(
                "UPDATE source_fetch SET entity_id = $2 WHERE entity_id = $1 RETURNING fetch_id",
                loose,
                firm,
            )
        ]
        await conn.execute("DELETE FROM entity WHERE entity_id = $1", loose)
        await conn.execute(
            """UPDATE entity SET attrs = jsonb_set(attrs, '{merged}',
                      coalesce(attrs -> 'merged', '[]'::jsonb) || $2::jsonb)
                WHERE entity_id = $1""",
            firm,
            json.dumps([log], default=str),
        )
    return log


async def unmerge(conn: asyncpg.Connection, firm: int, loose: int) -> int:
    """Undo one logged merge: the merged entity comes back with its id, and its claims, links
    and fetches point at it again. Returns the number of claims restored."""
    async with conn.transaction():
        attrs = json.loads(
            await conn.fetchval("SELECT attrs::text FROM entity WHERE entity_id = $1", firm) or "{}"
        )
        logs = attrs.get("merged") or []
        log = next((m for m in logs if m["entity"]["entity_id"] == loose), None)
        if log is None:
            raise ValueError(f"entity {firm} has no logged merge of {loose}")
        e = log["entity"]
        await conn.execute(
            """INSERT INTO entity (entity_id, type, name, norm_key, context_key, release_group_id,
                                   artist_id, local_key, attrs, resolve_status)
               OVERRIDING SYSTEM VALUE
               VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9::jsonb, $10)""",
            e["entity_id"], e["type"], e["name"], e["norm_key"], e["context_key"],
            e["release_group_id"], e["artist_id"], e["local_key"], json.dumps(e["attrs"]),
            e["resolve_status"],
        )  # fmt: skip
        for col, ids in (("subject_id", log["subject_of"]), ("object_id", log["object_of"])):
            await conn.execute(
                f"UPDATE assertion SET {col} = $1 WHERE assertion_id = ANY($2::bigint[])",
                loose,
                ids,
            )
        await conn.execute(
            "UPDATE assertion SET album_context = $1 WHERE assertion_id = ANY($2::bigint[])",
            loose,
            log["context_of"],
        )
        claims = await conn.fetch(
            """SELECT assertion_id, subject_id, predicate, object_id, object_value, source,
                      source_url, evidence FROM assertion WHERE assertion_id = ANY($1::bigint[])""",
            sorted(set(log["subject_of"]) | set(log["object_of"])),
        )
        for c in claims:
            obj = c["object_id"] if c["object_id"] is not None else c["object_value"]
            key = claim_key(c["subject_id"], c["predicate"], obj, c["source"], c["source_url"],
                            c["evidence"])  # fmt: skip
            await conn.execute(
                "UPDATE assertion SET claim_key = $2 WHERE assertion_id = $1 AND claim_key <> $2",
                c["assertion_id"],
                key,
            )
        if log["superseded"]:
            await conn.execute(
                """UPDATE assertion SET status = 'proposed' WHERE assertion_id = ANY($1::bigint[])""",
                log["superseded"],
            )
        await conn.executemany(
            """INSERT INTO entity_link (entity_id, kind, url, source, status)
               VALUES ($1, $2, $3, $4, $5) ON CONFLICT (entity_id, url) DO NOTHING""",
            [(loose, r["kind"], r["url"], r["source"], r["status"]) for r in log["links"]],
        )
        await conn.execute(
            "UPDATE source_fetch SET entity_id = $1 WHERE fetch_id = ANY($2::bigint[])",
            loose,
            log["fetches"],
        )
        rest = [m for m in logs if m is not log]
        await conn.execute(
            "UPDATE entity SET attrs = jsonb_set(attrs, '{merged}', $2::jsonb) WHERE entity_id = $1",
            firm,
            json.dumps(rest, default=str),
        )
    return len(claims)


PENDING_NAMES = """
SELECT DISTINCT e.entity_id, e.type, e.name, e.attrs ->> 'artist' AS artist
  FROM assertion a JOIN entity e ON e.entity_id IN (a.subject_id, a.object_id)
 WHERE a.status IN ('proposed', 'unread') AND e.mbid IS NULL AND e.local_key IS NULL
   AND e.resolve_status = 'unresolved' AND e.type IN ('artist', 'person', 'album')
   AND NOT (e.attrs ? 'search_checked')
 ORDER BY e.entity_id LIMIT $1
"""


async def resolve_posted_names(
    conn: asyncpg.Connection, mb: MusicBrainzClient, limit: int = 200
) -> dict[str, int]:
    """Names posted as written (`POST /assertions`) get an MBID by exact MusicBrainz search when
    exactly one artist or album matches and no entity holds that MBID yet; an MBID another
    entity already holds is left to the merge rule (same name and a shared album)."""
    counts: dict[str, int] = defaultdict(int)
    for e in await conn.fetch(PENDING_NAMES, limit):
        hit = None
        if e["type"] in ART:
            hits = names.exact_artists(await mb.search_artists(e["name"]), e["name"])
            if len(hits) == 1:
                a = hits[0]
                hit = (a["id"], "person" if a.get("type") == "Person" else "artist",
                       names.artist_attrs(a))  # fmt: skip
        else:
            title = e["name"].split(" – ", 1)[-1]
            hits = names.exact_release_group(
                await mb.search_release_groups(title, artist_name=e["artist"]), title
            )
            if len(hits) == 1:
                hit = (hits[0]["id"], "album",
                       {"year": names.year(hits[0].get("first-release-date"))})  # fmt: skip
        mark = {"search_checked": datetime.now(UTC).date().isoformat()}
        if hit is None:
            counts["not_found_or_ambiguous"] += 1
            await conn.execute(
                "UPDATE entity SET attrs = attrs || $2::jsonb WHERE entity_id = $1",
                e["entity_id"],
                json.dumps(mark),
            )
            continue
        mbid, type_, attrs = hit
        held = await conn.fetchval(
            """SELECT entity_id FROM entity WHERE mbid = $1::uuid
                 AND (type = $2 OR (type IN ('artist','person') AND $2 IN ('artist','person')))""",
            mbid,
            type_,
        )
        if held:
            counts["mbid_held_elsewhere"] += 1  # the merge rule decides
            await conn.execute(
                "UPDATE entity SET attrs = attrs || $2::jsonb WHERE entity_id = $1",
                e["entity_id"],
                json.dumps(mark | {"mb_candidate": mbid}),
            )
            continue
        await conn.execute(
            """UPDATE entity SET mbid = $2::uuid, type = $3, resolve_status = 'resolved',
                      attrs = attrs || $4::jsonb WHERE entity_id = $1""",
            e["entity_id"],
            mbid,
            type_,
            json.dumps({k: v for k, v in attrs.items() if v is not None} | mark),
        )
        counts["resolved"] += 1
    return dict(counts)
