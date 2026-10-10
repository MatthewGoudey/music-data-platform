"""`musicdata graph import` (GRAPH_SPEC 7.1): the free baseline for a slice's albums, and
`graph link`. No Firecrawl.

Per target album, by its release group MBID: URL relations as links (plus the English
Wikipedia page from Wikidata when MusicBrainz holds none); the canonical release by the edition
rule (resolve/canonical.py) with its credits, places, works and labels; the album artists'
memberships and areas; Discogs credits, labels and styles; the atlas note's `source_urls` as
links. Claims go in as `proposed` (Block D verifies them), one transaction per album, and
`graph_album.baseline_at` marks the album done, so a stopped run resumes where it left off.
"""

from __future__ import annotations

import json
from collections import defaultdict

import asyncpg

from musicdata.clients.discogs import DiscogsClient
from musicdata.clients.musicbrainz import MusicBrainzClient, NotFoundError
from musicdata.clients.wikidata import WikidataClient
from musicdata.config import get_settings
from musicdata.db import connection
from musicdata.graph.baseline import (
    ALBUM,
    ClaimSpec,
    Ent,
    discogs_claims,
    link_kind,
    musicbrainz_claims,
    url_links,
)
from musicdata.graph.claims import claim_key
from musicdata.identity import norm_key
from musicdata.jobs.runs import JobFn, RunContext
from musicdata.log import get_logger
from musicdata.resolve.canonical import choose_release

log = get_logger(__name__)

MAP = "v_atlas"
SLICES = {
    "crazy_horse": {
        "lanes": ("C1", "V1", "V20"),
        "path": "MJ Lenderman to Neil Young: the Crazy Horse line",
    }
}
EVIDENCE_MAX = 300
BASELINE_CONFIDENCE = 0.9

TARGETS = """
SELECT DISTINCT ON (e.facets ->> 'atlas_id')
       e.facets ->> 'atlas_id' AS atlas_id, e.raw_artist, e.raw_album, e.resolve_status,
       e.priority, e.start_here, e.lane_id, e.facets ->> 'source_urls' AS source_urls,
       (e.facets ->> 'atlas_id') = ANY($3::text[]) AS on_path,
       en.entity_id, rg.mbid::text AS mbid, rg.first_release_year
  FROM list_entry e
  JOIN list l USING (list_id)
  LEFT JOIN release_group rg ON rg.release_group_id = e.release_group_id
  LEFT JOIN entity en ON en.release_group_id = e.release_group_id AND en.type = 'album'
 WHERE l.slug = 'v_atlas' AND e.review_status = 'accepted'
   AND ($1::text[] IS NULL OR e.lane_id = ANY($1::text[])
        OR (e.facets ->> 'atlas_id') = ANY($3::text[]))
   AND ($2::int IS NULL OR e.release_group_id = $2)
 ORDER BY e.facets ->> 'atlas_id', e.entry_id
"""

TIER = {"Essential": 0, "Recommended": 2, "Deep cut": 3}


def order_key(t: asyncpg.Record) -> tuple:
    """Essential, then start-here or on a path, then Recommended, then Deep cut; by atlas id."""
    tier = TIER.get(t["priority"] or "", 2)
    if tier > 0 and (t["start_here"] or t["on_path"]):
        tier = 1
    return (tier, t["atlas_id"] or "")


def label_id(t) -> str:
    """The album part of a claim label: its atlas id, else rg<release_group_id> (spec 3.2)."""
    return t["atlas_id"] or f"rg{t['release_group_id']}"


def slice_label(slice_name: str) -> str:
    return "rg" if slice_name.startswith("rg:") else slice_name


