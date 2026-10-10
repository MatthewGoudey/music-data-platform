"""Documents on request and the worker's side of the API (docs/graph/COMPANION_SPEC.md 7.6 and 8).

A request makes a `requested` version; the worker lists requests (after a sweep of lapsed leases),
starts one (a 3-hour lease and the graph batch `D<id>`), fetches pages through the API so their
text is Firecrawl's, posts claims and reader verdicts into `D<id>`, and hands in the document. On
`ready` every marker must resolve — `[c:<id>]` to a claim that is accepted or in `D<id>`,
`[p:<id>]` to a cached page — and the resolved map becomes `citations`. A failed or lapsed
attempt returns to `requested` until the second attempt, then stays `failed`.
"""

from __future__ import annotations

import json
import re
import secrets

import asyncpg

from musicdata.config import get_settings
from musicdata.graph.fetch import MONTH_SPENT, is_english_page
from musicdata.graph.queries import DEAD_SITES, album_entity
from musicdata.pages.common import source_label

LEASE = "3 hours"
MAX_ATTEMPTS = 2
KINDS = ("deep_dive",)  # liner notes dropped (companion spec change 2026-10-10)
MARK = re.compile(r"\[(c|p):(\d+)\]")


ROUTINE_HEADERS = {"anthropic-version": "2023-06-01", "Content-Type": "application/json"}


async def fire_routine(text: str, client=None) -> dict:
    """Start the document worker routine at once (companion spec 8.2, on request): POST to its API
    trigger. Returns the run's session URL, or why it did not start; never raises, because the
    daily run picks up anything a failed start leaves requested."""
    import httpx

    settings = get_settings()
    if not settings.routine_fire_url or settings.routine_fire_token is None:
        return {"fired": False, "reason": "no routine trigger configured"}
    headers = ROUTINE_HEADERS | {
        "Authorization": f"Bearer {settings.routine_fire_token.get_secret_value()}"
    }
    try:
        async with client or httpx.AsyncClient(timeout=20) as http:
            r = await http.post(settings.routine_fire_url, headers=headers, json={"text": text})
    except Exception as exc:  # the network, not the request: the daily run is the net
        return {"fired": False, "reason": type(exc).__name__}
    if r.status_code != 200:
        return {"fired": False, "reason": f"routine trigger answered {r.status_code}"}
    return {"fired": True, "session_url": r.json().get("claude_code_session_url")}


class DocumentError(ValueError):
    """A request the document flow refuses; the message says why."""


def batch_label(document_id: int) -> str:
    return f"D{document_id}"


async def request(conn: asyncpg.Connection, release_group_id: int, kind: str) -> dict:
    """8.1: a new requested version, or the open one when a request is already waiting."""
    if kind not in KINDS:
        raise DocumentError(f"the document kind is deep_dive, not {kind}")
    if not await conn.fetchval(
        "SELECT 1 FROM release_group WHERE release_group_id = $1", release_group_id
    ):
        raise LookupError("no such album")
    open_ = await conn.fetchrow(
        """SELECT document_id, version, status FROM album_document
            WHERE release_group_id = $1 AND kind = $2 AND status IN ('requested', 'writing')""",
        release_group_id,
        kind,
    )
    if open_:
        return dict(open_) | {"created": False}
    row = await conn.fetchrow(
        """INSERT INTO album_document (release_group_id, kind, version)
           VALUES ($1, $2, coalesce((SELECT max(version) FROM album_document
                                      WHERE release_group_id = $1 AND kind = $2), 0) + 1)
           RETURNING document_id, version, status""",
        release_group_id,
        kind,
    )
    return dict(row) | {"created": True}


async def sweep(conn: asyncpg.Connection) -> int:
    """Lapsed leases count as failed attempts: back to requested, or failed at the second."""
    done = await conn.execute(
        f"""UPDATE album_document
               SET status = CASE WHEN attempts < {MAX_ATTEMPTS} THEN 'requested' ELSE 'failed' END,
                   lease_token = NULL, lease_until = NULL,
                   error = coalesce(error || '; ', '') || 'lease lapsed'
             WHERE status = 'writing' AND lease_until < now()"""
    )
    return int(done.split()[-1])


