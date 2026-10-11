"""`next_queue`: load what the queue needs from the database and build it (QUEUE_SPEC
sections 7–9). All reads; nothing here writes."""

from __future__ import annotations

import json
import secrets
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime
from urllib.parse import quote
from zoneinfo import ZoneInfo

import asyncpg

from musicdata.queue import config
from musicdata.queue.build import build, day_seed
from musicdata.queue.pools import due
from musicdata.queue.score import Item, Profile, passes, score_candidates, why_line, why_part

CHICAGO = ZoneInfo("America/Chicago")

ENTRIES = """
SELECT e.entry_id, e.list_id, e.release_group_id, l.slug, l.name AS list_name, l.goal,
       l.weight, l.ranked, l.default_priority, e.position, e.priority, e.lane_id,
       al.name AS lane_name, e.zone, e.layer, e.start_here, e.year,
       e.facets->>'descriptors' AS descriptors, e.raw_artist, e.raw_album,
       st.status, st.tracks_heard, st.track_count, st.hidden, st.pinned, st.snoozed_until,
       rg.title, rg.mbid IS NOT NULL AS mapped, rg.first_release_year, rg.primary_type,
       rg.artist_id, a.name AS artist, coalesce(ast.listens, 0) AS artist_listens,
       q.bumped_until, e.note,
       coalesce(l.source LIKE 'generated:%', false) AS generated
  FROM list_entry_status st
  JOIN list_entry e USING (entry_id)
  JOIN list l ON l.list_id = e.list_id
  JOIN release_group rg ON rg.release_group_id = e.release_group_id
  JOIN artist a ON a.artist_id = rg.artist_id
  LEFT JOIN artist_stat ast ON ast.artist_id = rg.artist_id
  LEFT JOIN atlas_lane al ON al.lane_id = e.lane_id
  LEFT JOIN queue_state q ON q.release_group_id = e.release_group_id
"""

HISTORY = """
WITH s AS (
    SELECT release_group_id,
           count(*) FILTER (WHERE session_type = 'full') AS full_sessions,
           count(*) FILTER (WHERE session_type = 'partial') AS partial_sessions,
           max(started_at) FILTER (WHERE session_type = 'full') AS last_full_at,
           max(started_at) AS last_session_at
      FROM album_session GROUP BY release_group_id)
SELECT s.*, rs.listens, rs.last_listened_at, rs.best_completion, rs.tracks_heard,
       rs.track_count, rg.title, rg.first_release_year, rg.primary_type, rg.artist_id,
       a.name AS artist,
       EXISTS (SELECT 1 FROM list_entry e JOIN list l USING (list_id)
                WHERE e.release_group_id = s.release_group_id
                  AND e.review_status = 'accepted' AND e.resolve_status = 'resolved'
                  AND coalesce(l.source NOT LIKE 'generated:%', true)) AS on_list
  FROM s
  JOIN release_group rg USING (release_group_id)
  JOIN artist a ON a.artist_id = rg.artist_id
  LEFT JOIN release_group_stat rs USING (release_group_id)
  LEFT JOIN queue_state q USING (release_group_id)
 WHERE q.hidden_at IS NULL AND (q.snoozed_until IS NULL OR q.snoozed_until <= $1)
   AND q.pinned_at IS NULL
"""

PINS = """
SELECT q.release_group_id, rg.artist_id, a.name AS artist, rg.title, rg.first_release_year,
       rg.primary_type, rs.tracks_heard, coalesce(rs.track_count, t.track_count) AS track_count,
       CASE WHEN rs.full_sessions > 0 THEN 'heard'
            WHEN rs.partial_sessions > 0 THEN 'started' ELSE 'unheard' END AS status
  FROM queue_state q
  JOIN release_group rg USING (release_group_id)
  JOIN artist a ON a.artist_id = rg.artist_id
  LEFT JOIN release_group_stat rs USING (release_group_id)
  LEFT JOIN release_group_tracklist t USING (release_group_id)
 WHERE q.pinned_at IS NOT NULL AND q.hidden_at IS NULL
 ORDER BY q.pinned_at, q.release_group_id
"""