async def ensure_album(conn: asyncpg.Connection, release_group_id: int) -> dict | None:
    """The album entity for a release group, created when missing (companion spec 3.2), as a
    target row; None when the release group does not exist."""
    rg = await conn.fetchrow(
        """SELECT rg.release_group_id, rg.mbid::text AS mbid, rg.title, rg.norm_key,
                  rg.first_release_year, a.name AS artist, a.norm_key AS artist_key
             FROM release_group rg JOIN artist a ON a.artist_id = rg.artist_id
            WHERE rg.release_group_id = $1""",
        release_group_id,
    )
    if rg is None:
        return None
    eid = await conn.fetchval(
        "SELECT entity_id FROM entity WHERE release_group_id = $1", release_group_id
    )
    if eid is None and rg["mbid"]:
        eid = await conn.fetchval(
            """UPDATE entity SET release_group_id = $1
                WHERE type = 'album' AND mbid = $2::uuid AND release_group_id IS NULL
            RETURNING entity_id""",
            release_group_id,
            rg["mbid"],
        )
    if eid is None:
        eid = await conn.fetchval(
            """INSERT INTO entity (type, name, norm_key, context_key, mbid, release_group_id, attrs)
               VALUES ('album', $1, $2, $3, $4::uuid, $5, $6::jsonb) RETURNING entity_id""",
            rg["title"],
            rg["norm_key"] or norm_key(rg["title"]) or rg["title"].casefold(),
            rg["artist_key"] or "",
            rg["mbid"],
            release_group_id,
            json.dumps({"year": rg["first_release_year"], "artist": rg["artist"]}),
        )
    atlas_id = await conn.fetchval(
        "SELECT coords ->> 'atlas_id' FROM map_membership WHERE entity_id = $1 AND map = $2",
        eid,
        MAP,
    )
    priority = await conn.fetchval("SELECT priority FROM graph_album WHERE entity_id = $1", eid)
    return {
        "atlas_id": atlas_id, "raw_artist": rg["artist"], "raw_album": rg["title"],
        "resolve_status": "resolved", "priority": priority, "start_here": False, "lane_id": None,
        "source_urls": None, "on_path": False, "entity_id": eid, "mbid": rg["mbid"],
        "first_release_year": rg["first_release_year"], "release_group_id": release_group_id,
    }  # fmt: skip


# Companion spec 3.3: the default scope in its order — 1 pages opened without a baseline, 2 the
# near-queue set by rank, 3 heard albums (a full session), 4 the V atlas by priority — each release
# group once, at its earliest place. `$1` picks the step an album still needs.
DEFAULT_ORDER = """
WITH src AS (
    SELECT en.release_group_id AS rg, 1 AS grp, 0 AS ord, NULL::text AS priority,
           false AS start_here
      FROM graph_album g JOIN entity en USING (entity_id)
     WHERE 'opened' = ANY(g.slices) AND en.release_group_id IS NOT NULL
    UNION ALL
    SELECT en.release_group_id, 2, (g.notes ->> 'near_queue_rank')::int, NULL, false
      FROM graph_album g JOIN entity en USING (entity_id)
     WHERE 'near_queue' = ANY(g.slices) AND en.release_group_id IS NOT NULL
    UNION ALL
    SELECT release_group_id, 3, -full_sessions, NULL, false
      FROM release_group_stat WHERE full_sessions > 0
    UNION ALL
    SELECT e.release_group_id, 4,
           CASE e.priority WHEN 'Essential' THEN 0 WHEN 'Recommended' THEN 2 ELSE 3 END
             - CASE WHEN e.start_here THEN 1 ELSE 0 END,
           e.priority, e.start_here
      FROM list_entry e JOIN list l USING (list_id)
     WHERE l.slug = 'v_atlas' AND e.resolve_status = 'resolved' AND e.review_status = 'accepted'
), first AS (
    SELECT DISTINCT ON (rg) rg, grp, ord, priority, start_here
      FROM src WHERE rg IS NOT NULL ORDER BY rg, grp, ord
)
SELECT f.rg, f.priority, f.start_here
  FROM first f
  JOIN release_group r ON r.release_group_id = f.rg
  LEFT JOIN entity en ON en.release_group_id = f.rg AND en.type = 'album'
  LEFT JOIN graph_album g ON g.entity_id = en.entity_id
 WHERE CASE $1::text
         WHEN 'baseline' THEN r.mbid IS NOT NULL AND g.baseline_at IS NULL
         WHEN 'fetch' THEN g.baseline_at IS NOT NULL AND g.fetched_at IS NULL
         WHEN 'facts' THEN g.fetched_at IS NOT NULL AND g.facts_at IS NULL
         ELSE true END
 ORDER BY f.grp, f.ord, f.rg
 LIMIT $2
"""


