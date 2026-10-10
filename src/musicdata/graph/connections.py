"""Connections and threads (docs/graph/COMPANION_SPEC.md section 4; Phase 6 Block D).

A connection links a candidate album g to a heard album h through one connecting node: a person or
band credited on or a member of both, a studio both were recorded at, an artist or album g sounds
like or was influenced by that h is by, or a first recording g covers that h's artist made. Heard
albums that share an artist part with g never count: the queue's artist affinity covers those.

    C(g) = Σ over nodes n:  weight(n) × confidence(n) × damp(n) × listen(n)

`graph connections` (nightly) rewrites `graph_connection` for every resolved list album with a
connection; `graph threads` (after every derive) rewrites `graph_thread` from the albums finished
in the last week. Reads accepted claims only; the constants live in `musicdata.queue.config`.
"""

from __future__ import annotations

import json
import math
import re
from collections import defaultdict
from dataclasses import dataclass, field
from urllib.parse import urlparse

import asyncpg

from musicdata.db import connection
from musicdata.identity import norm_key
from musicdata.jobs.runs import JobFn, RunContext
from musicdata.queue import config

_PARTS = re.compile(r"\s*(?:,|&|\+|/|\band\b|\bwith\b|\bfeat\.?|\bfeaturing\b)\s*", re.IGNORECASE)


def artist_parts(name: str | None) -> frozenset[str]:
    """The keys of an artist credit and of each artist in it ("Neil Young & Crazy Horse" → the
    whole credit, Neil Young, Crazy Horse)."""
    if not name:
        return frozenset()
    keys = {norm_key(name)} | {norm_key(p) for p in _PARTS.split(name)}
    return frozenset(k for k in keys if k)


_ROLE_NAMES = {"written-by": "songwriter", "composer": "songwriter", "lyricist": "lyrics"}


def role_text(role: object) -> str:
    """A role as the card shows it: lower case, MusicBrainz's names made plain, lists joined
    (["guitar", "lead vocals"] → "guitar, lead vocals")."""
    parts = role if isinstance(role, list) else [role]
    raw = [w.strip().lower() for r in parts if r for w in str(r).split(",") if w.strip()]
    words = [_ROLE_NAMES.get(w, w) for w in raw]
    words = [w for w in words if w != "performer"] or words
    return ", ".join(dict.fromkeys(words)) or "credited"


def role_weight(roles: list[str]) -> tuple[float, str]:
    """A credit's weight on the candidate (spec 4) and the role shown on the card."""
    best, shown = 0.0, roles[0] if roles else "credited"
    for r in roles or ["credited"]:
        low = r.lower()
        if "master" in low:
            w = config.CONNECTION_WEIGHT["person_mastering"]
        elif re.search(r"produc|writ|compos|lyric", low):
            w = config.CONNECTION_WEIGHT["person_core"]
        else:  # engineering, mixing, playing or singing as a session player
            w = config.CONNECTION_WEIGHT["person_session"]
        if w > best:
            best, shown = w, r
    return best, role_text(shown)


@dataclass
class Album:
    entity_id: int
    release_group_id: int
    title: str
    artist: str
    parts: frozenset[str]
    sessions: int = 0  # full sessions; > 0 means heard
    year: int | None = None


@dataclass
class Link:
    """One node's tie to one album: how (`kind`), its weight there, the role shown, confidence."""

    node: int
    album: int  # album entity id
    kind: str  # person | member | studio
    weight: float
    role: str
    confidence: float
    assertion_id: int


@dataclass
class Pointer:
    """A lineage or cover claim of a candidate: it points at an artist (parts) or an album."""

    album: int  # the candidate's entity id
    kind: str  # lineage | cover
    node: int
    target_parts: frozenset[str]
    target_album: int | None
    text: str
    confidence: float
    assertion_id: int


@dataclass
class Graph:
    albums: dict[int, Album]  # by entity id
    names: dict[int, str]  # node entity id → name
    links_by_album: dict[int, list[Link]] = field(default_factory=lambda: defaultdict(list))
    links_by_node: dict[int, list[Link]] = field(default_factory=lambda: defaultdict(list))
    pointers: dict[int, list[Pointer]] = field(default_factory=lambda: defaultdict(list))

    def add(self, link: Link) -> None:
        self.links_by_album[link.album].append(link)
        self.links_by_node[link.node].append(link)


def damp(albums: int) -> float:
    return 1 / (1 + math.log(1 + albums / config.DAMP_ALBUMS))


