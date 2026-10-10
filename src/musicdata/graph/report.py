"""`musicdata graph report --slice <name> | --batch <label>` (GRAPH_SPEC 7.6).

Coverage per facet counts claims of any status, and accepted claims once verification has
run: an album covers a facet when at least one claim with that album as context falls in it.
The report also gives counts by status and predicate, the first-pass reader SUPPORTS rate per
predicate (`reader_first`; PARTIAL counts against), the reading questions, gaps (failed
fetches, no-match albums, unresolved or ambiguous entities) and Firecrawl credits. With
`--batch`, it also lands in the batch folder as `report.md`.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import asyncpg

from musicdata.db import connection
from musicdata.graph.fetch import MONTH_SPENT
from musicdata.graph.importer import slice_targets
from musicdata.jobs.runs import JobFn, RunContext

FACETS = {
    "credits": ("credited_on",),
    "recording": ("recorded_at",),
    "label": ("released_by",),
    "people": ("member_of", "based_in", "performs_as", "renamed_from", "toured_with"),
    "lineage": (
        "influenced_by",
        "sounds_like",
        "covers",
        "samples",
        "interpolates",
        "associated_with",
    ),
}


async def coverage(
    conn: asyncpg.Connection, slice_name: str, only: set[int] | None = None
) -> dict[str, object]:
    targets = await slice_targets(conn, slice_name)
    if only is not None:
        targets = [t for t in targets if t["entity_id"] in only]
    ids = [t["entity_id"] for t in targets if t["entity_id"] and t["mbid"]]
    rows = await conn.fetch(
        """SELECT album_context, predicate, source, status, count(*) AS n
             FROM assertion
            WHERE album_context = ANY($1::bigint[]) AND status <> 'superseded'
            GROUP BY 1, 2, 3, 4""",
        ids,
    )
    albums = await conn.fetch(
        """SELECT entity_id, baseline_at, notes FROM graph_album
            WHERE entity_id = ANY($1::bigint[])""",
        ids,
    )
    by_album: dict[int, set[str]] = {}
    accepted: dict[int, set[str]] = {}
    predicates: Counter = Counter()
    sources: Counter = Counter()
    statuses: Counter = Counter()
    for r in rows:
        by_album.setdefault(r["album_context"], set()).add(r["predicate"])
        if r["status"] == "accepted":
            accepted.setdefault(r["album_context"], set()).add(r["predicate"])
        predicates[r["predicate"]] += r["n"]
        sources[r["source"]] += r["n"]
        statuses[r["status"]] += r["n"]
    baselined = [a for a in albums if a["baseline_at"]]
    facets = {
        facet: sum(1 for preds in by_album.values() if preds & set(names))
        for facet, names in FACETS.items()
    }
    facets_accepted = {
        facet: sum(1 for preds in accepted.values() if preds & set(names))
        for facet, names in FACETS.items()
    }
    gaps = Counter()
    for a in baselined:
        notes = json.loads(a["notes"]) if isinstance(a["notes"], str) else a["notes"]
        for k in ("gap", "discogs", "discogs_error"):
            if notes.get(k):
                gaps[f"{k}: {notes[k]}"[:80]] += 1
    no_match = [
        f"{t['atlas_id']} {t['raw_artist']} - {t['raw_album']} ({t['resolve_status']}"
        f"{', no MusicBrainz ID' if t['entity_id'] and not t['mbid'] else ''})"
        for t in targets
        if not t["entity_id"] or not t["mbid"]
    ]
    return {
        "slice": slice_name,
        "albums": len(targets),
        "researchable": len(ids),
        "baselined": len(baselined),
        "coverage": facets,
        "coverage_accepted": facets_accepted,
        "claims": sum(predicates.values()),
        "claims_by_status": dict(statuses.most_common()),
        "claims_by_predicate": dict(predicates.most_common()),
        "claims_by_source": dict(sources.most_common()),
        "gaps": dict(gaps.most_common()),
        "no_match": no_match,
    }


async def reading(conn: asyncpg.Connection, ids: list[int]) -> dict[str, object]:
    """Reader rates, reading questions, unresolved names, failed fetches and credits."""
    rates = {
        r["predicate"]: {"claims": r["n"], "supports": r["supports"]}
        for r in await conn.fetch(
            """SELECT predicate, count(*) AS n,
                      count(*) FILTER (WHERE reader_first = 'SUPPORTS') AS supports
                 FROM assertion
                WHERE album_context = ANY($1::bigint[]) AND reader_first IS NOT NULL
                GROUP BY predicate ORDER BY predicate""",
            ids,
        )
    }
    questions = [
        dict(r)
        for r in await conn.fetch(
            """SELECT a.assertion_id, a.claim_label, s.name AS subject, a.predicate,
                      o.name AS object, a.evidence, a.source_url, a.reader_reason
                 FROM assertion a
                 JOIN entity s ON s.entity_id = a.subject_id
                 LEFT JOIN entity o ON o.entity_id = a.object_id
                WHERE a.album_context = ANY($1::bigint[]) AND a.status = 'ask_matt'
                ORDER BY a.claim_label""",
            ids,
        )
    ]
    unresolved = [
        f"{r['type']} {r['name']} ({r['resolve_status']})"
        for r in await conn.fetch(
            """SELECT DISTINCT e.type, e.name, e.resolve_status
                 FROM assertion a JOIN entity e ON e.entity_id IN (a.subject_id, a.object_id)
                WHERE a.album_context = ANY($1::bigint[]) AND a.extractor = 'claude'
                  AND a.status <> 'superseded'
                  AND e.resolve_status IN ('ambiguous', 'unresolved')
                ORDER BY 1, 2""",
            ids,
        )
    ]
    failed = [
        f"{r['url']}: {r['error']}"
        for r in await conn.fetch(
            """SELECT DISTINCT ON (url) url, error FROM source_fetch f
                WHERE entity_id = ANY($1::bigint[]) AND NOT ok
                  AND NOT EXISTS (SELECT 1 FROM source_fetch g WHERE g.url = f.url AND g.ok)
                ORDER BY url, fetched_at DESC""",
            ids,
        )
    ]
    credits = await conn.fetchval(
        "SELECT coalesce(sum(credits), 0) FROM source_fetch WHERE entity_id = ANY($1::bigint[])",
        ids,
    )
    return {
        "reader_first": rates,
        "questions": questions,
        "unresolved": unresolved,
        "failed_fetches": failed,
        "credits": int(credits),
        "month_credits": int(await conn.fetchval(MONTH_SPENT)),
    }


def _pct(n: int, d: int) -> str:
    return f"{100 * n / d:.0f}%" if d else "-"


def markdown(c: dict[str, object]) -> str:
    base = max(int(c["baselined"]), 1)
    title = f"batch {c['batch']}" if c.get("batch") else c["slice"]
    lines = [
        f"# Graph report: {title}",
        "",
        f"{c['albums']} albums; {c['researchable']} with a MusicBrainz ID; "
        f"{c['baselined']} baselined; {c['claims']} claims.",
        "",
        "| Facet | Albums with a claim | Albums with an accepted claim | Share accepted |",
        "| --- | --- | --- | --- |",
    ]
    for facet, n in c["coverage"].items():
        acc = c["coverage_accepted"][facet]
        lines.append(f"| {facet} | {n} | {acc} | {100 * acc / base:.0f}% |")
    lines += [
        "",
        "Claims by status: " + ", ".join(f"{k} {v}" for k, v in c["claims_by_status"].items()),
        "Claims by predicate: "
        + ", ".join(f"{k} {v}" for k, v in c["claims_by_predicate"].items()),
        "Claims by source: " + ", ".join(f"{k} {v}" for k, v in c["claims_by_source"].items()),
    ]
    rates = c.get("reader_first") or {}
    if rates:
        lines += ["", "| Predicate | Read | First-pass SUPPORTS |", "| --- | --- | --- |"]
        for p, r in rates.items():
            lines.append(f"| {p} | {r['claims']} | {_pct(r['supports'], r['claims'])} |")
    questions = c.get("questions") or []
    lines += ["", f"## Reading questions ({len(questions)})"]
    for q in questions:
        lines += [
            "",
            f"**{q['claim_label']}** (`{q['assertion_id']}`): {q['subject']} {q['predicate']} "
            f"{q['object']}?",
            f"> {q['evidence']}",
            f"Source: {q['source_url']}. Reader: {q['reader_reason'] or '-'}",
        ]
    if c["gaps"]:
        lines += ["", "Gaps: " + "; ".join(f"{k} ({v})" for k, v in c["gaps"].items())]
    for key, head in (("failed_fetches", "Failed fetches"), ("unresolved", "Unresolved names")):
        items = c.get(key) or []
        if items:
            lines += ["", f"{head} ({len(items)}):"] + [f"- {x}" for x in items]
    if "credits" in c:
        lines += ["", f"Firecrawl credits: {c['credits']} for these albums; "
                      f"{c['month_credits']} this month."]  # fmt: skip
    lines += ["", f"No match ({len(c['no_match'])}):"] + [f"- {x}" for x in c["no_match"]]
    return "\n".join(lines) + "\n"


def graph_report(
    *, slice_name: str | None = None, batch: str | None = None, root: Path = Path(".")
) -> JobFn:
    async def _run(ctx: RunContext) -> None:
        async with connection(ctx.pool) as conn:
            only = None
            name = slice_name or ""
            if batch is not None:
                row = await conn.fetchrow(
                    "SELECT batch_id, slice FROM graph_batch WHERE label = $1", batch
                )
                if row is None:
                    raise ValueError(f"no batch {batch}")
                name = row["slice"]
                only = {
                    r[0]
                    for r in await conn.fetch(
                        "SELECT entity_id FROM graph_album WHERE batch_id = $1", row["batch_id"]
                    )
                }
            c = await coverage(conn, name, only)
            ids = [t for t in (only or set())] if only is not None else None
            if ids is None:
                ids = [
                    r[0]
                    for r in await conn.fetch(
                        "SELECT entity_id FROM graph_album WHERE $1 = ANY(slices)", name
                    )
                ]
            c.update(await reading(conn, ids), batch=batch)
        text = markdown(c)
        print(text)
        if batch is not None:
            from musicdata.graph.batch import folder

            out = folder(batch, root)
            out.mkdir(parents=True, exist_ok=True)
            (out / "report.md").write_text(text, encoding="utf-8")
        ctx.rows = int(c["baselined"])
        skip = ("no_match", "questions", "unresolved", "failed_fetches", "reader_first")
        ctx.notes.update(
            {k: v for k, v in c.items() if k not in skip},
            no_match=len(c["no_match"]),
            questions=len(c["questions"]),
            unresolved=len(c["unresolved"]),
            failed_fetches=len(c["failed_fetches"]),
            reader_first=c["reader_first"],
        )

    return _run