class UnknownProfileError(LookupError):
    pass


@dataclass
class ScoreContext:
    """What scoring reads once, so several profiles can be scored from one read."""

    entries: list[asyncpg.Record]
    tags: dict[int, list[str]]
    list_sizes: dict[int, int]
    lane_shares: dict[str, float]
    connections: dict[int, float]  # C(g) by release group (companion spec 4)
    lines: dict[int, list[str]]  # card lines by release group

    def score(self, p: Profile, now: datetime) -> list[Item]:
        return score_candidates(
            self.entries, p, list_sizes=self.list_sizes, lane_shares=self.lane_shares,
            tags=self.tags, now=now, connections=self.connections,
        )  # fmt: skip


async def score_context(conn: asyncpg.Connection) -> ScoreContext:
    entries = await conn.fetch(ENTRIES)
    tags: dict[int, list[str]] = defaultdict(list)
    for r in await conn.fetch(
        """SELECT rt.release_group_id, t.name FROM release_group_tag rt JOIN tag t USING (tag_id)
            WHERE rt.status = 'applied' ORDER BY t.name"""
    ):
        tags[r["release_group_id"]].append(r["name"])
    list_sizes = {
        r["list_id"]: r["n"]
        for r in await conn.fetch("SELECT list_id, count(*) AS n FROM list_entry GROUP BY 1")
    }
    lane_total: dict[str, int] = defaultdict(int)
    lane_heard: dict[str, int] = defaultdict(int)
    for e in entries:
        if e["slug"] == "v_atlas" and e["lane_id"]:
            lane_total[e["lane_id"]] += 1
            lane_heard[e["lane_id"]] += e["status"] == "heard"
    lane_shares = {k: lane_heard[k] / v for k, v in lane_total.items()}
    connections: dict[int, float] = {}
    lines: dict[int, list[str]] = {}
    if await conn.fetchval("SELECT to_regclass('graph_connection') IS NOT NULL"):
        for r in await conn.fetch(
            "SELECT release_group_id, score, top::text FROM graph_connection"
        ):
            connections[r[0]] = float(r[1])
            lines[r[0]] = [t["text"] for t in json.loads(r[2])][: config.CARD_LINES]
    return ScoreContext(entries, tags, list_sizes, lane_shares, connections, lines)


# Companion spec 5.3: the newest finished albums with threads, each thread's targets by rank.
THREADS = """
SELECT t.from_release_group_id, f.title AS finished, t.to_release_group_id, t.connection::text
  FROM graph_thread t
  JOIN release_group f ON f.release_group_id = t.from_release_group_id
  JOIN (SELECT release_group_id, max(started_at) AS at FROM album_session
         WHERE session_type = 'full' GROUP BY release_group_id) s
    ON s.release_group_id = t.from_release_group_id
 ORDER BY s.at DESC, t.rank
"""


# An album's genres: list genre fields (RYM, Acclaimed Music) and the graph's has_genre claims.
ALBUM_GENRES = r"""
SELECT e.release_group_id AS rg, lower(trim(x)) AS genre
  FROM list_entry e,  -- bracketed notes ("jazz (specifically vocal jazz, …)") go before the split
       unnest(string_to_array(regexp_replace(e.facets ->> 'genre', '\s*\([^)]*\)', '', 'g'), ',')) x
 WHERE e.facets ? 'genre' AND e.release_group_id = ANY($1::int[]) AND trim(x) <> ''
UNION
SELECT en.release_group_id, lower(o.name)
  FROM assertion a
  JOIN entity en ON en.entity_id = a.subject_id AND en.type = 'album'
  JOIN entity o ON o.entity_id = a.object_id
 WHERE a.predicate = 'has_genre' AND a.status = 'accepted'
   AND en.release_group_id = ANY($1::int[])
"""
RECENT = """
SELECT DISTINCT release_group_id FROM album_session
 WHERE started_at > now() - make_interval(days => $1)
"""