def listen(sessions: int) -> float:
    return min(1.0, config.LISTEN_BASE + config.LISTEN_STEP * sessions)


def connect(g: Graph, cand: Album, heard: dict[int, Album]) -> tuple[float, list[dict]]:
    """C(g) and every node's entry, strongest first. `heard` maps entity id → heard album."""
    usable = {e: h for e, h in heard.items() if e != cand.entity_id and not (h.parts & cand.parts)}
    if not usable:
        return 0.0, []
    nodes: dict[int, dict] = {}

    # People and studios: the node's best tie to the candidate, then the heard albums it reaches.
    for link in g.links_by_album.get(cand.entity_id, []):
        if norm_key(g.names.get(link.node, "")) in cand.parts:
            continue  # the candidate's own artist: artist affinity covers it
        reach = [x for x in g.links_by_node[link.node] if x.album in usable]
        if not reach:
            continue
        best_h = max(reach, key=lambda x: (min(x.confidence, link.confidence),
                                           usable[x.album].sessions, -x.album))  # fmt: skip
        h = usable[best_h.album]
        conf = min(best_h.confidence, link.confidence)
        busy = len({x.album for x in g.links_by_node[link.node]})
        sessions = sum(usable[a].sessions for a in {x.album for x in reach})
        value = link.weight * conf * damp(busy) * listen(sessions)
        name = g.names.get(link.node, "?")
        if link.kind == "studio":
            text = f"Recorded at {name} · also {h.title} ✓"
        elif best_h.kind == "member":
            text = f"{name} ({link.role}) · {h.artist} ✓"
        else:
            text = f"{name} ({link.role}) · also on {h.title} ✓"
        entry = {
            "kind": "studio" if link.kind == "studio" else "person",
            "node": link.node, "text": text, "heard": h.release_group_id,
            "weight": round(value, 4),
            "assertions": sorted({link.assertion_id, best_h.assertion_id}),
        }  # fmt: skip
        if link.node not in nodes or value > nodes[link.node]["weight"]:
            nodes[link.node] = entry

    # Lineage and covers: the candidate's own claims, reaching heard albums by that artist.
    for p in g.pointers.get(cand.entity_id, []):
        if p.target_parts & cand.parts:
            continue
        reach = [
            h
            for e, h in usable.items()
            if (p.target_album is not None and e == p.target_album)
            or (p.target_parts and h.parts & p.target_parts)
        ]
        if not reach:
            continue
        sessions = sum(h.sessions for h in reach)
        value = config.CONNECTION_WEIGHT[p.kind] * p.confidence * listen(sessions)
        h = max(reach, key=lambda x: (x.sessions, -x.entity_id))
        entry = {
            "kind": p.kind, "node": p.node, "text": p.text, "heard": h.release_group_id,
            "weight": round(value, 4), "assertions": [p.assertion_id],
        }  # fmt: skip
        if p.node not in nodes or value > nodes[p.node]["weight"]:
            nodes[p.node] = entry

    ranked = sorted(nodes.values(), key=lambda x: (-x["weight"], x["node"]))
    return round(sum(x["weight"] for x in ranked), 4), ranked


# --- loading -----------------------------------------------------------------------------

ALBUMS = """
SELECT e.entity_id, e.release_group_id, rg.title, a.name AS artist,
       coalesce(s.full_sessions, 0) AS sessions, rg.first_release_year AS year
  FROM entity e
  JOIN release_group rg ON rg.release_group_id = e.release_group_id
  JOIN artist a ON a.artist_id = rg.artist_id
  LEFT JOIN release_group_stat s ON s.release_group_id = e.release_group_id
 WHERE e.type = 'album'
"""
CREDITS = """
SELECT a.assertion_id, a.subject_id AS node, coalesce(al.entity_id, a.album_context) AS album,
       a.qualifiers, a.confidence
  FROM assertion a
  JOIN entity o ON o.entity_id = a.object_id
  LEFT JOIN entity al ON al.entity_id = a.object_id AND al.type = 'album'
 WHERE a.predicate = 'credited_on' AND a.status = 'accepted'
   AND (o.type = 'album' OR a.album_context IS NOT NULL)
"""
MEMBERS = """
SELECT a.assertion_id, a.subject_id AS node, b.name AS band, a.qualifiers, a.confidence
  FROM assertion a JOIN entity b ON b.entity_id = a.object_id
 WHERE a.predicate = 'member_of' AND a.status = 'accepted'
"""
STUDIOS = """
SELECT a.assertion_id, a.object_id AS node, a.subject_id AS album, a.confidence
  FROM assertion a
  JOIN entity s ON s.entity_id = a.subject_id AND s.type = 'album'
  JOIN entity o ON o.entity_id = a.object_id AND o.type = 'place'
 WHERE a.predicate = 'recorded_at' AND a.status = 'accepted'
"""
POINTERS = """
SELECT a.assertion_id, a.predicate, a.subject_id, s.type AS s_type, s.name AS s_name,
       s.attrs ->> 'artist' AS s_artist, a.object_id, o.type AS o_type, o.name AS o_name,
       o.attrs ->> 'artist' AS o_artist, a.qualifiers, a.confidence, a.source_url, a.album_context
  FROM assertion a
  JOIN entity s ON s.entity_id = a.subject_id
  JOIN entity o ON o.entity_id = a.object_id
 WHERE a.status = 'accepted'
   AND a.predicate IN ('sounds_like', 'influenced_by', 'covers', 'interpolates')
"""
NAMES = "SELECT entity_id, name FROM entity WHERE entity_id = ANY($1::bigint[])"


