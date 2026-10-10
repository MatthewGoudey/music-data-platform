"""Generated lists (docs/graph/COMPANION_SPEC.md 5.4–5.6; graph spec section 11): `following`
from the people, bands, labels and studios Matt follows, and `graph_walk`, the walk back from one
album. Each rewrite replaces its list's entries with complete `list_entry` rows (keys from the
same identity functions `lists load` uses, resolved, accepted, a reason in `note`), so the queue
reads them like any list, but only under the profiles that name them.
"""

from __future__ import annotations

from collections import defaultdict

import asyncpg

from musicdata.db import connection
from musicdata.graph.connections import Album, Graph, damp, load_graph
from musicdata.identity import album_key, norm_key
from musicdata.jobs.runs import JobFn, RunContext
from musicdata.queue import config

WALK_KEEP = 60  # entries in a walk
WALK_SECOND_HOP = 15  # first-hop albums the walk steps on from
SECOND_HOP_FACTOR = 0.5


async def write_list(
    conn: asyncpg.Connection, slug: str, entries: list[tuple[int, str]], added_by: str,
    name: str | None = None,
) -> int:  # fmt: skip
    """Replace a generated list's entries: (release_group_id, note), best first."""
    list_id = await conn.fetchval(
        "SELECT list_id FROM list WHERE slug = $1 AND source LIKE 'generated:%'", slug
    )
    if list_id is None:
        raise ValueError(f"no generated list {slug}")
    info = {
        r["release_group_id"]: r
        for r in await conn.fetch(
            """SELECT rg.release_group_id, rg.title, a.name AS artist, rg.first_release_year
                 FROM release_group rg JOIN artist a ON a.artist_id = rg.artist_id
                WHERE rg.release_group_id = ANY($1::int[])""",
            [rg for rg, _ in entries],
        )
    }
    rows, keys = [], set()
    for rg, note in entries:
        r = info.get(rg)
        if r is None:
            continue
        key = (norm_key(r["artist"]), album_key(r["title"]))
        if not all(key) or key in keys:
            continue
        keys.add(key)
        rows.append((list_id, len(rows) + 1, r["artist"], r["title"], r["first_release_year"],
                     note, key[0], key[1], rg, added_by))  # fmt: skip
    async with conn.transaction():
        await conn.execute("DELETE FROM list_entry WHERE list_id = $1", list_id)
        await conn.executemany(
            """INSERT INTO list_entry (list_id, position, raw_artist, raw_album, year, note,
                      artist_key, album_key, release_group_id, resolve_status, review_status,
                      added_by)
               VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, 'resolved', 'accepted', $10)""",
            rows,
        )
        if name:
            await conn.execute("UPDATE list SET name = $2 WHERE list_id = $1", list_id, name)
    return len(rows)


# --- following -----------------------------------------------------------------------------

FOLLOWED_ALBUMS = """
WITH f AS (SELECT f.entity_id, f.followed_at, e.name, e.type, e.artist_id
             FROM graph_follow f JOIN entity e USING (entity_id))
SELECT f.entity_id, f.followed_at, f.name, x.release_group_id, x.why, rg.first_release_year AS year
  FROM f
  CROSS JOIN LATERAL (
      SELECT o.release_group_id,
             coalesce(a.qualifiers ->> 'role', a.qualifiers ->> 'instrument') AS why
        FROM assertion a JOIN entity o ON o.entity_id = a.object_id AND o.type = 'album'
       WHERE a.subject_id = f.entity_id AND a.predicate = 'credited_on' AND a.status = 'accepted'
      UNION ALL
      SELECT s.release_group_id,
             CASE a.predicate WHEN 'released_by' THEN 'label' ELSE 'recorded there' END
        FROM assertion a JOIN entity s ON s.entity_id = a.subject_id AND s.type = 'album'
       WHERE a.object_id = f.entity_id AND a.predicate IN ('released_by', 'recorded_at')
         AND a.status = 'accepted'
      UNION ALL
      SELECT r.release_group_id, NULL
        FROM release_group r
       WHERE r.artist_id = f.artist_id AND r.primary_type = 'Album'
         AND NOT ('Compilation' = ANY(coalesce(r.secondary_types, '{}'))
                  OR 'Live' = ANY(coalesce(r.secondary_types, '{}')))
  ) x
  JOIN release_group rg ON rg.release_group_id = x.release_group_id
 WHERE x.release_group_id IS NOT NULL
 ORDER BY f.followed_at DESC, rg.first_release_year DESC NULLS LAST, x.release_group_id
"""


