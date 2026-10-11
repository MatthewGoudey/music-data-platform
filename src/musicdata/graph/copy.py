"""`musicdata --env <target> graph copy --source <env>` (graph spec 7.6; companion spec 3.1).

Copies `predicate`, `entity`, `entity_link`, `map_membership`, `graph_batch`, `graph_album`,
`source_fetch` and `assertion` from the source database into the target's empty graph tables,
ids and all (`OVERRIDING SYSTEM VALUE`), then resets each identity sequence. Listening rows are
not shared between environments, so `entity.release_group_id` and `entity.artist_id` are remapped
by MBID in the target (NULL where the target has no such row; `graph link` fills them later), and
`pipeline_run_id` columns are cleared (they name the source's runs). One transaction: the target
gets everything or nothing. Refuses a target whose graph tables already hold entities or claims,
unless `replace` (dev only: `graph copy --source prod --replace` refills dev from prod, companion
spec 3.1) empties them first, inside the same transaction.
"""

from __future__ import annotations

from collections import Counter

import asyncpg

from musicdata.db import connection
from musicdata.jobs.runs import JobFn, RunContext

TABLES = ["predicate", "entity", "entity_link", "map_membership", "graph_batch", "graph_album",
          "source_fetch", "assertion"]  # fmt: skip
IDENTITY = {"entity": "entity_id", "graph_batch": "batch_id", "source_fetch": "fetch_id",
            "assertion": "assertion_id"}  # fmt: skip
CHUNK = 500


async def _columns(conn: asyncpg.Connection, table: str) -> list[str]:
    return [
        r[0]
        for r in await conn.fetch(
            """SELECT column_name FROM information_schema.columns
                WHERE table_schema = 'public' AND table_name = $1 ORDER BY ordinal_position""",
            table,
        )
    ]


async def graph_tables_mb(conn: asyncpg.Connection) -> float:
    """The graph tables' size with indexes and TOAST, in MB."""
    total = await conn.fetchval(
        "SELECT sum(pg_total_relation_size(t::regclass)) FROM unnest($1::text[]) AS t", TABLES
    )
    return round(int(total or 0) / 1048576, 1)


# Tables that point at graph rows and are emptied with them on --replace, children first.
DEPENDENT = ["graph_follow", "graph_connection", "graph_thread"]


async def copy_graph(
    source: asyncpg.Connection, target: asyncpg.Connection, replace: bool = False
) -> dict:
    held = await target.fetchval(
        "SELECT (SELECT count(*) FROM entity) + (SELECT count(*) FROM assertion)"
    )
    if held and not replace:
        raise RuntimeError(f"the target's graph tables are not empty ({held} entities and claims)")
    counts: Counter = Counter()
    # listening rows by MBID in the source, then by MBID in the target
    src_rg = {r[0]: r[1] for r in await source.fetch(
        "SELECT release_group_id, mbid::text FROM release_group WHERE mbid IS NOT NULL")}  # fmt: skip
    src_artist = {r[0]: r[1] for r in await source.fetch(
        "SELECT artist_id, mbid::text FROM artist WHERE mbid IS NOT NULL")}  # fmt: skip
    tgt_rg = {r[0]: r[1] for r in await target.fetch(
        "SELECT mbid::text, release_group_id FROM release_group WHERE mbid IS NOT NULL")}  # fmt: skip
    tgt_artist = {r[0]: r[1] for r in await target.fetch(
        "SELECT mbid::text, artist_id FROM artist WHERE mbid IS NOT NULL")}  # fmt: skip
    async with target.transaction():
        if replace:
            # TRUNCATE empties tens of thousands of rows at once (DELETE ran past the pool's
            # 60-second statement limit); every table that points at a graph row goes with them.
            present = [t for t in DEPENDENT
                       if await target.fetchval("SELECT to_regclass($1) IS NOT NULL", t)]  # fmt: skip
            await target.execute(f"TRUNCATE {', '.join(TABLES + present)}", timeout=600)
            counts["replaced_entities_and_claims"] = int(held)
        await target.execute("DELETE FROM predicate")  # the seed's rows, replaced by the source's
        for table in TABLES:
            cols = await _columns(target, table)
            src_cols = set(await _columns(source, table))
            if set(cols) != src_cols:
                raise RuntimeError(
                    f"{table}: the two databases' columns differ; migrate both first"
                )
            col_list = ", ".join(f'"{c}"' for c in cols)  # predicate."symmetric" is reserved
            rows = [dict(r) for r in await source.fetch(f"SELECT {col_list} FROM {table}")]
            if table == "entity":
                used: set[int] = set()
                for r in rows:
                    rg = (
                        tgt_rg.get(src_rg.get(r["release_group_id"]))
                        if r["release_group_id"]
                        else None
                    )
                    if rg in used:
                        rg = None
                        counts["entity_rg_duplicate"] += 1
                    if rg:
                        used.add(rg)
                    if r["release_group_id"]:
                        counts["entity_rg_mapped" if rg else "entity_rg_unmapped"] += 1
                    r["release_group_id"] = rg
                    if r["artist_id"]:
                        aid = tgt_artist.get(src_artist.get(r["artist_id"]))
                        counts["entity_artist_mapped" if aid else "entity_artist_unmapped"] += 1
                        r["artist_id"] = aid
            replaces: list[tuple[int, int]] = []
            if table == "assertion":
                for r in rows:
                    if r["replaces"]:
                        replaces.append((r["assertion_id"], r["replaces"]))
                        r["replaces"] = None
            if "pipeline_run_id" in cols:
                for r in rows:
                    r["pipeline_run_id"] = None
            override = " OVERRIDING SYSTEM VALUE" if table in IDENTITY else ""
            sql = (
                f"INSERT INTO {table} ({col_list}){override} "
                f"VALUES ({', '.join(f'${i}' for i in range(1, len(cols) + 1))})"
            )
            for i in range(0, len(rows), CHUNK):
                await target.executemany(
                    sql, [tuple(r[c] for c in cols) for r in rows[i : i + CHUNK]]
                )
            await target.executemany(
                "UPDATE assertion SET replaces = $2 WHERE assertion_id = $1", replaces
            )
            if table in IDENTITY:
                col = IDENTITY[table]
                await target.execute(
                    f"""SELECT setval(pg_get_serial_sequence('{table}', '{col}'),
                                      coalesce((SELECT max({col}) FROM {table}), 1))"""
                )
            counts[table] = len(rows)
    return dict(counts)


def graph_copy(source_url: str, replace: bool = False) -> JobFn:
    async def _run(ctx: RunContext) -> None:
        if replace and ctx.env == "prod":
            raise RuntimeError("--replace empties the target's graph: never in prod")
        source = await asyncpg.connect(source_url)
        try:
            source_mb = await graph_tables_mb(source)
            async with connection(ctx.pool) as target:
                counts = await copy_graph(source, target, replace)
                target_db_mb = round(
                    int(await target.fetchval("SELECT pg_database_size(current_database())"))
                    / 1048576,
                    1,
                )
                target_graph_mb = await graph_tables_mb(target)
        finally:
            await source.close()
        ctx.rows = counts.get("assertion", 0)
        ctx.notes.update(
            source_graph_mb=source_mb, target_graph_mb=target_graph_mb,
            target_database_mb=target_db_mb, **counts,
        )  # fmt: skip

    return _run
