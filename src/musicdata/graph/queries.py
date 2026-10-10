"""The graph API's reads and writes (companion spec section 11, Block A; graph spec section 10).

Plain async functions over a connection, so the router stays thin and tests can call them
directly. Everything reads accepted claims through the `edge` view (one current claim per edge),
except the brief's tracks and the questions, which read `assertion` for evidence and track lists.
"""

from __future__ import annotations

import json
from collections import defaultdict
from urllib.parse import urlparse

import asyncpg

from musicdata.graph.batch import SENTENCES
from musicdata.graph.claims import claim_key
from musicdata.graph.entities import upsert_entity
from musicdata.graph.fetch import is_english_page
from musicdata.graph.report import FACETS
from musicdata.identity import norm_key, title_key

LINK_KINDS = {"wikipedia", "wikidata", "musicbrainz", "discogs", "allmusic", "bandcamp", "official",
              "label_page", "interview", "review", "live_session", "streaming", "liner_notes",
              "other"}  # fmt: skip
DEAD_SITES = ("rateyourmusic.com",)  # cached pages often held the wrong album (graph spec, P04)
# edge-vocabulary section 7, one research question per gap
QUESTIONS = {
    "credits": "Who produced, engineered and played on {album}?",
    "recording": "Where and when was {album} recorded?",
    "label": "Which label released {album}, and in which year?",
    "people": "Who was in {artist} when {album} was made?",
    "lineage": "Which artists or albums do critics compare {album} to, or does {artist} name as "
    "an influence? Which songs on it are covers, and of whom?",
}
BUDGET = {"firecrawl_credits_per_album": 35, "pages": 4, "searches": 2}  # vocabulary 6a

EDGES = """
SELECT e.subject_id, e.predicate, e.object_id, e.object_value, e.confidence, e.sources,
       p.facet, a.assertion_id, a.evidence, a.source_url, a.basis, a.qualifiers::text AS qualifiers,
       s.name AS subject_name, s.type AS subject_type, s.release_group_id AS subject_rg,
       o.name AS object_name, o.type AS object_type, o.release_group_id AS object_rg
  FROM edge e
  JOIN predicate p ON p.name = e.predicate
  JOIN assertion a ON a.assertion_id = e.best_assertion_id
  JOIN entity s ON s.entity_id = e.subject_id
  LEFT JOIN entity o ON o.entity_id = e.object_id
 WHERE e.subject_id = $1 OR e.object_id = $1
"""


def _edge(r: asyncpg.Record, me: int) -> dict:
    out = r["subject_id"] == me
    other = (
        {"entity_id": r["object_id"], "type": r["object_type"], "name": r["object_name"],
         "release_group_id": r["object_rg"]}
        if out
        else {"entity_id": r["subject_id"], "type": r["subject_type"], "name": r["subject_name"],
              "release_group_id": r["subject_rg"]}
    )  # fmt: skip
    if out and r["object_id"] is None:
        other = {"value": r["object_value"]}
    return {
        "predicate": r["predicate"],
        "direction": "out" if out else "in",
        "other": other,
        "confidence": r["confidence"],
        "sources": list(r["sources"]),
        "basis": r["basis"],
        "evidence": r["evidence"],
        "source_url": r["source_url"],
        "assertion_id": r["assertion_id"],
        "qualifiers": json.loads(r["qualifiers"] or "{}"),
    }


async def edges_by_facet(conn: asyncpg.Connection, entity_id: int) -> dict[str, list[dict]]:
    grouped: dict[str, list[dict]] = defaultdict(list)
    for r in await conn.fetch(EDGES, entity_id):
        grouped[r["facet"]].append(_edge(r, entity_id))
    for edges in grouped.values():
        edges.sort(key=lambda e: (e["predicate"], -float(e["confidence"] or 0)))
    return dict(grouped)


async def search_entities(
    conn: asyncpg.Connection, q: str, type_: str | None, limit: int
) -> list[asyncpg.Record]:
    return await conn.fetch(
        """SELECT entity_id, type, name, mbid, release_group_id, resolve_status,
                  round(similarity(name, $1)::numeric, 2) AS similarity
             FROM entity
            WHERE ($2::text IS NULL OR type = $2)
              AND (name % $1 OR name ILIKE '%' || $1 || '%')
            ORDER BY similarity(name, $1) DESC, name LIMIT $3""",
        q,
        type_,
        limit,
    )