async def scope_targets(
    conn: asyncpg.Connection, scope: str, pending: str | None = None, limit: int | None = None
) -> list[dict]:
    """Companion spec 3.2: `rg:<id>[,<id>…]`, `opened` (pages opened without a baseline) or
    `default` (3.3, in its order; `pending` keeps the albums that still need that step:
    baseline, fetch or facts, and `limit` caps how many become targets)."""
    if scope == "default":
        out = []
        for r in await conn.fetch(DEFAULT_ORDER, pending, limit):
            t = await ensure_album(conn, r["rg"])
            if t is None:
                continue
            if r["priority"]:
                t["priority"], t["start_here"] = r["priority"], r["start_here"]
            out.append(t)
        return out
    if scope == "opened":
        rgs = [
            r[0]
            for r in await conn.fetch(
                """SELECT e.release_group_id FROM graph_album g JOIN entity e USING (entity_id)
                    WHERE 'opened' = ANY(g.slices) AND g.baseline_at IS NULL
                      AND e.release_group_id IS NOT NULL ORDER BY g.entity_id"""
            )
        ]
    elif scope.startswith("rg:"):
        rgs = [int(x) for x in scope[3:].split(",") if x.strip()]
    else:
        raise ValueError(f"unknown scope {scope}")
    out = []
    for rg in rgs:
        t = await ensure_album(conn, rg)
        if t is not None:
            out.append(t)
    return out


async def slice_targets(
    conn: asyncpg.Connection, slice_name: str, pending: str | None = None, limit: int | None = None
) -> list:
    """A slice's atlas entries in research order (GRAPH_SPEC 6), resolved or not; or a scope's
    release groups (`rg:<ids>`, `opened`, `default`; companion spec 3.2–3.3). `pending` and
    `limit` narrow the default scope only (thousands of albums); slices stay whole."""
    if slice_name in ("opened", "default") or slice_name.startswith("rg:"):
        return await scope_targets(conn, slice_name, pending, limit)
    lanes, rg, path_ids = None, None, []
    if slice_name.startswith("album:"):
        rg = int(slice_name.split(":", 1)[1])
    elif slice_name in SLICES:
        spec = SLICES[slice_name]
        lanes = list(spec["lanes"])
        path_ids = [
            r[0]
            for r in await conn.fetch(
                "SELECT atlas_id FROM atlas_path WHERE path = $1", spec["path"]
            )
        ]
    elif slice_name != MAP:
        raise ValueError(f"unknown slice {slice_name}")
    rows = await conn.fetch(TARGETS, lanes, rg, path_ids)
    return sorted(rows, key=order_key)


async def ensure_entity(conn: asyncpg.Connection, ent: Ent, cache: dict) -> int:
    """The entity for a claim's subject or object: one per MusicBrainz ID (one per artist
    across artist and person), else one per type and name key."""
    k = (ent.type, ent.mbid or norm_key(ent.name))
    if k in cache:
        return cache[k]
    key = norm_key(ent.name) or ent.name.casefold()
    if ent.mbid and ent.type in ("artist", "person"):
        eid = await conn.fetchval(
            """INSERT INTO entity (type, name, norm_key, mbid)
               VALUES ($1, $2, $3, $4)
               ON CONFLICT (mbid) WHERE mbid IS NOT NULL AND type IN ('artist','person')
                  DO UPDATE SET name = EXCLUDED.name,
                                type = CASE WHEN EXCLUDED.type = 'person' THEN 'person'
                                            ELSE entity.type END
               RETURNING entity_id""",
            ent.type,
            ent.name,
            key,
            ent.mbid,
        )
    elif ent.mbid:
        eid = await conn.fetchval(
            """INSERT INTO entity (type, name, norm_key, mbid)
               VALUES ($1, $2, $3, $4)
               ON CONFLICT (type, mbid) WHERE mbid IS NOT NULL AND type NOT IN ('artist','person')
                  DO UPDATE SET name = EXCLUDED.name
               RETURNING entity_id""",
            ent.type,
            ent.name,
            key,
            ent.mbid,
        )
    else:
        eid = await conn.fetchval(
            """INSERT INTO entity (type, name, norm_key, resolve_status)
               VALUES ($1, $2, $3, 'unresolved')
               ON CONFLICT (type, norm_key, context_key) WHERE mbid IS NULL AND local_key IS NULL
                  DO UPDATE SET name = entity.name
               RETURNING entity_id""",
            ent.type,
            ent.name,
            key,
        )
    cache[k] = eid
    return eid


