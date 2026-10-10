"""`musicdata graph facts` (GRAPH_SPEC 7.3): claims from each Firecrawl `facts` fetch.

A port of the Firecrawl half of `scripts/graph/facts_to_claims.py`: one `credited_on` per
person (roles merged from producers, engineers and mixers, and musicians) and one
`recorded_at` per location. Source `wikipedia`, extractor `firecrawl_json`, basis
`documented`, confidence 0.75, `fetch_id` set; the name-on-page check runs in `graph verify`.
`graph_album.facts_at` is set for every album the step sees, including albums with no page.
"""

from __future__ import annotations

import json
from collections import defaultdict

from musicdata.db import connection
from musicdata.graph.baseline import ALBUM, ClaimSpec, Ent, musical_roles
from musicdata.graph.claims import claim_key
from musicdata.graph.importer import EVIDENCE_MAX, ensure_entity, label_id, slice_targets
from musicdata.jobs.runs import JobFn, RunContext

CONFIDENCE = 0.75


def facts_claims(facts: dict, url: str) -> list[ClaimSpec]:
    people: dict[str, set[str]] = {}
    for p in facts.get("producers") or []:
        if p:
            people.setdefault(p, set()).add("producer")
    for e in facts.get("engineers_and_mixers") or []:
        if e.get("name"):
            people.setdefault(e["name"], set()).update(
                x.strip() for x in (e.get("role") or "").split(",") if x.strip()
            )
    for m in facts.get("musicians") or []:
        if m.get("name"):
            people.setdefault(m["name"], set()).update(m.get("roles") or [])
    out = []
    for name, roles in people.items():
        r = musical_roles(roles)
        if not r:  # cover art, layout and the like are not work on the music (spec v5)
            continue
        out.append(
            ClaimSpec(
                Ent("person", name), "credited_on", ALBUM, {"role": r}, "wikipedia",
                f"Wikipedia personnel: {name} — {', '.join(r)}", url,
            )
        )  # fmt: skip
    for loc in facts.get("recording_locations") or []:
        place = loc.get("studio_or_place")
        if not place:
            continue
        out.append(
            ClaimSpec(
                ALBUM, "recorded_at", Ent("place", place),
                {"dates": facts.get("recording_dates") or None, "city": loc.get("city")},
                "wikipedia", f"Wikipedia: recorded at {place}, {loc.get('city')}", url,
            )
        )  # fmt: skip
    return out


def graph_facts(*, slice_name: str, refresh: bool = False) -> JobFn:
    async def _run(ctx: RunContext) -> None:
        counts: dict[str, int] = defaultdict(int)
        async with connection(ctx.pool) as conn:
            targets = await slice_targets(conn, slice_name)
            ready = {
                r["entity_id"]
                for r in await conn.fetch(
                    """SELECT entity_id FROM graph_album
                        WHERE fetched_at IS NOT NULL AND ($1 OR facts_at IS NULL)""",
                    refresh,
                )
            }
        for t in targets:
            if t["entity_id"] not in ready:
                continue
            async with connection(ctx.pool) as conn, conn.transaction():
                fetches = await conn.fetch(
                    """SELECT fetch_id, url, facts FROM source_fetch
                        WHERE entity_id = $1 AND ok AND mode = 'facts' AND facts IS NOT NULL""",
                    t["entity_id"],
                )
                cache: dict = {}
                n = 0
                for f in fetches:
                    facts = json.loads(f["facts"]) if isinstance(f["facts"], str) else f["facts"]
                    for c in facts_claims(facts or {}, f["url"]):
                        n += 1
                        s = (
                            t["entity_id"]
                            if c.subject is ALBUM
                            else await ensure_entity(conn, c.subject, cache)
                        )
                        o = (
                            t["entity_id"]
                            if c.obj is ALBUM
                            else await ensure_entity(conn, c.obj, cache)
                        )
                        evidence = c.evidence[:EVIDENCE_MAX]
                        await conn.execute(
                            """INSERT INTO assertion (claim_key, claim_label, subject_id, predicate,
                                       object_id, qualifiers, source, extractor, basis, evidence,
                                       source_url, fetch_id, album_context, status, confidence,
                                       asserted_by, pipeline_run_id)
                               VALUES ($1, $2, $3, $4, $5, $6::jsonb, 'wikipedia', 'firecrawl_json',
                                       'documented', $7, $8, $9, $10, 'proposed', $11,
                                       'graph facts', $12)
                               ON CONFLICT DO NOTHING""",
                            claim_key(s, c.predicate, o, c.source, c.source_url, evidence),
                            f"R{ctx.run_id}-{label_id(t)}-W{n:03d}",
                            s,
                            c.predicate,
                            o,
                            json.dumps(c.qualifiers, default=str),
                            evidence,
                            c.source_url,
                            f["fetch_id"],
                            t["entity_id"],
                            CONFIDENCE,
                            ctx.run_id,
                        )
                counts["claims"] += n
                counts["pages"] += len(fetches)
                await conn.execute(
                    "UPDATE graph_album SET facts_at = now() WHERE entity_id = $1", t["entity_id"]
                )
            counts["albums"] += 1
        ctx.rows = counts["albums"]
        ctx.notes.update(slice=slice_name, **counts)

    return _run
