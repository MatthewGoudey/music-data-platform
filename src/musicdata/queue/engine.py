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
       q.bumped_until
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
       EXISTS (SELECT 1 FROM list_entry e
                WHERE e.release_group_id = s.release_group_id
                  AND e.review_status = 'accepted' AND e.resolve_status = 'resolved') AS on_list
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

    def score(self, p: Profile, now: datetime) -> list[Item]:
        return score_candidates(
            self.entries, p, list_sizes=self.list_sizes, lane_shares=self.lane_shares,
            tags=self.tags, now=now,
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
    return ScoreContext(entries, tags, list_sizes, lane_shares)


async def load_profile(conn: asyncpg.Connection, name: str) -> Profile:
    r = await conn.fetchrow("SELECT * FROM queue_profile WHERE name = $1", name)
    if r is None:
        raise UnknownProfileError(name)
    return Profile(
        name=r["name"],
        filters=json.loads(r["filters"]),
        goal_weights=json.loads(r["goal_weights"]),
        zone_weights=json.loads(r["zone_weights"]),
        composition=json.loads(r["composition"]),
        affinity_weight=float(r["affinity_weight"]),
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
) -> dict[str, object]:
    now = now or datetime.now(CHICAGO)
    p = await load_profile(conn, profile_name)
    comp = p.composition
    n = max(1, min(n or int(comp.get("n", config.DEFAULT_N)), config.MAX_N))
    if seed is None:
        seed = secrets.randbits(40) if shuffle else day_seed(p.name, now.astimezone(CHICAGO).date())

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
    )
    return {
        "profile": p.name,
        "generated_at": now.isoformat(timespec="seconds"),
        "seed": seed,
        "shuffle": shuffle,
        "candidates": len(candidates),
        "items": [render_item(i) for i in items],
    }


def render_item(i: Item) -> dict[str, object]:
    d = asdict(i)
    d.pop("artist_id")
    d["why_line"] = f"{i.pool} · {i.reason}" if i.pool else why_line(i.why) if i.why else "pinned"
    d["why_lines"] = [] if i.pool else [why_part(w) for w in i.why]
    d["play_url"] = play_url(i)
    return d