def _quals(q) -> dict:
    return json.loads(q) if isinstance(q, str) else (q or {})


def _roles(q: dict) -> list[str]:
    r = q.get("role")
    return [r] if isinstance(r, str) else [str(x) for x in (r or [])]


def _site(url: str | None) -> str:
    host = (urlparse(url or "").hostname or "").removeprefix("www.")
    return "Wikipedia" if host.endswith("wikipedia.org") else host or "A source"


def _work(name: str) -> str:
    return name.split(" – ", 1)[1] if " – " in name else name


async def load_graph(conn: asyncpg.Connection) -> Graph:
    albums = {
        r["entity_id"]: Album(
            r["entity_id"],
            r["release_group_id"],
            r["title"],
            r["artist"],
            artist_parts(r["artist"]),
            int(r["sessions"]),
            r["year"],
        )  # fmt: skip
        for r in await conn.fetch(ALBUMS)
    }
    g = Graph(albums=albums, names={})
    for r in await conn.fetch(CREDITS):
        if r["album"] not in albums:
            continue
        w, role = role_weight(_roles(_quals(r["qualifiers"])))
        g.add(Link(r["node"], r["album"], "person", w, role, float(r["confidence"]),
                   r["assertion_id"]))  # fmt: skip
    by_part: dict[str, list[Album]] = defaultdict(list)
    for a in albums.values():
        for k in a.parts:
            by_part[k].append(a)
    for r in await conn.fetch(MEMBERS):
        q = _quals(r["qualifiers"])
        role = role_text(q.get("instrument") or "member")
        for a in by_part.get(norm_key(r["band"] or ""), []):
            g.add(Link(r["node"], a.entity_id, "member", config.CONNECTION_WEIGHT["person_core"],
                       role, float(r["confidence"]), r["assertion_id"]))  # fmt: skip
    for r in await conn.fetch(STUDIOS):
        if r["album"] in albums:
            g.add(Link(r["node"], r["album"], "studio", config.CONNECTION_WEIGHT["studio"],
                       "studio", float(r["confidence"]), r["assertion_id"]))  # fmt: skip
    for r in await conn.fetch(POINTERS):
        q = _quals(r["qualifiers"])
        if r["predicate"] in ("sounds_like", "influenced_by"):
            cand = r["subject_id"] if r["s_type"] == "album" else None
            if cand not in albums:
                continue
            target = r["o_artist"] if r["o_type"] == "album" else r["o_name"]
            src = q.get("via") or _site(r["source_url"])
            text = (
                f"{src} compares it to {target} ✓"
                if r["predicate"] == "sounds_like"
                else f"{src} names {target} as an influence ✓"
            )
            g.pointers[cand].append(Pointer(
                cand, "lineage", r["object_id"],
                artist_parts(r["o_name"] if r["o_type"] != "album" else r["o_artist"]),
                r["object_id"] if r["o_type"] == "album" else None,
                text, float(r["confidence"]), r["assertion_id"],
            ))  # fmt: skip
        else:  # covers, interpolates: the candidate's own recording borrows a first recording
            cand = r["album_context"] if r["s_type"] == "recording" else r["subject_id"]
            if cand not in albums:
                continue
            if r["s_type"] == "recording" and not (
                artist_parts(r["s_artist"]) & albums[cand].parts
            ):
                continue  # a later artist covering the candidate's song: not the candidate's tie
            orig = r["o_artist"] or ""
            if not orig:
                continue
            verb = "Covers" if r["predicate"] == "covers" else "Borrows from"
            work = q.get("work") or _work(r["o_name"])
            g.pointers[cand].append(Pointer(
                cand, "cover", r["object_id"], artist_parts(orig), None,
                f'{verb} "{work}" ({orig}) ✓', float(r["confidence"]), r["assertion_id"],
            ))  # fmt: skip
    nodes = list(g.links_by_node)
    g.names = {r[0]: r[1] for r in await conn.fetch(NAMES, nodes)}
    return g