async def write_album(
    conn: asyncpg.Connection,
    album_id: int,
    atlas_id: str,
    claims: list[ClaimSpec],
    links: list[tuple[str, str, str]],
    notes: dict,
    run_id: int | None,
) -> int:
    cache: dict = {}
    rows = []
    for n, c in enumerate(claims, 1):
        s = album_id if c.subject is ALBUM else await ensure_entity(conn, c.subject, cache)
        o = album_id if c.obj is ALBUM else await ensure_entity(conn, c.obj, cache)
        evidence = c.evidence[:EVIDENCE_MAX]
        rows.append(
            (
                claim_key(s, c.predicate, o, c.source, c.source_url, evidence),
                f"R{run_id}-{atlas_id}-F{n:03d}",
                s,
                c.predicate,
                o,
                json.dumps(c.qualifiers, default=str),
                c.source,
                c.extractor,
                evidence,
                c.source_url,
                album_id,
                run_id,
            )
        )
    await conn.executemany(
        """INSERT INTO assertion (claim_key, claim_label, subject_id, predicate, object_id,
                                  qualifiers, source, extractor, basis, evidence, source_url,
                                  album_context, status, confidence, asserted_by, pipeline_run_id)
           VALUES ($1, $2, $3, $4, $5, $6::jsonb, $7, $8, 'documented', $9, $10, $11,
                   'proposed', 0.9, 'graph import', $12)
           ON CONFLICT (claim_key) DO UPDATE  -- a re-import refreshes qualifiers (track lists)
              SET qualifiers = EXCLUDED.qualifiers, updated_at = now()""",
        rows,
    )
    await conn.executemany(
        """INSERT INTO entity_link (entity_id, kind, url, source)
           VALUES ($1, $2, $3, $4) ON CONFLICT (entity_id, url) DO NOTHING""",
        [(album_id, kind, url, source) for kind, url, source in links],
    )
    await conn.execute(
        """UPDATE graph_album SET baseline_at = now(), notes = notes || $2::jsonb
            WHERE entity_id = $1""",
        album_id,
        json.dumps(notes, default=str),
    )
    return len(rows)


DB_STOP_MB = 650  # companion spec 9: stop the default import and report at 650 MB
ERRORS_MAX = 20  # a run that fails this many albums stops and fails (MusicBrainz down)


async def database_mb(conn: asyncpg.Connection) -> float:
    return round(
        int(await conn.fetchval("SELECT pg_database_size(current_database())")) / 1048576, 1
    )


