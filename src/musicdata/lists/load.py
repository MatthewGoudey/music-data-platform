"""`musicdata lists load`: the seed files → list, list_entry, atlas lanes and paths.

Idempotent (docs/QUEUE_SPEC.md section 5). Lists upsert by slug and entries by
(list, artist key, album key); a reload refreshes an entry's facts and never touches its
resolution, so a resolved album stays resolved. Entries that left a file stay in the
database and are counted, not deleted. Runs from the laptop: the third-party lists are
not on GitHub (`musicdata --env <env> lists load`).
"""

from __future__ import annotations

import json
from pathlib import Path

import asyncpg

from musicdata.db import connection
from musicdata.jobs.runs import JobFn, RunContext
from musicdata.lists.seed import ListFile, read_lanes, read_list, read_paths, read_registry

REGISTRY = Path("seeds/lists/_lists.csv")
ATLAS = Path("seeds/atlas")


async def load_atlas(conn: asyncpg.Connection, root: Path) -> dict[str, int]:
    lanes = read_lanes(root / ATLAS / "lanes.csv")
    paths = read_paths(root / ATLAS / "paths.csv")
    async with conn.transaction():
        await conn.executemany(
            """INSERT INTO atlas_lane (lane_id, name, zone, era_span, definition)
               VALUES ($1, $2, $3, $4, $5)
               ON CONFLICT (lane_id) DO UPDATE
                  SET name = EXCLUDED.name, zone = EXCLUDED.zone,
                      era_span = EXCLUDED.era_span, definition = EXCLUDED.definition""",
            [(x.lane_id, x.name, x.zone, x.era_span, x.definition) for x in lanes],
        )
        await conn.execute("DELETE FROM atlas_lane_parent")
        await conn.executemany(
            "INSERT INTO atlas_lane_parent (lane_id, parent_lane_id) VALUES ($1, $2)",
            [(x.lane_id, p) for x in lanes for p in x.parents],
        )
        await conn.execute("DELETE FROM atlas_path")
        await conn.executemany(
            """INSERT INTO atlas_path (path, step, atlas_id, connection_to_next)
               VALUES ($1, $2, $3, $4)""",
            paths,
        )
    return {"lanes": len(lanes), "paths": len(paths)}


async def load_list(conn: asyncpg.Connection, lf: ListFile) -> dict[str, int]:
    s = lf.spec
    async with conn.transaction():
        list_id = await conn.fetchval(
            """INSERT INTO list (slug, name, goal, weight, ranked, default_priority, source,
                                 file_rows)
               VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
               ON CONFLICT (slug) DO UPDATE
                  SET name = EXCLUDED.name, goal = EXCLUDED.goal, weight = EXCLUDED.weight,
                      ranked = EXCLUDED.ranked, default_priority = EXCLUDED.default_priority,
                      source = EXCLUDED.source, file_rows = EXCLUDED.file_rows
               RETURNING list_id""",
            s.slug,
            s.name,
            s.goal,
            s.weight,
            s.ranked,
            s.default_priority,
            s.source,
            len(lf.entries),
        )
        before = await conn.fetchval("SELECT count(*) FROM list_entry WHERE list_id = $1", list_id)
        await conn.executemany(
            """INSERT INTO list_entry (list_id, position, raw_artist, raw_album, year, priority,
                                       lane_id, zone, layer, start_here, facets, note,
                                       artist_key, album_key)
               VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11::jsonb, $12, $13, $14)
               ON CONFLICT (list_id, artist_key, album_key) DO UPDATE
                  SET position = EXCLUDED.position, raw_artist = EXCLUDED.raw_artist,
                      raw_album = EXCLUDED.raw_album, year = EXCLUDED.year,
                      priority = EXCLUDED.priority, lane_id = EXCLUDED.lane_id,
                      zone = EXCLUDED.zone, layer = EXCLUDED.layer,
                      start_here = EXCLUDED.start_here, facets = EXCLUDED.facets,
                      note = EXCLUDED.note""",
            [
                (
                    list_id,
                    e.position,
                    e.raw_artist,
                    e.raw_album,
                    e.year,
                    e.priority,
                    e.lane_id,
                    e.zone,
                    e.layer,
                    e.start_here,
                    json.dumps(e.facets, ensure_ascii=False),
                    e.note,
                    e.artist_key,
                    e.album_key,
                )
                for e in lf.entries
            ],
        )
        stale = await conn.fetchval(
            """SELECT count(*) FROM list_entry le
                WHERE le.list_id = $1
                  AND NOT EXISTS (SELECT 1 FROM unnest($2::text[], $3::text[]) AS u(a, b)
                                   WHERE u.a = le.artist_key AND u.b = le.album_key)""",
            list_id,
            [e.artist_key for e in lf.entries],
            [e.album_key for e in lf.entries],
        )
        after = await conn.fetchval("SELECT count(*) FROM list_entry WHERE list_id = $1", list_id)
    return {
        "file_rows": lf.rows,
        "entries": len(lf.entries),
        "duplicates_in_file": lf.duplicates,
        "keyless": lf.keyless,
        "added": after - before,
        "no_longer_in_file": stale,
    }


def lists_load(*, root: Path = Path(".")) -> JobFn:
    async def _run(ctx: RunContext) -> None:
        specs = read_registry(root / REGISTRY)
        files = [read_list(s, root) for s in specs]  # read everything before writing anything
        async with connection(ctx.pool) as conn:
            ctx.notes["atlas"] = await load_atlas(conn, root)
            for lf in files:
                ctx.notes[lf.spec.slug] = await load_list(conn, lf)
        ctx.rows = sum(len(lf.entries) for lf in files)

    return _run
