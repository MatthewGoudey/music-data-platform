"""`musicdata graph report --slice <name>`: coverage per facet (GRAPH_SPEC 7.6).

Until Block D verifies claims, coverage counts claims of any status: an album covers a facet
when at least one claim with that album as context falls in the facet. The report also lists
the slice's albums with no match (no resolved entry or no MusicBrainz ID) and the baseline's
gaps. Block D extends it with statuses, reader rates, reading questions and credits.
"""

from __future__ import annotations

import json
from collections import Counter

import asyncpg

from musicdata.db import connection
from musicdata.graph.importer import slice_targets
from musicdata.jobs.runs import JobFn, RunContext

FACETS = {
    "credits": ("credited_on",),
    "recording": ("recorded_at",),
    "label": ("released_by",),
    "people": ("member_of", "based_in"),
    "lineage": ("influenced_by", "sounds_like", "covers", "samples", "associated_with"),
}


async def coverage(conn: asyncpg.Connection, slice_name: str) -> dict[str, object]:
    targets = await slice_targets(conn, slice_name)
    ids = [t["entity_id"] for t in targets if t["entity_id"] and t["mbid"]]
    rows = await conn.fetch(
        """SELECT album_context, predicate, source, count(*) AS n
             FROM assertion WHERE album_context = ANY($1::bigint[])
            GROUP BY 1, 2, 3""",
        ids,
    )
    albums = await conn.fetch(
        """SELECT entity_id, baseline_at, notes FROM graph_album
            WHERE entity_id = ANY($1::bigint[])""",
        ids,
    )
    by_album: dict[int, set[str]] = {}
    predicates: Counter = Counter()
    sources: Counter = Counter()
    for r in rows:
        by_album.setdefault(r["album_context"], set()).add(r["predicate"])
        predicates[r["predicate"]] += r["n"]
        sources[r["source"]] += r["n"]
    baselined = [a for a in albums if a["baseline_at"]]
    facets = {
        facet: sum(1 for preds in by_album.values() if preds & set(names))
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
        "claims": sum(predicates.values()),
        "claims_by_predicate": dict(predicates.most_common()),
        "claims_by_source": dict(sources.most_common()),
        "gaps": dict(gaps.most_common()),
        "no_match": no_match,
    }


def markdown(c: dict[str, object]) -> str:
    base = max(int(c["baselined"]), 1)
    lines = [
        f"# Graph coverage: {c['slice']}",
        "",
        f"{c['albums']} albums; {c['researchable']} with a MusicBrainz ID; "
        f"{c['baselined']} baselined; {c['claims']} claims (any status).",
        "",
        "| Facet | Albums with a claim | Share of baselined |",
        "| --- | --- | --- |",
    ]
    for facet, n in c["coverage"].items():
        lines.append(f"| {facet} | {n} | {100 * n / base:.0f}% |")
    lines += [
        "",
        "Claims by predicate: "
        + ", ".join(f"{k} {v}" for k, v in c["claims_by_predicate"].items()),
    ]
    lines += [
        "Claims by source: " + ", ".join(f"{k} {v}" for k, v in c["claims_by_source"].items())
    ]
    if c["gaps"]:
        lines += ["", "Gaps: " + "; ".join(f"{k} ({v})" for k, v in c["gaps"].items())]
    lines += ["", f"No match ({len(c['no_match'])}):"] + [f"- {x}" for x in c["no_match"]]
    return "\n".join(lines) + "\n"


def graph_report(*, slice_name: str) -> JobFn:
    async def _run(ctx: RunContext) -> None:
        async with connection(ctx.pool) as conn:
            c = await coverage(conn, slice_name)
        print(markdown(c))
        ctx.rows = int(c["baselined"])
        ctx.notes.update(
            {k: v for k, v in c.items() if k != "no_match"}, no_match=len(c["no_match"])
        )

    return _run