async def requested(conn: asyncpg.Connection) -> list[dict]:
    await sweep(conn)
    rows = await conn.fetch(
        """SELECT d.document_id, d.release_group_id, d.kind, d.version, d.requested_at,
                  d.attempts, rg.title, a.name AS artist
             FROM album_document d
             JOIN release_group rg USING (release_group_id)
             JOIN artist a ON a.artist_id = rg.artist_id
            WHERE d.status = 'requested' ORDER BY d.requested_at, d.document_id"""
    )
    return [dict(r) for r in rows]


async def start(conn: asyncpg.Connection, document_id: int) -> dict:
    token = secrets.token_urlsafe(24)
    row = await conn.fetchrow(
        f"""UPDATE album_document
               SET status = 'writing', lease_token = $2, lease_until = now() + interval '{LEASE}',
                   attempts = attempts + 1
             WHERE document_id = $1 AND status = 'requested'
         RETURNING document_id, release_group_id, kind, version, lease_until, attempts""",
        document_id,
        token,
    )
    if row is None:
        raise DocumentError("only a requested document can start")
    await conn.execute(
        "INSERT INTO graph_batch (label, slice) VALUES ($1, 'document') ON CONFLICT (label) DO NOTHING",
        batch_label(document_id),
    )
    return dict(row) | {"lease_token": token, "batch": batch_label(document_id)}


async def _leased(conn: asyncpg.Connection, document_id: int, token: str) -> asyncpg.Record:
    row = await conn.fetchrow(
        """SELECT document_id, release_group_id, kind, attempts, lease_token, lease_until > now() AS live
             FROM album_document WHERE document_id = $1 AND status = 'writing'""",
        document_id,
    )
    if row is None or not secrets.compare_digest(row["lease_token"] or "", token or ""):
        raise DocumentError("no writing document with that lease token")
    if not row["live"]:
        raise DocumentError("the lease has lapsed; start the document again")
    return row


async def renew(conn: asyncpg.Connection, document_id: int, token: str) -> dict:
    await _leased(conn, document_id, token)
    until = await conn.fetchval(
        f"""UPDATE album_document SET lease_until = now() + interval '{LEASE}'
             WHERE document_id = $1 RETURNING lease_until""",
        document_id,
    )
    return {"document_id": document_id, "lease_until": until}


async def get_fetch(conn: asyncpg.Connection, fetch_id: int) -> dict | None:
    r = await conn.fetchrow(
        "SELECT fetch_id, url, title, mode, fetched_at, body FROM source_fetch WHERE fetch_id = $1 AND ok",
        fetch_id,
    )
    return dict(r) if r else None