async def entity_detail(conn: asyncpg.Connection, entity_id: int) -> dict | None:
    row = await conn.fetchrow(
        """SELECT entity_id, type, name, mbid, release_group_id, artist_id, local_key,
                  attrs::text AS attrs, resolve_status
             FROM entity WHERE entity_id = $1""",
        entity_id,
    )
    if row is None:
        return None
    links = await conn.fetch(
        "SELECT kind, url, source, status FROM entity_link WHERE entity_id = $1 ORDER BY kind, url",
        entity_id,
    )
    maps = await conn.fetch(
        "SELECT map, coords::text AS coords FROM map_membership WHERE entity_id = $1", entity_id
    )
    return dict(row) | {
        "attrs": json.loads(row["attrs"] or "{}"),
        "links": [dict(r) for r in links],
        "maps": [{"map": r["map"], "coords": json.loads(r["coords"])} for r in maps],
        "edges": await edges_by_facet(conn, entity_id),
    }


async def neighbors(
    conn: asyncpg.Connection,
    entity_id: int,
    predicates: list[str] | None,
    direction: str,
    min_confidence: float,
    limit: int,
) -> list[asyncpg.Record]:
    return await conn.fetch(
        """SELECT n.entity_id, n.type, n.name, n.release_group_id, x.predicate, x.direction,
                  x.confidence
             FROM (SELECT object_id AS other, predicate, 'out' AS direction, confidence
                     FROM edge WHERE subject_id = $1 AND object_id IS NOT NULL
                   UNION ALL
                   SELECT subject_id, predicate, 'in', confidence
                     FROM edge WHERE object_id = $1) x
             JOIN entity n ON n.entity_id = x.other
            WHERE ($2::text[] IS NULL OR x.predicate = ANY($2::text[]))
              AND ($3 = 'both' OR x.direction = $3)
              AND x.confidence >= $4
            ORDER BY x.confidence DESC, n.name LIMIT $5""",
        entity_id,
        predicates,
        direction,
        min_confidence,
        limit,
    )


async def album_entity(conn: asyncpg.Connection, release_group_id: int) -> int | None:
    return await conn.fetchval(
        "SELECT entity_id FROM entity WHERE type = 'album' AND release_group_id = $1",
        release_group_id,
    )


async def album_pages(conn: asyncpg.Connection, album_id: int) -> list[dict]:
    """The album's cached pages: those fetched for it, and any linked to it with an ok link."""
    rows = await conn.fetch(
        """SELECT DISTINCT ON (f.url) f.fetch_id, f.url, f.title, f.mode, f.fetched_at
             FROM source_fetch f
            WHERE f.ok
              AND ((f.entity_id = $1
                    AND NOT EXISTS (SELECT 1 FROM entity_link d WHERE d.entity_id = $1
                                     AND d.url = f.url AND d.status <> 'ok'))
                   OR EXISTS (SELECT 1 FROM entity_link l WHERE l.entity_id = $1
                               AND l.url = f.url AND l.status = 'ok'))
            ORDER BY f.url, f.fetched_at DESC""",
        album_id,
    )
    return [dict(r) | {"site": (urlparse(r["url"]).hostname or "").removeprefix("www.")}
            for r in rows]  # fmt: skip


def track_notes(
    tracks: list[dict], credits: list[dict], lineage: list[dict]
) -> tuple[list[dict], list[dict]]:
    """Each track with the credits whose `tracks` qualifier names it and the covers or
    borrowings about it; the rest of the credits (album-wide, or without a track list) apart."""
    by_key = {title_key(t["title"]): t for t in tracks}
    for t in tracks:
        t["credits"], t["lineage"] = [], []
    album_wide = []
    for c in credits:
        named = c["qualifiers"].get("tracks")
        if isinstance(named, list) and named:
            hit = False
            for title in named:
                t = by_key.get(title_key(title))
                if t is not None:
                    t["credits"].append(c)
                    hit = True
            if c["qualifiers"].get("album_wide") or not hit:
                album_wide.append(c)
        else:
            album_wide.append(c)
    for item in lineage:
        name = item["recording"] or ""
        t = by_key.get(title_key(name.split(" – ", 1)[-1]))
        if t is not None:
            t["lineage"].append(item)
    return tracks, album_wide


