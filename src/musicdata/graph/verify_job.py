"""`musicdata graph verify --batch <label> | --slice <name>` (GRAPH_SPEC 8): the rules in
`graph/verify.py` applied to stored claims.

The job reads the claims of the batch's (or slice's) albums with their entities, gathers what
the checks look up (page bodies from `source_fetch`, atlas notes, entity years, work
recordings, band memberships, credits of other albums), and writes each claim's checks,
fails, support, confidence and status. Lookups that need MusicBrainz or Discogs are free and
cached in `entity.attrs`, so a second run makes no requests. Claims by `matt` keep their status.
"""

from __future__ import annotations

import json
from collections import Counter

import asyncpg

from musicdata.clients.discogs import DiscogsClient, clean_name
from musicdata.clients.musicbrainz import MusicBrainzClient, NotFoundError
from musicdata.config import get_settings
from musicdata.db import connection
from musicdata.graph import names
from musicdata.graph.baseline import url_links
from musicdata.graph.importer import slice_targets
from musicdata.graph.merge import candidates, merge, resolve_posted_names
from musicdata.graph.verify import DB, LINEAGE, Lookups, kind, match_key, norm, verify
from musicdata.jobs.runs import JobFn, RunContext
from musicdata.log import get_logger
from musicdata.resolve.canonical import choose_release

log = get_logger(__name__)

CLAIMS = """
SELECT a.assertion_id, a.claim_label, a.predicate, a.qualifiers::text AS qualifiers, a.source,
       a.extractor, a.basis, a.direction, a.evidence, a.source_url, a.status, a.fails,
       a.reader_verdict, a.reader_reason, a.subject_id, a.object_id, a.object_value,
       a.album_context, m.coords ->> 'atlas_id' AS album
  FROM assertion a
  LEFT JOIN map_membership m ON m.entity_id = a.album_context AND m.map = 'v_atlas'
 WHERE a.album_context = ANY($1::bigint[]) AND a.status <> 'superseded'
 ORDER BY a.assertion_id
"""
ENTITIES = """
SELECT e.entity_id, e.type, e.name, e.mbid::text AS mbid, e.attrs::text AS attrs,
       rg.first_release_year, m.coords ->> 'atlas_id' AS atlas_id
  FROM entity e
  LEFT JOIN release_group rg USING (release_group_id)
  LEFT JOIN map_membership m ON m.entity_id = e.entity_id AND m.map = 'v_atlas'
 WHERE e.entity_id = ANY($1::bigint[])
"""


def _entity(r: asyncpg.Record) -> dict:
    attrs = json.loads(r["attrs"]) if r["attrs"] else {}
    e = {
        "id": r["entity_id"],
        "type": r["type"],
        "name": r["name"],
        "mbid": r["mbid"],
        "attrs": attrs,
        "year": attrs.get("year") or r["first_release_year"],
    }
    for k in ("artist", "album"):
        if attrs.get(k):
            e[k] = attrs[k]
    if r["atlas_id"]:
        e["atlas_id"] = r["atlas_id"]
    aliases = [m["entity"]["name"] for m in attrs.get("merged") or []]
    if aliases:
        e["aliases"] = aliases
    return e


async def album_ids(
    conn: asyncpg.Connection, *, batch_id: int | None = None, slice_name: str | None = None
) -> list[int]:
    if batch_id is not None:
        return [
            r[0]
            for r in await conn.fetch(
                "SELECT entity_id FROM graph_album WHERE batch_id = $1", batch_id
            )
        ]
    targets = await slice_targets(conn, slice_name or "")
    return [t["entity_id"] for t in targets if t["entity_id"]]


PENDING_ALBUMS = """
SELECT DISTINCT album_context FROM assertion
 WHERE status IN ('proposed', 'unread') AND album_context IS NOT NULL
"""
PENDING_LOOSE = """
SELECT a.assertion_id, a.claim_label, a.predicate, a.qualifiers::text AS qualifiers, a.source,
       a.extractor, a.basis, a.direction, a.evidence, a.source_url, a.status, a.fails,
       a.reader_verdict, a.reader_reason, a.subject_id, a.object_id, a.object_value,
       a.album_context, NULL::text AS album
  FROM assertion a
 WHERE a.album_context IS NULL AND a.status IN ('proposed', 'unread')
 ORDER BY a.assertion_id
"""


