"""`musicdata graph seed` (GRAPH_SPEC section 5). Idempotent.

Loads the 21 predicates from `seeds/graph/predicates.csv`; one `map` entity (`v_atlas`); every
atlas lane as a `lane` entity; every list as a `list` entity; and for each resolved `v_atlas`
entry an `album` entity with its `map_membership` coordinates.

The atlas is AI-written, so it sets the pilot's scope and never makes a claim (spec v5): the
seed writes no `lane_parent` claims, and a run removes any an earlier rule wrote.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

import asyncpg

from musicdata.db import connection
from musicdata.identity import norm_key
from musicdata.jobs.runs import JobFn, RunContext

PREDICATES = Path("seeds/graph/predicates.csv")
MAP = "v_atlas"
MAP_NAME = "The V — Album Atlas"


def read_predicates(path: Path = PREDICATES) -> list[dict[str, object]]:
    with path.open(encoding="utf-8", newline="") as f:
        return [
            {
                "name": r["name"],
                "facet": r["facet"],
                "subject_types": r["subject_types"].split("|"),
                "object_types": r["object_types"].split("|"),
                "symmetric": r["symmetric"] == "true",
                "lineage": r["lineage"] == "true",
                "description": r["description"],
            }
            for r in csv.DictReader(f)
        ]


LOCAL_ENTITY = """
    INSERT INTO entity (type, name, norm_key, local_key, attrs, resolve_status)
    VALUES ($1, $2, $3, $4, $5::jsonb, 'local')
    ON CONFLICT (type, local_key) WHERE local_key IS NOT NULL
       DO UPDATE SET name = EXCLUDED.name, norm_key = EXCLUDED.norm_key,
                     attrs = entity.attrs || EXCLUDED.attrs
    RETURNING entity_id
"""

ALBUMS = """
    INSERT INTO entity (type, name, norm_key, context_key, mbid, release_group_id, attrs)
    SELECT DISTINCT ON (rg.release_group_id)
           'album', rg.title, rg.norm_key, a.norm_key, rg.mbid, rg.release_group_id,
           jsonb_build_object('year', rg.first_release_year, 'artist', a.name)
      FROM list_entry e
      JOIN list l USING (list_id)
      JOIN release_group rg ON rg.release_group_id = e.release_group_id
      JOIN artist a ON a.artist_id = rg.artist_id
     WHERE l.slug = $1 AND e.resolve_status = 'resolved' AND e.review_status = 'accepted'
     ORDER BY rg.release_group_id
    ON CONFLICT (release_group_id) WHERE release_group_id IS NOT NULL
       DO UPDATE SET name = EXCLUDED.name, norm_key = EXCLUDED.norm_key,
                     context_key = EXCLUDED.context_key, mbid = EXCLUDED.mbid,
                     attrs = entity.attrs || EXCLUDED.attrs
"""

MEMBERSHIP = """
    INSERT INTO map_membership (entity_id, map, coords)
    SELECT DISTINCT ON (en.entity_id) en.entity_id, $1,
           jsonb_build_object('atlas_id', e.facets ->> 'atlas_id', 'lane', e.lane_id,
                              'zone', e.zone, 'layer', e.layer, 'priority', e.priority,
                              'start_here', e.start_here)
      FROM list_entry e
      JOIN list l USING (list_id)
      JOIN entity en ON en.release_group_id = e.release_group_id AND en.type = 'album'
     WHERE l.slug = $1 AND e.resolve_status = 'resolved' AND e.review_status = 'accepted'
     ORDER BY en.entity_id, e.entry_id
    ON CONFLICT (entity_id, map) DO UPDATE SET coords = EXCLUDED.coords
"""


async def seed(conn: asyncpg.Connection, run_id: int | None, root: Path = Path(".")) -> dict:
    predicates = read_predicates(root / PREDICATES)
    async with conn.transaction():
        await conn.executemany(
            """INSERT INTO predicate (name, facet, subject_types, object_types, "symmetric",
                                      lineage, description)
               VALUES ($1, $2, $3, $4, $5, $6, $7)
               ON CONFLICT (name) DO UPDATE
                  SET facet = EXCLUDED.facet, subject_types = EXCLUDED.subject_types,
                      object_types = EXCLUDED.object_types, "symmetric" = EXCLUDED."symmetric",
                      lineage = EXCLUDED.lineage, description = EXCLUDED.description""",
            [tuple(p.values()) for p in predicates],
        )
        await conn.fetchval(LOCAL_ENTITY, "map", MAP_NAME, norm_key(MAP_NAME), MAP, "{}")

        lanes: dict[str, int] = {}
        for r in await conn.fetch("SELECT lane_id, name, zone, era_span FROM atlas_lane"):
            lanes[r["lane_id"]] = await conn.fetchval(
                LOCAL_ENTITY,
                "lane",
                f"{r['lane_id']} {r['name']}",
                norm_key(f"{r['lane_id']} {r['name']}"),
                r["lane_id"],
                json.dumps({"map": MAP, "zone": r["zone"], "era_span": r["era_span"]}),
            )
        removed = await conn.execute(
            "DELETE FROM assertion WHERE predicate = 'lane_parent' AND source LIKE 'map:%'"
        )

        for r in await conn.fetch("SELECT slug, name FROM list ORDER BY slug"):
            await conn.fetchval(
                LOCAL_ENTITY, "list", r["name"], norm_key(r["name"]), r["slug"], "{}"
            )

        await conn.execute(ALBUMS, MAP)
        await conn.execute(MEMBERSHIP, MAP)
        counts = await conn.fetchrow(
            """SELECT (SELECT count(*) FROM predicate) AS predicates,
                      (SELECT count(*) FROM entity WHERE type = 'map') AS maps,
                      (SELECT count(*) FROM entity WHERE type = 'lane') AS lanes,
                      (SELECT count(*) FROM entity WHERE type = 'list') AS lists,
                      (SELECT count(*) FROM entity WHERE type = 'album') AS albums,
                      (SELECT count(*) FROM map_membership WHERE map = $1) AS memberships""",
            MAP,
        )
    return dict(counts) | {"lane_parent_removed": int(removed.split()[-1])}


def graph_seed(root: Path = Path(".")) -> JobFn:
    async def _run(ctx: RunContext) -> None:
        async with connection(ctx.pool) as conn:
            counts = await seed(conn, ctx.run_id, root)
        ctx.rows = int(counts["albums"])
        ctx.notes.update(counts)

    return _run