async def album_genres(conn: asyncpg.Connection, rgs: list[int]) -> dict[int, set[str]]:
    out: dict[int, set[str]] = defaultdict(set)
    if not rgs:
        return out
    if not await conn.fetchval("SELECT to_regclass('assertion') IS NOT NULL"):
        q = ALBUM_GENRES.split("UNION")[0]
    else:
        q = ALBUM_GENRES
    for r in await conn.fetch(q, rgs):
        out[r["rg"]].add(r["genre"])
    return out


async def tastebreaker_choices(
    conn: asyncpg.Connection, candidates: list[Item], seed: int
) -> list[tuple[Item, str]]:
    """Queue spec v20: candidates whose genres share nothing with what Matt played in the last
    TASTEBREAKER_DAYS days, the Claude canon first; the day's seed picks one of the best
    TASTEBREAKER_POOL, the rest follow as fallbacks."""
    import random

    recent = [r[0] for r in await conn.fetch(RECENT, config.TASTEBREAKER_DAYS)]
    lately = set().union(*(await album_genres(conn, recent)).values()) if recent else set()
    pool = candidates[: max(config.TASTEBREAKER_POOL * 40, 1000)]
    genres = await album_genres(conn, [c.release_group_id for c in pool])
    eligible = [c for c in pool if genres.get(c.release_group_id)
                and not (genres[c.release_group_id] & lately)]  # fmt: skip
    first = [c for c in eligible if any(w["list"] == config.TASTEBREAKER_FIRST for w in c.why)]
    ranked = first or eligible
    if not ranked:
        return []
    top = ranked[: config.TASTEBREAKER_POOL]
    pick = random.Random(seed).choice(top)
    order = [pick] + [c for c in ranked if c is not pick]

    def because(c: Item) -> str:
        g = ", ".join(sorted(genres[c.release_group_id])[:3])
        return f"Tastebreaker · {g} — nothing like it in your last {config.TASTEBREAKER_DAYS} days"

    return [(c, because(c)) for c in order]


async def thread_choices(
    conn: asyncpg.Connection, candidates: list[Item]
) -> list[tuple[Item, str, int]]:
    """Candidates under the profile that a thread reaches, newest finished album first, each with
    its why line (`Because you finished <album>: <connection>`)."""
    if not await conn.fetchval("SELECT to_regclass('graph_thread') IS NOT NULL"):
        return []
    by_rg = {c.release_group_id: c for c in candidates}
    out = []
    for r in await conn.fetch(THREADS):
        item = by_rg.get(r["to_release_group_id"])
        if item is not None:
            text = json.loads(r["connection"])["text"]
            because = f"Because you finished {r['finished']}: {text}"
            out.append((item, because, r["from_release_group_id"]))
    return out


async def load_profile(conn: asyncpg.Connection, name: str) -> Profile:
    # Named columns, not *: a pooled server keeps a prepared plan per query text, and a plan
    # for * breaks when a migration adds a column ("cached statement plan is invalid").
    r = await conn.fetchrow(
        """SELECT name, filters, goal_weights, zone_weights, composition, affinity_weight,
                  graph_weight
             FROM queue_profile WHERE name = $1""",
        name,
    )
    if r is None:
        raise UnknownProfileError(name)
    return Profile(
        name=r["name"],
        filters=json.loads(r["filters"]),
        goal_weights=json.loads(r["goal_weights"]),
        zone_weights=json.loads(r["zone_weights"]),
        composition=json.loads(r["composition"]),
        affinity_weight=float(r["affinity_weight"]),
        graph_weight=float(r["graph_weight"]),
    )


def play_url(item: Item) -> str:
    return "https://open.spotify.com/search/" + quote(f"{item.artist} {item.album}")