def _why(raw: str | None) -> str:
    if not raw:
        return ""
    text = raw.strip("[]").replace('"', "")
    return ", ".join(dict.fromkeys(w.strip().lower() for w in text.split(",") if w.strip()))


async def rebuild_following(conn: asyncpg.Connection) -> int:
    entries = []
    for r in await conn.fetch(FOLLOWED_ALBUMS):
        why = _why(r["why"])
        entries.append(
            (r["release_group_id"], f"Following {r['name']}" + (f" · {why}" if why else ""))
        )
    return await write_list(conn, "following", entries, "graph:follow")


def graph_follow_lists() -> JobFn:
    async def _run(ctx: RunContext) -> None:
        async with connection(ctx.pool) as conn:
            ctx.rows = await rebuild_following(conn)

    return _run


# --- walk back -------------------------------------------------------------------------------


def _older(x: Album, than: Album) -> bool:
    return x.year is not None and than.year is not None and x.year < than.year


def walk_scores(g: Graph, start: Album) -> dict[int, tuple[float, str]]:
    """Release group → (score, the strongest step's reason), two hops back from `start`."""
    scores: dict[int, float] = defaultdict(float)
    reasons: dict[int, tuple[float, str]] = {}

    def add(x: Album, value: float, reason: str) -> None:
        if x.entity_id == start.entity_id or (x.parts & start.parts) or not x.release_group_id:
            return
        scores[x.release_group_id] += value
        if value > reasons.get(x.release_group_id, (0.0, ""))[0]:
            reasons[x.release_group_id] = (value, reason)

    def hop(src: Album, factor: float, via: str) -> dict[int, float]:
        reached: dict[int, float] = defaultdict(float)
        for link in g.links_by_album.get(src.entity_id, []):
            name = g.names.get(link.node, "")
            if norm_key(name) in src.parts or norm_key(name) in start.parts:
                continue  # an album's own artist (or the start's): discography is not a step back
            busy = len({x.album for x in g.links_by_node[link.node]})
            for other in g.links_by_node[link.node]:
                x = g.albums.get(other.album)
                if x is None or not _older(x, src):
                    continue
                value = factor * link.weight * min(link.confidence, other.confidence) * damp(busy)
                reason = (
                    f"Recorded at {name}, like {src.title}{via}"
                    if link.kind == "studio"
                    else f"{name} ({link.role}) on both{via}"
                )
                add(x, value, reason)
                reached[x.entity_id] += value
        for p in g.pointers.get(src.entity_id, []):
            weight = config.CONNECTION_WEIGHT[p.kind]
            for x in g.albums.values():
                hit = (p.target_album == x.entity_id) or bool(
                    p.target_parts and x.parts & p.target_parts
                )
                if not hit or not _older(x, src):
                    continue
                value = factor * weight * p.confidence
                add(x, value, p.text.removesuffix(" ✓") + via)
                reached[x.entity_id] += value
        return reached

    first = hop(start, 1.0, "")
    for eid, _v in sorted(first.items(), key=lambda kv: -kv[1])[:WALK_SECOND_HOP]:
        step = g.albums[eid]
        hop(step, SECOND_HOP_FACTOR, f" (via {step.title})")
    return {rg: (round(v, 4), reasons[rg][1]) for rg, v in scores.items()}


async def walk(conn: asyncpg.Connection, release_group_id: int) -> dict[str, object]:
    g = await load_graph(conn)
    start = next((a for a in g.albums.values() if a.release_group_id == release_group_id), None)
    title = await conn.fetchval(
        "SELECT title FROM release_group WHERE release_group_id = $1", release_group_id
    )
    if title is None:
        raise LookupError("no such album")
    scored = walk_scores(g, start) if start is not None else {}
    best = sorted(scored.items(), key=lambda kv: (-kv[1][0], kv[0]))[:WALK_KEEP]
    n = await write_list(conn, "graph_walk", [(rg, why) for rg, (_s, why) in best],
                         f"graph:{release_group_id}", name=f"Walk back from {title}")  # fmt: skip
    return {"from": release_group_id, "title": title, "entries": n}