def gaps(edges: dict[str, list[dict]]) -> list[str]:
    """The facets with no accepted claim (graph report FACETS)."""
    have = {e["predicate"] for es in edges.values() for e in es}
    return [facet for facet, preds in FACETS.items() if not have & set(preds)]


def research_questions(missing: list[str], artist: str, album: str) -> list[str]:
    return [QUESTIONS[f].format(artist=artist, album=album) for f in missing if f in QUESTIONS]


async def album_brief(conn: asyncpg.Connection, release_group_id: int) -> dict | None:
    """Edge-vocabulary section 8: identity and tracks, where it sits, edges by facet, pages,
    links, gaps and research questions, Matt's context, and a budget hint."""
    rg = await conn.fetchrow(
        """SELECT rg.release_group_id, rg.mbid, rg.title, a.name AS artist, a.mbid AS artist_mbid,
                  rg.primary_type, rg.secondary_types, rg.first_release_year,
                  t.release_mbid, t.source AS tracklist_source
             FROM release_group rg JOIN artist a ON a.artist_id = rg.artist_id
             LEFT JOIN release_group_tracklist t USING (release_group_id)
            WHERE rg.release_group_id = $1""",
        release_group_id,
    )
    if rg is None:
        return None
    tracks = [
        dict(r)
        for r in await conn.fetch(
            """SELECT position, title, recording_mbid, length_ms FROM release_group_track
                WHERE release_group_id = $1 ORDER BY position""",
            release_group_id,
        )
    ]
    album_id = await album_entity(conn, release_group_id)
    edges: dict[str, list[dict]] = {}
    credits: list[dict] = []
    lineage: list[dict] = []
    links: list[dict] = []
    pages: list[dict] = []
    maps: list[dict] = []
    if album_id is not None:
        edges = await edges_by_facet(conn, album_id)
        credits = [
            {"name": e["other"].get("name"), "entity_id": e["other"].get("entity_id"),
             "roles": e["qualifiers"].get("role") or [], "qualifiers": e["qualifiers"],
             "assertion_id": e["assertion_id"], "sources": e["sources"]}
            for e in edges.get("people", []) if e["predicate"] == "credited_on"
        ]  # fmt: skip
        lineage = [
            {"predicate": r["predicate"], "recording": r["subject_name"],
             "other": r["object_name"], "evidence": r["evidence"],
             "source_url": r["source_url"], "assertion_id": r["assertion_id"]}
            for r in await conn.fetch(
                """SELECT a.predicate, s.name AS subject_name, o.name AS object_name,
                          a.evidence, a.source_url, a.assertion_id
                     FROM assertion a JOIN entity s ON s.entity_id = a.subject_id
                     JOIN entity o ON o.entity_id = a.object_id
                    WHERE a.album_context = $1 AND a.status = 'accepted'
                      AND a.predicate IN ('covers', 'interpolates', 'samples')""",
                album_id,
            )
        ]  # fmt: skip
        links = [
            dict(r)
            for r in await conn.fetch(
                """SELECT kind, url, source FROM entity_link
                    WHERE entity_id = $1 AND status = 'ok' ORDER BY kind, url""",
                album_id,
            )
        ]
        pages = await album_pages(conn, album_id)
        maps = [
            {"map": r["map"], "coords": json.loads(r["coords"])}
            for r in await conn.fetch(
                "SELECT map, coords::text AS coords FROM map_membership WHERE entity_id = $1",
                album_id,
            )
        ]
    tracks, album_credits = track_notes(tracks, credits, lineage)
    missing = gaps(edges)
    stat = await conn.fetchrow(
        """SELECT listens, full_sessions, partial_sessions, best_completion, last_listened_at
             FROM release_group_stat WHERE release_group_id = $1""",
        release_group_id,
    )
    lists = await conn.fetch(
        """SELECT l.slug, e.position, e.priority, s.status
             FROM list_entry_status s JOIN list_entry e USING (entry_id)
             JOIN list l ON l.list_id = e.list_id
            WHERE s.release_group_id = $1 ORDER BY l.slug""",
        release_group_id,
    )
    heard = (
        "heard" if stat and stat["full_sessions"]
        else "started" if stat and stat["partial_sessions"] else "unheard"
    )  # fmt: skip
    return {
        "identity": dict(rg) | {"album_entity_id": album_id},
        "tracks": tracks,
        "album_credits": album_credits,
        "maps": maps,
        "edges": edges,
        "pages": pages,
        "links": links,
        "gaps": missing,
        "research_questions": research_questions(missing, rg["artist"], rg["title"]),
        "context": {
            "status": heard,
            "stat": dict(stat) if stat else None,
            "lists": [dict(r) for r in lists],
        },  # fmt: skip
        "budget": BUDGET,
    }