def graph_import(*, slice_name: str, limit: int | None = None, refresh: bool = False) -> JobFn:
    async def _run(ctx: RunContext) -> None:
        settings = get_settings()
        counts: dict[str, int] = defaultdict(int)
        default = slice_name == "default"
        async with connection(ctx.pool) as conn:
            size = await database_mb(conn)
            ctx.notes.update(database_mb=size)
            if default and size >= DB_STOP_MB:
                ctx.notes.update(slice=slice_name, stopped_at_size=True)
                log.warning("graph import stopped: database size", extra={"mb": size})
                return
            targets = await slice_targets(
                conn, slice_name, "baseline" if default and not refresh else None, limit
            )
            for t in targets:
                if t["entity_id"] is None:
                    continue
                await conn.execute(
                    """INSERT INTO graph_album (entity_id, slices, priority)
                       VALUES ($1, ARRAY[$2::text], $3)
                       ON CONFLICT (entity_id) DO UPDATE
                          SET slices = CASE WHEN $2::text = ANY(graph_album.slices)
                                            THEN graph_album.slices
                                            ELSE graph_album.slices || $2::text END,
                              priority = EXCLUDED.priority""",
                    t["entity_id"],
                    slice_label(slice_name),
                    t["priority"],
                )
            done = {
                r[0]
                for r in await conn.fetch(
                    "SELECT entity_id FROM graph_album WHERE baseline_at IS NOT NULL"
                )
            }
        todo = [t for t in targets if t["entity_id"] and t["mbid"]]
        if not refresh:
            todo = [t for t in todo if t["entity_id"] not in done]
        counts["targets"] = len(targets)
        counts["no_match"] = sum(1 for t in targets if not t["entity_id"] or not t["mbid"])
        if limit is not None:
            todo = todo[:limit]
        token = settings.discogs_token.get_secret_value() if settings.discogs_token else None
        ua = settings.musicbrainz_user_agent
        async with (
            MusicBrainzClient(user_agent=ua) as mb,
            DiscogsClient(user_agent=ua, token=token) as dg,
            WikidataClient(user_agent=ua) as wd,
        ):
            artists_seen: dict[str, dict] = {}
            for i, t in enumerate(todo, 1):
                notes: dict[str, object] = {}
                try:
                    rg = await mb.release_group(t["mbid"])
                    if rg["id"] != t["mbid"]:  # merged in MusicBrainz: browse by the new MBID
                        notes["merged_into"] = rg["id"]
                    links = [(k, u, "musicbrainz") for k, u in url_links(rg)]
                    if not any(k == "wikipedia" for k, _, _ in links):
                        for _k, u, _ in [x for x in links if x[0] == "wikidata"][:1]:
                            enwiki = await wd.enwiki_url(u.rstrip("/").rsplit("/", 1)[-1])
                            if enwiki:
                                links.append(("wikipedia", enwiki, "wikidata"))
                    canon = choose_release(
                        await mb.releases_of_group(rg["id"]), rg.get("first-release-date")
                    )
                    claims: list[ClaimSpec] = []
                    if canon is not None:
                        release = await mb.release(canon["id"])
                        artists = []
                        for credit in rg.get("artist-credit") or []:
                            aid = (credit.get("artist") or {}).get("id")
                            if aid and aid not in artists_seen:
                                artists_seen[aid] = await mb.artist(aid)
                            if aid:
                                artists.append(artists_seen[aid])
                        claims += musicbrainz_claims(release, artists)
                        notes["canonical_release"] = canon["id"]
                    else:
                        notes["gap"] = "no release with tracks"
                    discogs_links = [u for k, u, _ in links if k == "discogs"]
                    if discogs_links:
                        try:
                            album_dg = await dg.album(discogs_links)
                            if album_dg:
                                claims += discogs_claims(album_dg)
                        except Exception as exc:  # a Discogs failure leaves MusicBrainz's claims
                            notes["discogs_error"] = str(exc)[:200]
                    else:
                        notes["discogs"] = "no link"
                    for url in (t["source_urls"] or "").split():
                        links.append((link_kind(url), url, f"map:{MAP}"))
                except NotFoundError:
                    notes, claims, links = {"gap": "release group not in MusicBrainz"}, [], []
                except Exception as exc:  # one album's failure leaves it for the next run
                    counts["errors"] += 1
                    log.warning(
                        "graph import album failed",
                        extra={"release_group_id": t["release_group_id"], "error": str(exc)[:200]},
                    )
                    if counts["errors"] >= ERRORS_MAX:
                        raise
                    continue
                async with connection(ctx.pool) as conn, conn.transaction():
                    n = await write_album(
                        conn, t["entity_id"], label_id(t), claims, links, notes, ctx.run_id
                    )
                counts["albums"] += 1
                counts["claims"] += n
                if i % 20 == 0:
                    log.info("graph import progress", extra={"done": i, "of": len(todo)})
                if default and i % 50 == 0:
                    async with connection(ctx.pool) as conn:
                        size = await database_mb(conn)
                    if size >= DB_STOP_MB:
                        counts["stopped_at_size"] = 1
                        break
            counts["musicbrainz_requests"] = mb.requests
            counts["discogs_requests"] = dg.requests
            counts["wikidata_requests"] = wd.requests
        ctx.rows = counts["albums"]
        async with connection(ctx.pool) as conn:
            ctx.notes.update(database_mb_after=await database_mb(conn))
        ctx.notes.update(slice=slice_name, **counts)

    return _run


def graph_link() -> JobFn:
    """Point album and artist entities at the listening tables wherever an MBID matches."""

    async def _run(ctx: RunContext) -> None:
        async with connection(ctx.pool) as conn:
            albums = await conn.execute(
                """UPDATE entity e SET release_group_id = rg.release_group_id
                     FROM release_group rg
                    WHERE e.type = 'album' AND e.mbid = rg.mbid AND e.release_group_id IS NULL
                      AND NOT EXISTS (SELECT 1 FROM entity x
                                       WHERE x.release_group_id = rg.release_group_id)"""
            )
            artists = await conn.execute(
                """UPDATE entity e SET artist_id = a.artist_id
                     FROM artist a
                    WHERE e.type IN ('artist', 'person') AND e.mbid = a.mbid
                      AND e.artist_id IS DISTINCT FROM a.artist_id"""
            )
        ctx.notes.update(
            albums_linked=int(albums.split()[-1]), artists_linked=int(artists.split()[-1])
        )

    return _run
