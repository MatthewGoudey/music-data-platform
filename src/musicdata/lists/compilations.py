"""`musicdata lists reject-compilations --list <slug>`: set aside entries that resolved to a
compilation (QUEUE_SPEC v12).

A compilation can never count as heard (derive/sessions.py `eligible`), so an entry
resolved to one would wait in the queue forever. Its `review_status` becomes `rejected`:
the queue, `/gaps` and L3 skip it, L1 still counts it. Greatest hits are dropped by title
when a list is converted (scripts/convert_billboard.py); this catches the ones MusicBrainz
knows as compilations though their titles do not say so.
"""

from __future__ import annotations

from musicdata.db import connection
from musicdata.jobs.runs import JobFn, RunContext

REJECT = """
UPDATE list_entry e
   SET review_status = 'rejected'
  FROM list l, release_group rg
 WHERE l.list_id = e.list_id AND l.slug = $1
   AND rg.release_group_id = e.release_group_id
   AND e.review_status = 'accepted'
   AND (rg.is_compilation OR 'Compilation' = ANY(rg.secondary_types))
RETURNING e.raw_artist, e.raw_album
"""


def reject_compilations(slug: str) -> JobFn:
    async def _run(ctx: RunContext) -> None:
        async with connection(ctx.pool) as conn:
            rows = await conn.fetch(REJECT, slug)
        ctx.rows = len(rows)
        ctx.notes.update(
            list=slug,
            rejected=len(rows),
            examples=[f"{r['raw_artist']} - {r['raw_album']}" for r in rows[:30]],
        )

    return _run