async def load_claims(
    conn: asyncpg.Connection,
    *,
    batch_id: int | None = None,
    slice_name: str | None = None,
    pending: bool = False,
    also_albums: list[int] | None = None,
) -> list[dict]:
    """The stored claims of a batch's or slice's albums in the shape `verify()` reads; with
    `pending`, every claim of the albums that have a proposed or unread claim, plus the
    proposed or unread claims with no album."""
    if pending:
        ids = sorted({r[0] for r in await conn.fetch(PENDING_ALBUMS)} | set(also_albums or []))
        rows = list(await conn.fetch(CLAIMS, ids)) + list(await conn.fetch(PENDING_LOOSE))
    else:
        ids = await album_ids(conn, batch_id=batch_id, slice_name=slice_name)
        rows = await conn.fetch(CLAIMS, ids)
    ent_ids = {r["subject_id"] for r in rows} | {r["object_id"] for r in rows if r["object_id"]}
    ents = {r["entity_id"]: _entity(r) for r in await conn.fetch(ENTITIES, list(ent_ids))}
    claims = []
    for r in rows:
        obj = (
            ents[r["object_id"]]
            if r["object_id"]
            else {"type": "literal", "name": r["object_value"]}
        )
        claims.append(
            {
                "assertion_id": r["assertion_id"],
                "claim_id": r["claim_label"],
                "subject": ents[r["subject_id"]],
                "predicate": r["predicate"],
                "object": obj,
                "qualifiers": json.loads(r["qualifiers"]) if r["qualifiers"] else {},
                "source": r["source"],
                "extractor": r["extractor"],
                "basis": r["basis"],
                "direction": r["direction"],
                "evidence": r["evidence"],
                "source_url": r["source_url"],
                "status": r["status"],
                "fails": list(r["fails"] or []),
                "reader": (
                    {"verdict": r["reader_verdict"], "reason": r["reader_reason"]}
                    if r["reader_verdict"]
                    else None
                ),
                "album": r["album"] or "",
                "album_context": r["album_context"],
            }
        )
    return claims


async def _save_attrs(conn: asyncpg.Connection, e: dict, extra: dict) -> None:
    e["attrs"].update(extra)
    await conn.execute(
        "UPDATE entity SET attrs = attrs || $2::jsonb WHERE entity_id = $1",
        e["id"],
        json.dumps(extra, default=str),
    )


async def _fill_years(conn: asyncpg.Connection, mb: MusicBrainzClient, claims: list[dict]) -> None:
    for c in claims:
        if c["predicate"] not in LINEAGE:
            continue
        for e in (c["subject"], c["object"]):
            if e.get("year") or not e.get("mbid") or "year_checked" in e.get("attrs", {}):
                continue
            try:
                if e["type"] in ("artist", "person"):
                    a = await mb.artist(e["mbid"])
                    y = names.year((a.get("life-span") or {}).get("begin"))
                elif e["type"] == "album":
                    y = names.year((await mb.release_group(e["mbid"])).get("first-release-date"))
                else:
                    continue
            except NotFoundError:
                y = None
            e["year"] = y
            await _save_attrs(conn, e, {"year": y, "year_checked": True})


async def _artists(conn: asyncpg.Connection, mb: MusicBrainzClient, claims: list[dict]) -> dict:
    out: dict[str, dict] = {}
    for c in claims:
        if c["predicate"] != "member_of" or c["source"] in DB:
            continue
        for e in (c["subject"], c["object"]):
            if not e.get("mbid"):
                continue
            if "relations" not in e["attrs"]:
                try:
                    rels = names.relations(await mb.artist(e["mbid"]))
                except NotFoundError:
                    rels = []
                await _save_attrs(conn, e, {"relations": rels})
            out[match_key(e["name"])] = {"name": e["name"], "relations": e["attrs"]["relations"]}
    return out