# --- writes ------------------------------------------------------------------------------------


class GraphInputError(ValueError):
    """A request the graph refuses, with the reason."""


async def _entity_as_written(conn: asyncpg.Connection, e: dict) -> int:
    """An entity for a posted name: by atlas id, release group or MBID when given, else the name
    as written (`unresolved` until `graph verify --pending` resolves it)."""
    type_, name = e.get("type"), e.get("name")
    if not type_ or not name:
        raise GraphInputError("every subject and object needs a type and a name")
    if e.get("atlas_id"):
        eid = await conn.fetchval(
            "SELECT entity_id FROM map_membership WHERE map = 'v_atlas' AND coords ->> 'atlas_id' = $1",
            e["atlas_id"],
        )
        if eid:
            return eid
    if e.get("release_group_id"):
        eid = await album_entity(conn, int(e["release_group_id"]))
        if eid:
            return eid
    attrs = {k: e[k] for k in ("artist", "year", "album") if e.get(k)}
    context = norm_key(e.get("artist") or "") if type_ in ("album", "recording", "work") else ""
    if e.get("mbid"):
        return await upsert_entity(conn, type_, name, mbid=e["mbid"], attrs=attrs, context=context)
    return await upsert_entity(
        conn, type_, name, attrs=attrs, status="unresolved", context=context, keep_status=True
    )


async def post_assertions(conn: asyncpg.Connection, claims: list[dict]) -> dict[str, int]:
    """Proposed claims, names as written; returns claim label → assertion id (an existing claim
    with the same label or the same key keeps its id)."""
    predicates = {r[0] for r in await conn.fetch("SELECT name FROM predicate")}
    out: dict[str, int] = {}
    for c in claims:
        label, source, url = c.get("claim_id"), str(c.get("source") or ""), c.get("source_url")
        if not label or c.get("predicate") not in predicates or not c.get("evidence"):
            raise GraphInputError(f"{label}: needs claim_id, a known predicate and evidence")
        if source.startswith("map:"):
            raise GraphInputError(f"{label}: the atlas is not a source of claims (spec v5)")
        if url and not is_english_page(url):
            raise GraphInputError(f"{label}: claims come from English pages (spec v8)")
        if source == "wikipedia" and url and "wikipedia.org" not in url:
            source = "web:" + (urlparse(url).hostname or "").removeprefix("www.")
        async with conn.transaction():
            s = await _entity_as_written(conn, c["subject"])
            o = await _entity_as_written(conn, c["object"])
            ctx = None
            if c.get("album"):
                ctx = await conn.fetchval(
                    """SELECT entity_id FROM map_membership
                        WHERE map = 'v_atlas' AND coords ->> 'atlas_id' = $1""",
                    c["album"],
                )
            elif c.get("release_group_id"):
                ctx = await album_entity(conn, int(c["release_group_id"]))
            if (c.get("album") or c.get("release_group_id")) and ctx is None:
                raise GraphInputError(
                    f"{label}: the graph holds no album {c.get('album') or c.get('release_group_id')}"
                )
            batch_id = None
            if c.get("batch"):
                batch_id = await conn.fetchval(
                    "SELECT batch_id FROM graph_batch WHERE label = $1", c["batch"]
                )
            fetch_id = await conn.fetchval(
                "SELECT fetch_id FROM source_fetch WHERE ok AND url = $1 ORDER BY fetched_at DESC LIMIT 1",
                (url or "").split("#")[0],
            )
            key = claim_key(s, c["predicate"], o, source, url, c["evidence"])
            new = await conn.fetchval(
                """INSERT INTO assertion (claim_key, claim_label, subject_id, predicate, object_id,
                           qualifiers, source, extractor, basis, direction, evidence, source_url,
                           fetch_id, album_context, batch_id, status, asserted_by)
                   VALUES ($1, $2, $3, $4, $5, $6::jsonb, $7, $8, $9, $10, $11, $12, $13, $14,
                           $15, 'proposed', 'POST /assertions')
                   ON CONFLICT DO NOTHING RETURNING assertion_id""",
                key, label, s, c["predicate"], o,
                json.dumps(c.get("qualifiers") or {}, default=str), source,
                c.get("extractor") or "claude", c.get("basis") or "reported", c.get("direction"),
                c["evidence"], url, fetch_id, ctx, batch_id,
            )  # fmt: skip
            if new is None:
                new = await conn.fetchval(
                    "SELECT assertion_id FROM assertion WHERE claim_label = $1 OR claim_key = $2",
                    label,
                    key,
                )
        out[label] = new
    return out


