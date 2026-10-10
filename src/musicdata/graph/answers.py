"""Matt's answers to reading questions (GRAPH_SPEC 8): `musicdata graph answer`, and later
`POST /graph/questions/{assertion_id}/answer`.

yes and no each write a `matt` claim on the same edge with the same quote (source and extractor
`matt`, status accepted or rejected) and mark the question `superseded`; skip leaves it open.
An answer can narrow the claim with qualifiers ("yes, but only the UK release").
"""

from __future__ import annotations

import json

import asyncpg

from musicdata.db import connection
from musicdata.graph.claims import claim_key
from musicdata.jobs.runs import JobFn, RunContext

ANSWERS = ("yes", "no", "skip")


async def answer(
    conn: asyncpg.Connection, label: str, reply: str, qualifiers: dict | None = None
) -> int | None:
    """Record one answer; returns the new `matt` claim's id (None for skip)."""
    if reply not in ANSWERS:
        raise ValueError(f"answer is yes, no or skip, not {reply!r}")
    q = await conn.fetchrow(
        """SELECT assertion_id, subject_id, predicate, object_id, object_value,
                  qualifiers::text AS qualifiers, evidence, source_url, album_context, batch_id
             FROM assertion WHERE claim_label = $1 AND status = 'ask_matt'""",
        label,
    )
    if q is None:
        raise ValueError(f"{label} is not an open reading question")
    if reply == "skip":
        return None
    quals = {**json.loads(q["qualifiers"] or "{}"), **(qualifiers or {})}
    accepted = reply == "yes"
    obj = q["object_id"] if q["object_id"] is not None else q["object_value"]
    async with conn.transaction():
        new = await conn.fetchval(
            """INSERT INTO assertion (claim_key, claim_label, replaces, subject_id, predicate,
                      object_id, object_value, qualifiers, source, extractor, basis, evidence,
                      source_url, album_context, batch_id, status, confidence, asserted_by)
               VALUES ($1, $2, $3, $4, $5, $6, $7, $8::jsonb, 'matt', 'matt', 'documented', $9,
                       $10, $11, $12, $13, $14, 'matt')
               RETURNING assertion_id""",
            claim_key(q["subject_id"], q["predicate"], obj, "matt", q["source_url"], q["evidence"]),
            f"{label}-M",
            q["assertion_id"],
            q["subject_id"],
            q["predicate"],
            q["object_id"],
            q["object_value"],
            json.dumps(quals, default=str),
            q["evidence"],
            q["source_url"],
            q["album_context"],
            q["batch_id"],
            "accepted" if accepted else "rejected",
            1.0 if accepted else 0.0,
        )
        await conn.execute(
            """UPDATE assertion SET status = 'superseded', updated_at = now()
                WHERE assertion_id = $1""",
            q["assertion_id"],
        )
    return new


def graph_answer(*, replies: list[tuple[str, str, dict | None]]) -> JobFn:
    """`replies`: (claim label, yes|no|skip, qualifiers or None)."""

    async def _run(ctx: RunContext) -> None:
        counts = {a: 0 for a in ANSWERS}
        async with connection(ctx.pool) as conn:
            for label, reply, quals in replies:
                await answer(conn, label, reply, quals)
                counts[reply] += 1
        ctx.rows = counts["yes"] + counts["no"]
        ctx.notes.update(**counts)

    return _run