async def fetch_page(conn: asyncpg.Connection, url: str, mode: str, document_id: int) -> dict:
    """8.3 POST /fetches: the API fetches the page through Firecrawl and caches it under the
    document's batch, within DOCUMENT_CREDITS and the month's ceiling."""
    from musicdata.clients.firecrawl import COST, SCHEMA_VERSION, FirecrawlClient

    settings = get_settings()
    if mode not in COST:
        raise DocumentError(f"mode is one of {sorted(COST)}")
    if not is_english_page(url) or (url.split("/")[2] if "//" in url else "").endswith(DEAD_SITES):
        raise DocumentError("pages are English, and Rate Your Music is not read")
    doc = await conn.fetchrow(
        "SELECT release_group_id FROM album_document WHERE document_id = $1 AND status = 'writing'",
        document_id,
    )
    if doc is None:
        raise DocumentError("fetches belong to a writing document")
    cached = await conn.fetchrow(
        """SELECT fetch_id, url, title, body FROM source_fetch
            WHERE url = $1 AND schema_version = $2 AND ok ORDER BY fetched_at DESC LIMIT 1""",
        url,
        SCHEMA_VERSION[mode],
    )
    if cached:
        return dict(cached) | {"credits": 0, "cached": True}
    label = batch_label(document_id)
    spent = int(await conn.fetchval(
        "SELECT coalesce((counts ->> 'fetch_credits')::int, 0) FROM graph_batch WHERE label = $1",
        label,
    ) or 0)  # fmt: skip
    if spent + COST[mode] > settings.document_credits:
        raise DocumentError(
            f"the document's page budget ({settings.document_credits} credits) is spent"
        )
    if int(await conn.fetchval(MONTH_SPENT)) + COST[mode] > settings.graph_monthly_credits:
        raise DocumentError("this month's Firecrawl ceiling is reached")
    if settings.firecrawl_api_key is None:
        raise DocumentError("FIRECRAWL_API_KEY is not set on this server")
    async with FirecrawlClient(settings.firecrawl_api_key.get_secret_value()) as fc:
        page = await fc.scrape(url, mode)
    entity = await album_entity(conn, doc["release_group_id"])
    fetch_id = await conn.fetchval(
        """INSERT INTO source_fetch (url, mode, schema_version, entity_id, credits, ok, error, title,
                                     body, facts)
           VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10::jsonb) RETURNING fetch_id""",
        url, mode, SCHEMA_VERSION[mode], entity, page.credits, page.ok, page.error, page.title,
        page.body, json.dumps(page.facts) if page.facts is not None else None,
    )  # fmt: skip
    await conn.execute(
        """UPDATE graph_batch
              SET counts = counts || jsonb_build_object('fetch_credits',
                           coalesce((counts ->> 'fetch_credits')::int, 0) + $2::int)
            WHERE label = $1""",
        label,
        page.credits,
    )
    if not page.ok:
        raise DocumentError(f"the page could not be fetched: {page.error}")
    return {"fetch_id": fetch_id, "url": url, "title": page.title, "body": page.body,
            "credits": page.credits, "cached": False}  # fmt: skip


async def citations_for(
    conn: asyncpg.Connection, body_md: str, batch_id: int | None
) -> tuple[dict, list[str]]:
    """Each marker resolved to what the page links, and the markers that do not resolve."""
    claims = sorted({int(i) for k, i in MARK.findall(body_md) if k == "c"})
    pages = sorted({int(i) for k, i in MARK.findall(body_md) if k == "p"})
    out: dict[str, dict] = {}
    for r in await conn.fetch(
        """SELECT a.assertion_id, a.status, a.batch_id, a.source, a.source_url, a.evidence,
                  s.name AS subject, a.predicate, o.name AS object
             FROM assertion a JOIN entity s ON s.entity_id = a.subject_id
             LEFT JOIN entity o ON o.entity_id = a.object_id
            WHERE a.assertion_id = ANY($1::bigint[])""",
        claims,
    ):
        if r["status"] == "accepted" or (batch_id is not None and r["batch_id"] == batch_id
                                         and r["status"] not in ("rejected", "superseded")):  # fmt: skip
            what = f"{r['subject']} · {r['predicate'].replace('_', ' ')} · {r['object'] or ''}"
            out[f"c:{r['assertion_id']}"] = {
                "assertion_id": r["assertion_id"], "url": r["source_url"],
                "label": source_label([r["source"]], r["source_url"]),
                "title": f"{what} — “{(r['evidence'] or '')[:180]}”",
            }  # fmt: skip
    for r in await conn.fetch(
        "SELECT fetch_id, url, title FROM source_fetch WHERE fetch_id = ANY($1::bigint[]) AND ok",
        pages,
    ):
        out[f"p:{r['fetch_id']}"] = {"fetch_id": r["fetch_id"], "url": r["url"],
                                     "label": source_label([], r["url"]), "title": r["title"] or r["url"]}  # fmt: skip
    missing = [f"{k}:{i}" for k, i in MARK.findall(body_md) if f"{k}:{i}" not in out]
    return out, sorted(set(missing))