async def post_link(
    conn: asyncpg.Connection, entity_id: int, kind: str, url: str, source: str
) -> dict:
    if kind not in LINK_KINDS:
        raise GraphInputError(f"unknown link kind {kind}")
    host = (urlparse(url).hostname or "").lower()
    if not is_english_page(url) or host.endswith(DEAD_SITES):
        raise GraphInputError("links point at English pages, and Rate Your Music is not read")
    if not await conn.fetchval("SELECT 1 FROM entity WHERE entity_id = $1", entity_id):
        raise GraphInputError(f"no entity {entity_id}")
    added = await conn.fetchval(
        """INSERT INTO entity_link (entity_id, kind, url, source) VALUES ($1, $2, $3, $4)
           ON CONFLICT (entity_id, url) DO NOTHING RETURNING true""",
        entity_id,
        kind,
        url,
        source,
    )
    return {"entity_id": entity_id, "url": url, "added": bool(added)}


async def set_link_status(conn: asyncpg.Connection, url: str, status: str) -> int:
    """Mark every registered link to a URL ok, dead or blocked (a page that does not exist, or
    holds the wrong album); fetch plans and batch exports read `ok` links only. Returns the count."""
    if status not in ("ok", "dead", "blocked"):
        raise GraphInputError(f"link status is ok, dead or blocked, not {status}")
    done = await conn.execute(
        "UPDATE entity_link SET status = $2, verified_at = now() WHERE url = $1", url, status
    )
    return int(done.split()[-1])


async def assertions_by_id(conn: asyncpg.Connection, ids: list[int]) -> list[asyncpg.Record]:
    return await conn.fetch(
        """SELECT a.assertion_id, a.claim_label, a.status, s.name AS subject, a.predicate,
                  o.name AS object, a.evidence, a.source, a.source_url, a.fetch_id,
                  a.confidence, a.qualifiers::text AS qualifiers
             FROM assertion a JOIN entity s ON s.entity_id = a.subject_id
             LEFT JOIN entity o ON o.entity_id = a.object_id
            WHERE a.assertion_id = ANY($1::bigint[]) ORDER BY a.assertion_id""",
        ids,
    )


async def open_questions(conn: asyncpg.Connection) -> list[dict]:
    rows = await conn.fetch(
        """SELECT a.assertion_id, a.claim_label, a.predicate, a.evidence, a.source_url,
                  a.reader_reason, a.qualifiers::text AS qualifiers,
                  s.name AS s_name, o.name AS o_name, o.attrs ->> 'artist' AS o_artist
             FROM assertion a JOIN entity s ON s.entity_id = a.subject_id
             LEFT JOIN entity o ON o.entity_id = a.object_id
            WHERE a.status = 'ask_matt' ORDER BY a.claim_label"""
    )
    out = []
    for r in rows:
        template = SENTENCES.get(r["predicate"], "{s} " + r["predicate"] + " {o}")
        quals = json.loads(r["qualifiers"] or "{}")
        claim = template.format(s=r["s_name"], o=r["o_name"], oa=r["o_artist"] or r["o_name"],
                                kind=quals.get("kind", "collaborator"))  # fmt: skip
        out.append(
            {"assertion_id": r["assertion_id"], "claim_label": r["claim_label"], "claim": claim,
             "quote": r["evidence"], "source_url": r["source_url"],
             "reader_reason": r["reader_reason"]}
        )  # fmt: skip
    return out
