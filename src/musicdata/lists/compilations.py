"""`musicdata lists restore-compilations --list <slug>`: take back entries set aside for resolving to
a compilation (QUEUE_SPEC v24, undoing v12).

Until 2026-10-17 a compilation could never count as heard, so `lists reject-compilations` set such
entries to `review_status = 'rejected'`. Matt's rule since: a sitting through 80% of a compilation is
listening to it (derive/runs.py), so a compilation on a list can be heard and belongs in the queue.
This returns those entries to `accepted`; an entry rejected for any other reason stays.
"""

from __future__ import annotations

from musicdata.db import connection
from musicdata.jobs.runs import JobFn, RunContext

RESTORE = """
UPDATE list_entry e
   SET review_status = 'accepted'
  FROM list l, release_group rg
 WHERE l.list_id = e.list_id AND l.slug = $1
   AND rg.release_group_id = e.release_group_id
   AND e.review_status = 'rejected'
   AND (rg.is_compilation OR 'Compilation' = ANY(rg.secondary_types))
RETURNING e.raw_artist, e.raw_album
"""


def restore_compilations(slug: str) -> JobFn:
    async def _run(ctx: RunContext) -> None:
        async with connection(ctx.pool) as conn:
            rows = await conn.fetch(RESTORE, slug)
        ctx.rows = len(rows)
        ctx.notes.update(
            list=slug,
            restored=len(rows),
            examples=[f"{r['raw_artist']} - {r['raw_album']}" for r in rows[:30]],
        )

    return _run