async def finish(conn: asyncpg.Connection, document_id: int, body: dict) -> dict:
    """8.3 PUT /documents/{id}: ready (every marker resolved) or failed (retry until the second)."""
    doc = await _leased(conn, document_id, str(body.get("lease_token") or ""))
    status = body.get("status")
    if status == "failed":
        new = "requested" if doc["attempts"] < MAX_ATTEMPTS else "failed"
        await conn.execute(
            """UPDATE album_document SET status = $2, lease_token = NULL, lease_until = NULL,
                      error = $3, model = coalesce($4, model)
                WHERE document_id = $1""",
            document_id, new, str(body.get("error") or "failed")[:2000], body.get("model"),
        )  # fmt: skip
        return {"document_id": document_id, "status": new}
    if status != "ready":
        raise DocumentError("status is ready or failed")
    text = str(body.get("body_md") or "")
    if not MARK.search(text):
        raise DocumentError("a document cites its sources with [c:<id>] and [p:<id>] markers")
    batch_id = await conn.fetchval(
        "SELECT batch_id FROM graph_batch WHERE label = $1", batch_label(document_id)
    )
    cites, missing = await citations_for(conn, text, batch_id)
    if missing:
        raise DocumentError(f"markers that do not resolve: {', '.join(missing[:40])}")
    fetch_credits = int(await conn.fetchval(
        "SELECT coalesce((counts ->> 'fetch_credits')::int, 0) FROM graph_batch WHERE batch_id = $1",
        batch_id,
    ) or 0)  # fmt: skip
    async with conn.transaction():
        await conn.execute(
            """UPDATE album_document SET status = 'superseded'
                WHERE release_group_id = $1 AND kind = $2 AND status = 'ready'""",
            doc["release_group_id"],
            doc["kind"],
        )
        await conn.execute(
            """UPDATE album_document
                  SET status = 'ready', finished_at = now(), lease_token = NULL, lease_until = NULL,
                      body_md = $2, citations = $3::jsonb, checks = $4::jsonb, model = $5,
                      firecrawl_credits = $6, error = NULL,
                      claims_through = (SELECT max(assertion_id) FROM assertion WHERE status = 'accepted')
                WHERE document_id = $1""",
            document_id, text, json.dumps(cites), json.dumps(body.get("checks") or {}),
            body.get("model"), int(body.get("firecrawl_credits") or 0) + fetch_credits,
        )  # fmt: skip
    return {"document_id": document_id, "status": "ready", "markers": len(cites)}


OUT_OF_DATE = """
SELECT d.kind,
       EXISTS (SELECT 1 FROM assertion a
                WHERE a.album_context = $2 AND a.status = 'accepted'
                  AND a.asserted_at > d.finished_at
                  AND a.batch_id IS DISTINCT FROM (SELECT batch_id FROM graph_batch
                                                    WHERE label = 'D' || d.document_id))
       OR EXISTS (SELECT 1 FROM jsonb_each(d.citations) c
                    JOIN assertion a ON a.assertion_id = (c.value ->> 'assertion_id')::bigint
                   WHERE a.status IN ('rejected', 'superseded')) AS stale
  FROM album_document d
 WHERE d.release_group_id = $1 AND d.status = 'ready'
"""


async def document_states(conn: asyncpg.Connection, release_group_id: int) -> dict[str, dict]:
    """Each kind's state for the album page's tabs: none, requested, writing, ready, out of date,
    or failed (7.6, 8.1)."""
    if not await conn.fetchval("SELECT to_regclass('album_document') IS NOT NULL"):
        return {}
    rows = await conn.fetch(
        """SELECT DISTINCT ON (kind) kind, status, version, error FROM album_document
            WHERE release_group_id = $1 AND status <> 'superseded'
            ORDER BY kind, CASE status WHEN 'writing' THEN 0 WHEN 'requested' THEN 1
                                       WHEN 'ready' THEN 2 ELSE 3 END, version DESC""",
        release_group_id,
    )
    out = {r["kind"]: {"status": r["status"], "version": r["version"]} for r in rows}
    ready = {k for k, v in out.items() if v["status"] == "ready"}
    if ready:
        entity = await album_entity(conn, release_group_id)
        for r in await conn.fetch(OUT_OF_DATE, release_group_id, entity):
            if r["kind"] in ready and r["stale"]:
                out[r["kind"]]["status"] = "out_of_date"
    return out