async def _foreign(
    conn: asyncpg.Connection, mb: MusicBrainzClient, dg: DiscogsClient, claims: list[dict]
) -> dict:
    """Who MusicBrainz and Discogs credit on albums named by `credited_on` claims that are
    not targets of the research (a target's own baseline is already a claim)."""
    out: dict[str, dict] = {}
    for c in claims:
        o = c["object"]
        if (
            c["predicate"] != "credited_on"
            or c["source"] in DB
            or o["type"] != "album"
            or o.get("atlas_id")
            or not o.get("mbid")
        ):
            continue
        if "credits" not in o["attrs"]:
            credits: dict[str, object] = {"status": "found"}
            try:
                rg = await mb.release_group(o["mbid"])
                canon = choose_release(
                    await mb.releases_of_group(rg["id"]), rg.get("first-release-date")
                )
                credits["musicbrainz"] = (
                    sorted(names.release_credit_names(await mb.release(canon["id"])))
                    if canon
                    else []
                )
                discogs = [u for k, u in url_links(rg) if k == "discogs"]
                album_dg = await dg.album(discogs) if discogs else None
                credits["discogs"] = sorted(
                    {
                        match_key(clean_name(x["name"]))
                        for x in (album_dg or {}).get("credits") or []
                    }
                )
            except NotFoundError:
                credits = {"status": "not in MusicBrainz"}
            await _save_attrs(conn, o, {"credits": credits})
        cr = o["attrs"]["credits"]
        out[o["name"]] = {
            "status": cr.get("status"),
            "musicbrainz": set(cr.get("musicbrainz") or []),
            "discogs": set(cr.get("discogs") or []),
        }
    return out


async def lookups(
    conn: asyncpg.Connection, claims: list[dict], mb: MusicBrainzClient, dg: DiscogsClient
) -> Lookups:
    urls = {
        u
        for c in claims
        if kind(c) in ("text", "firecrawl_json") or c["evidence"].startswith("field:")
        for u in ((c["source_url"] or "").split("#")[0], c["source_url"] or "")
        if u
    }
    pages = {
        r["url"]: norm(r["body"])
        for r in await conn.fetch(
            """SELECT DISTINCT ON (url) url, body FROM source_fetch
                WHERE ok AND url = ANY($1::text[]) ORDER BY url, fetched_at DESC""",
            sorted(urls),
        )
    }
    atlas_ids = sorted({c["album"] for c in claims if c["album"]})
    notes = {
        r["atlas_id"]: json.loads(r["facets"])
        for r in await conn.fetch(
            """SELECT e.facets ->> 'atlas_id' AS atlas_id, e.facets::text AS facets
                 FROM list_entry e JOIN list l USING (list_id)
                WHERE l.slug = 'v_atlas' AND e.facets ->> 'atlas_id' = ANY($1::text[])""",
            atlas_ids,
        )
    }
    work_ids = sorted(
        {c["qualifiers"]["work_mbid"] for c in claims if c["qualifiers"].get("work_mbid")}
    )
    works = {}
    for r in await conn.fetch(
        "SELECT attrs::text AS attrs FROM entity WHERE type = 'work' AND mbid::text = ANY($1)",
        work_ids,
    ):
        w = json.loads(r["attrs"])
        if w.get("sample") is not None:
            works[match_key((w.get("title") or "").split("(")[0])] = w
    await _fill_years(conn, mb, claims)
    return Lookups(
        pages=pages,
        notes=notes,
        works=works,
        artists=await _artists(conn, mb, claims),
        foreign=await _foreign(conn, mb, dg, claims),
        reader={c["claim_id"]: c["reader"] for c in claims if c["reader"]},
    )


async def write_results(conn: asyncpg.Connection, claims: list[dict]) -> Counter:
    statuses: Counter = Counter()
    rows = []
    for c in claims:
        if c["extractor"] == "matt":
            statuses[c["status"]] += 1
            continue
        statuses[c["suggested_status"]] += 1
        rows.append(
            (
                c["assertion_id"],
                c["suggested_status"],
                c["confidence"],
                json.dumps(c["checks"], default=str),
                c["fails"],
                c["independent_support"],
            )
        )
    await conn.executemany(
        """UPDATE assertion SET status = $2, confidence = $3, checks = $4::jsonb, fails = $5,
                  support = $6, updated_at = now()
            WHERE assertion_id = $1""",
        rows,
    )
    return statuses