CANDIDATES = """
SELECT DISTINCT e.release_group_id FROM list_entry e
 WHERE e.resolve_status = 'resolved' AND e.review_status = 'accepted'
   AND e.release_group_id IS NOT NULL
"""


async def compute_connections(conn: asyncpg.Connection) -> tuple[list[tuple], dict[str, int]]:
    g = await load_graph(conn)
    heard = {e: a for e, a in g.albums.items() if a.sessions > 0}
    by_rg = {a.release_group_id: a for a in g.albums.values()}
    rows = []
    for r in await conn.fetch(CANDIDATES):
        cand = by_rg.get(r[0])
        if cand is None:
            continue
        score, ranked = connect(g, cand, heard)
        if score > 0:
            rows.append((cand.release_group_id, score, json.dumps(ranked[: config.CONNECTION_TOP])))
    stats = {
        "albums": len(g.albums), "heard_in_graph": len(heard), "connected": len(rows),
        "nodes": len(g.links_by_node),
    }  # fmt: skip
    return rows, stats


async def store_connections(conn: asyncpg.Connection, rows: list[tuple]) -> None:
    async with conn.transaction():
        await conn.execute("DELETE FROM graph_connection")
        await conn.executemany(
            "INSERT INTO graph_connection (release_group_id, score, top) VALUES ($1, $2, $3::jsonb)",
            rows,
        )


def graph_connections() -> JobFn:
    async def _run(ctx: RunContext) -> None:
        async with connection(ctx.pool) as conn:
            rows, stats = await compute_connections(conn)
            await store_connections(conn, rows)
        ctx.rows = len(rows)
        ctx.notes.update(**stats)

    return _run


# --- threads -----------------------------------------------------------------------------

FINISHED = f"""
SELECT release_group_id, max(started_at) AS at FROM album_session
 WHERE session_type = 'full' AND started_at > now() - interval '{config.THREAD_DAYS} days'
 GROUP BY release_group_id ORDER BY at DESC LIMIT {config.THREAD_FROM}
"""


async def compute_threads(conn: asyncpg.Connection) -> list[tuple]:
    finished = [r[0] for r in await conn.fetch(FINISHED)]
    if not finished:
        return []
    g = await load_graph(conn)
    by_rg = {a.release_group_id: a for a in g.albums.values()}
    unheard = [
        by_rg[r[0]] for r in await conn.fetch(CANDIDATES)
        if r[0] in by_rg and by_rg[r[0]].sessions == 0
    ]  # fmt: skip
    rows = []
    for rg in finished:
        f = by_rg.get(rg)
        if f is None:
            continue
        heard = {f.entity_id: Album(f.entity_id, f.release_group_id, f.title, f.artist, f.parts,
                                    max(f.sessions, 1))}  # fmt: skip
        scored = []
        for cand in unheard:
            score, ranked = connect(g, cand, heard)
            if score > 0:
                scored.append((score, cand.release_group_id, ranked[0]))
        scored.sort(key=lambda x: (-x[0], x[1]))
        for rank, (_score, to, top) in enumerate(scored[: config.THREAD_TO], 1):
            rows.append((rg, to, rank, json.dumps(top)))
    return rows


def graph_threads() -> JobFn:
    async def _run(ctx: RunContext) -> None:
        async with connection(ctx.pool) as conn:
            if not await conn.fetchval("SELECT to_regclass('graph_thread') IS NOT NULL"):
                ctx.notes.update(reason="no graph_thread table")
                return
            rows = await compute_threads(conn)
            async with conn.transaction():
                await conn.execute("DELETE FROM graph_thread")
                await conn.executemany(
                    """INSERT INTO graph_thread (from_release_group_id, to_release_group_id, rank,
                                                 connection) VALUES ($1, $2, $3, $4::jsonb)""",
                    rows,
                )
        ctx.rows = len(rows)
        ctx.notes.update(threads=len({r[0] for r in rows}))

    return _run
