"""Graph acceptance checks G1–G8 (GRAPH_SPEC 12), run by `musicdata dq`. Each passes with
"no graph tables" in an environment whose database predates migration 0018."""

from __future__ import annotations

import json

import asyncpg

from musicdata.config import get_settings
from musicdata.graph.fetch import MONTH_SPENT
from musicdata.graph.verify import Lookups, evidence_check, kind, norm

Result = tuple[float | None, bool, dict[str, object]]
NO_GRAPH: Result = (None, True, {"reason": "no graph tables"})
DB_SIZE_MAX_MB = 800
FIRECRAWL_KEY = r"(?<![0-9a-f])fc-[0-9a-f]{32}(?![0-9a-f])"

G1 = """
SELECT a.claim_label FROM assertion a
  JOIN predicate p ON p.name = a.predicate
  JOIN entity s ON s.entity_id = a.subject_id
  LEFT JOIN entity o ON o.entity_id = a.object_id
 WHERE a.status NOT IN ('rejected', 'superseded')
   AND (NOT (s.type = ANY(p.subject_types)
             OR (s.type = 'person' AND 'artist' = ANY(p.subject_types)))
        OR (o.entity_id IS NOT NULL
            AND NOT (o.type = ANY(p.object_types)
                     OR (o.type = 'person' AND 'artist' = ANY(p.object_types))))
        OR (o.entity_id IS NULL AND NOT 'literal' = ANY(p.object_types)))
"""
G3 = """
WITH y AS (
    SELECT e.entity_id, coalesce((e.attrs ->> 'year')::int, rg.first_release_year) AS year
      FROM entity e LEFT JOIN release_group rg USING (release_group_id))
SELECT a.claim_label FROM assertion a
  JOIN predicate p ON p.name = a.predicate AND p.lineage
  JOIN y s ON s.entity_id = a.subject_id
  JOIN y o ON o.entity_id = a.object_id
 WHERE a.status = 'accepted' AND o.year > s.year
"""
G4 = """
SELECT claim_label FROM assertion
 WHERE status = 'accepted' AND extractor = 'claude' AND evidence NOT LIKE 'field:%'
   AND NOT (reader_verdict IS NOT DISTINCT FROM 'SUPPORTS'
            OR (reader_verdict = 'PARTIAL' AND support && ARRAY['musicbrainz', 'discogs']))
"""
G5 = """
SELECT a.claim_label FROM assertion a JOIN graph_batch b USING (batch_id)
 WHERE b.status = 'verified'
   AND a.status NOT IN ('accepted', 'rejected', 'ask_matt', 'superseded')
"""
G6 = f"""
SELECT 'source_fetch' AS tbl, count(*) AS n FROM source_fetch WHERE error ~ '{FIRECRAWL_KEY}'
UNION ALL
SELECT 'assertion', count(*) FROM assertion WHERE evidence ~ '{FIRECRAWL_KEY}'
UNION ALL
SELECT 'pipeline_run', count(*) FROM pipeline_run WHERE notes::text ~ '{FIRECRAWL_KEY}'
"""


async def has_graph(conn: asyncpg.Connection) -> bool:
    return await conn.fetchval("SELECT to_regclass('assertion') IS NOT NULL")


async def _labels(conn: asyncpg.Connection, sql: str) -> Result:
    if not await has_graph(conn):
        return NO_GRAPH
    labels = [r[0] for r in await conn.fetch(sql)]
    return float(len(labels)), not labels, {"claims": labels[:20]}


async def g1_types(conn: asyncpg.Connection) -> Result:
    return await _labels(conn, G1)


async def g2_evidence(conn: asyncpg.Connection) -> Result:
    """Accepted text claims pass the Evidence check again against the stored pages."""
    if not await has_graph(conn):
        return NO_GRAPH
    rows = await conn.fetch(
        """SELECT a.claim_label, a.predicate, a.source, a.extractor, a.basis, a.evidence,
                  a.source_url, s.name AS s_name, o.name AS o_name
             FROM assertion a JOIN entity s ON s.entity_id = a.subject_id
             LEFT JOIN entity o ON o.entity_id = a.object_id
            WHERE a.status = 'accepted' AND a.extractor IN ('claude', 'firecrawl_json')"""
    )
    claims = [
        {
            "claim_id": r["claim_label"],
            "predicate": r["predicate"],
            "source": r["source"],
            "extractor": r["extractor"],
            "basis": r["basis"],
            "evidence": r["evidence"],
            "source_url": r["source_url"],
            "subject": {"name": r["s_name"]},
            "object": {"name": r["o_name"] or ""},
        }
        for r in rows
    ]
    urls = sorted(
        {
            u
            for c in claims
            for u in (c["source_url"] or "", (c["source_url"] or "").split("#")[0])
            if u
        }
    )
    pages = {
        r["url"]: norm(r["body"])
        for r in await conn.fetch(
            """SELECT DISTINCT ON (url) url, body FROM source_fetch
                WHERE ok AND url = ANY($1::text[]) ORDER BY url, fetched_at DESC""",
            urls,
        )
    }
    notes = {
        r["atlas_id"]: json.loads(r["facets"])
        for r in await conn.fetch(
            """SELECT e.facets ->> 'atlas_id' AS atlas_id, e.facets::text AS facets
                 FROM list_entry e JOIN list l USING (list_id) WHERE l.slug = 'v_atlas'"""
        )
    }
    lk = Lookups(pages=pages, notes=notes)
    failing = [c["claim_id"] for c in claims if evidence_check(c, kind(c), lk, {})]
    return float(len(failing)), not failing, {"checked": len(claims), "claims": failing[:20]}


async def g3_direction(conn: asyncpg.Connection) -> Result:
    return await _labels(conn, G3)


async def g4_reader(conn: asyncpg.Connection) -> Result:
    return await _labels(conn, G4)


async def g5_batches_settled(conn: asyncpg.Connection) -> Result:
    return await _labels(conn, G5)


async def g6_no_keys(conn: asyncpg.Connection) -> Result:
    if not await has_graph(conn):
        return NO_GRAPH
    found = {r["tbl"]: r["n"] for r in await conn.fetch(G6) if r["n"]}
    return float(sum(found.values())), not found, {"found": found}


async def g7_database_size(conn: asyncpg.Connection) -> Result:
    mb = await conn.fetchval("SELECT pg_database_size(current_database()) / 1048576.0")
    return round(float(mb), 1), float(mb) < DB_SIZE_MAX_MB, {"limit_mb": DB_SIZE_MAX_MB}


async def g8_credits(conn: asyncpg.Connection) -> Result:
    if not await has_graph(conn):
        return NO_GRAPH
    limit = get_settings().graph_monthly_credits
    spent = int(await conn.fetchval(MONTH_SPENT))
    return float(spent), spent <= limit, {"limit": limit}