def graph_verify(*, batch: str | None = None, slice_name: str | None = None) -> JobFn:
    if (batch is None) == (slice_name is None):
        raise ValueError("verify one batch or one slice")

    async def _run(ctx: RunContext) -> None:
        settings = get_settings()
        token = settings.discogs_token.get_secret_value() if settings.discogs_token else None
        ua = settings.musicbrainz_user_agent
        async with (
            connection(ctx.pool) as conn,
            MusicBrainzClient(user_agent=ua) as mb,
            DiscogsClient(user_agent=ua, token=token) as dg,
        ):
            batch_id = None
            if batch is not None:
                batch_id = await conn.fetchval(
                    "SELECT batch_id FROM graph_batch WHERE label = $1", batch
                )
                if batch_id is None:
                    raise ValueError(f"no batch {batch}")
            claims = await load_claims(conn, batch_id=batch_id, slice_name=slice_name)
            lk = await lookups(conn, claims, mb, dg)
            verify(claims, lk)
            async with conn.transaction():
                statuses = await write_results(conn, claims)
                ids = sorted({c["album_context"] for c in claims})
                await conn.execute(
                    "UPDATE graph_album SET verified_at = now() WHERE entity_id = ANY($1::bigint[])",
                    ids,
                )
                if batch_id is not None:
                    done = statuses.get("unread", 0) == 0 and statuses.get("proposed", 0) == 0
                    await conn.execute(
                        """UPDATE graph_batch
                              SET status = CASE WHEN $2 THEN 'verified' ELSE status END,
                                  counts = counts || $3::jsonb
                            WHERE batch_id = $1""",
                        batch_id,
                        done,
                        json.dumps({"statuses": dict(statuses)}),
                    )
            ctx.notes.update(musicbrainz_requests=mb.requests, discogs_requests=dg.requests)
        ctx.rows = len(claims)
        ctx.notes.update(batch=batch, slice=slice_name, claims=len(claims), **dict(statuses))
        log.info("graph verify", extra={"statuses": dict(statuses)})

    return _run


def graph_verify_pending(*, name_limit: int = 200) -> JobFn:
    """Companion spec 3.4: resolve names posted as written, merge duplicates by the graph spec
    v10 rule (logged, undoable), then verify every proposed or unread claim in any batch or
    none, so imported and posted claims reach `accepted` and the `edge` view."""

    async def _run(ctx: RunContext) -> None:
        settings = get_settings()
        token = settings.discogs_token.get_secret_value() if settings.discogs_token else None
        ua = settings.musicbrainz_user_agent
        async with (
            connection(ctx.pool) as conn,
            MusicBrainzClient(user_agent=ua) as mb,
            DiscogsClient(user_agent=ua, token=token) as dg,
        ):
            named = await resolve_posted_names(conn, mb, name_limit)
            merges, apart = await candidates(conn)
            merged, touched = [], set()
            for m in merges:
                entry = await merge(conn, m["loose"], m["firm"], ctx.run_id)
                if entry:
                    claim_ids = sorted(set(entry["subject_of"]) | set(entry["object_of"]))
                    touched |= {
                        r[0]
                        for r in await conn.fetch(
                            """SELECT DISTINCT album_context FROM assertion
                                WHERE assertion_id = ANY($1::bigint[]) AND album_context IS NOT NULL""",
                            claim_ids,
                        )
                    }
                    merged.append(
                        {"kept": m["firm"], "merged": m["loose"], "name": m["firm_name"],
                         "claims": len(set(entry["subject_of"]) | set(entry["object_of"]))}
                    )  # fmt: skip
            claims = await load_claims(conn, pending=True, also_albums=sorted(touched))
            lk = await lookups(conn, claims, mb, dg)
            verify(claims, lk)
            async with conn.transaction():
                statuses = await write_results(conn, claims)
                ids = sorted({c["album_context"] for c in claims if c["album_context"]})
                await conn.execute(
                    "UPDATE graph_album SET verified_at = now() WHERE entity_id = ANY($1::bigint[])",
                    ids,
                )
                await conn.execute(
                    """UPDATE graph_batch b SET status = 'verified'
                        WHERE status <> 'verified' AND NOT EXISTS (
                              SELECT 1 FROM assertion a WHERE a.batch_id = b.batch_id
                                 AND a.status IN ('proposed', 'unread'))
                          AND EXISTS (SELECT 1 FROM assertion a WHERE a.batch_id = b.batch_id)"""
                )
            ctx.notes.update(musicbrainz_requests=mb.requests, discogs_requests=dg.requests)
        ctx.rows = len(claims)
        ctx.notes.update(
            names=named, merged=merged, left_apart=len(apart),
            left_apart_examples=[f"{a['loose_name']} ~ {a['firm_name']}: {a['reason']}"
                                 for a in apart[:30]],
            claims=len(claims), **dict(statuses),
        )  # fmt: skip
        log.info(
            "graph verify --pending", extra={"statuses": dict(statuses), "merged": len(merged)}
        )

    return _run