async def next_queue(
    conn: asyncpg.Connection,
    profile_name: str = "default",
    *,
    n: int | None = None,
    shuffle: bool = False,
    seed: int | None = None,
    exclude: frozenset[int] = frozenset(),
    now: datetime | None = None,
    more: bool = False,
) -> dict[str, object]:
    """The queue for a profile. `more` builds the next page the list scrolls on to (queue spec
    v20): the same mix of slots, leaving out every album in `exclude` (those already on the page)
    and their artists, with no pins and a seed of its own."""
    now = now or datetime.now(CHICAGO)
    p = await load_profile(conn, profile_name)
    comp = p.composition
    n = max(1, min(n or int(comp.get("n", config.DEFAULT_N)), config.MAX_N))
    if seed is None:
        seed = secrets.randbits(40) if shuffle else day_seed(p.name, now.astimezone(CHICAGO).date())
        if more:  # each page draws its own revisit order, thread and Tastebreaker
            seed += len(exclude)

    sc = await score_context(conn)
    entries, tags = sc.entries, sc.tags
    candidates = sc.score(p, now)

    # Revisits: albums with history, inside the profile's filters when it has any.
    in_profile = {
        int(e["release_group_id"])
        for e in entries
        if passes(e, p.filters, tags.get(int(e["release_group_id"]), ()))
    }
    why_by_group: dict[int, list[dict[str, object]]] = defaultdict(list)
    for c in candidates:
        why_by_group[c.release_group_id] = c.why
    pools: dict[str, list[tuple[float, Item]]] = defaultdict(list)
    for r in await conn.fetch(HISTORY, now):
        rg = int(r["release_group_id"])
        if p.filters and rg not in in_profile:
            continue
        d = due(r, now)
        if d is None:
            continue
        item = Item(
            release_group_id=rg,
            artist_id=int(r["artist_id"]),
            artist=r["artist"],
            album=r["title"],
            year=r["first_release_year"],
            primary_type=r["primary_type"],
            status="heard" if r["full_sessions"] else "started",
            tracks_heard=r["tracks_heard"],
            track_count=r["track_count"],
            pool=d.pool,
            reason=d.reason,
            tags=tags.get(rg, []),
        )
        pools[d.pool].append((d.overdue_days, item))
    ordered = {k: [i for _, i in sorted(v, key=lambda x: -x[0])] for k, v in pools.items()}

    pins = []
    for r in await conn.fetch(PINS):
        rg = int(r["release_group_id"])
        pins.append(
            Item(
                release_group_id=rg,
                artist_id=int(r["artist_id"]),
                artist=r["artist"],
                album=r["title"],
                year=r["first_release_year"],
                primary_type=r["primary_type"],
                status=r["status"],
                tracks_heard=r["tracks_heard"],
                track_count=r["track_count"],
                why=why_by_group.get(rg, []),
                tags=tags.get(rg, []),
            )
        )

    thread = int(comp.get("thread", 0))
    breaker = int(comp.get("tastebreaker", 0))
    shown_artists: set[int] = set()
    if more:
        pins = []
        shown_artists = {
            r[0]
            for r in await conn.fetch(
                "SELECT artist_id FROM release_group WHERE release_group_id = ANY($1::int[])",
                list(exclude),
            )
        }
    items = build(
        candidates,
        ordered,
        pins,
        n=n,
        revisit_share=float(comp.get("revisit_share", 0.2)),
        wildcard=int(comp.get("wildcard", 1)),
        seed=seed,
        shuffle=shuffle,
        exclude=exclude,
        thread=thread,
        threads=await thread_choices(conn, candidates) if thread else (),
        tastebreaker=breaker,
        breakers=await tastebreaker_choices(conn, candidates, seed) if breaker else (),
        shown_artists=shown_artists,
    )
    for item in items:
        item.connections = sc.lines.get(item.release_group_id, [])
    return {
        "profile": p.name,
        "generated_at": now.isoformat(timespec="seconds"),
        "seed": seed,
        "shuffle": shuffle,
        "candidates": len(candidates),
        "remaining": max(sum(1 for c in candidates if c.release_group_id not in exclude) - n, 0),
        "items": [render_item(i) for i in items],
    }


def render_item(i: Item) -> dict[str, object]:
    d = asdict(i)
    d.pop("artist_id")
    if i.slot == "new" and i.status == "heard":
        d["slot"] = "heard"  # a heard album in a walk back, not a new one
    d["why_line"] = (
        i.because if i.because
        else f"{i.pool} · {i.reason}" if i.pool
        else why_line(i.why) if i.why else "pinned"
    )  # fmt: skip
    d["why_lines"] = [] if i.pool or i.because else [why_part(w) for w in i.why]
    d["play_url"] = play_url(i)
    return d
