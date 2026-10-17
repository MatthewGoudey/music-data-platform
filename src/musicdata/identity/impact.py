"""`musicdata identity impact`: every stored key the working tree's rules would change.

Run it before committing a change to `normalize.py`: the stored keys were written by the rules
in force, so any difference is what a rekey (`full-load -f mode=repair`) would do. It reads
release group titles (album_key), reported release names (album_key) and track names
(title_key), and prints each change with its listen count. A key rule change once moved
`title_key` with the edition rule and the rekey stored 81 duplicates; this shows that first.
"""

from __future__ import annotations

from musicdata.db import connection
from musicdata.identity import album_key, title_key
from musicdata.jobs.runs import JobFn, RunContext

SHOW = 40


def identity_impact() -> JobFn:
    async def _run(ctx: RunContext) -> None:
        async with connection(ctx.pool) as conn:
            groups = await conn.fetch(
                "SELECT release_group_id, title, norm_key, mbid IS NOT NULL AS mapped "
                "FROM release_group"
            )
            reported = await conn.fetch(
                """SELECT release_name, reported_key, count(*) AS n FROM listen
                    WHERE release_name IS NOT NULL GROUP BY 1, 2"""
            )
            titles = await conn.fetch(
                "SELECT track_name, norm_title, count(*) AS n FROM listen GROUP BY 1, 2"
            )
        changes = {
            "release_group_keys": [
                (
                    1,
                    f"{g['title']!r}: {g['norm_key']} -> {k}"
                    + ("" if g["mapped"] else " (unmapped)"),
                )
                for g in groups
                if (k := album_key(g["title"])) != g["norm_key"]
            ],
            "reported_keys": [
                (r["n"], f"{r['release_name']!r}: {r['reported_key']} -> {k}")
                for r in reported
                if (k := album_key(r["release_name"]) or r["release_name"].casefold())
                != r["reported_key"]
            ],
            "title_keys": [
                (r["n"], f"{r['track_name']!r}: {r['norm_title']} -> {k}")
                for r in titles
                if (k := title_key(r["track_name"])) != r["norm_title"]
            ],
        }
        for kind, rows in changes.items():
            rows.sort(reverse=True)
            print(f"{kind}: {len(rows)} changes, {sum(n for n, _ in rows)} listens or groups")
            for n, line in rows[:SHOW]:
                print(f"  {n:6d}  {line}")
        ctx.rows = sum(len(v) for v in changes.values())
        ctx.notes.update(
            {k: len(v) for k, v in changes.items()},
            examples={k: [line for _, line in v[:10]] for k, v in changes.items()},
        )

    return _run
