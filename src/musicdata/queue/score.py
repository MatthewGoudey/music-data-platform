"""Scoring candidates (docs/QUEUE_SPEC.md section 7). Pure functions over loaded rows.

base(g)  = Σ over the entries of g that pass the profile's filters:
           list weight × goal weight × tier × position factor × start here × zone weight
score(g) = base(g) × affinity × lane gap × bump
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime

from musicdata.queue import config


@dataclass(frozen=True)
class Profile:
    name: str
    filters: Mapping[str, object] = field(default_factory=dict)
    goal_weights: Mapping[str, float] = field(default_factory=dict)
    zone_weights: Mapping[str, float] = field(default_factory=dict)
    composition: Mapping[str, float] = field(default_factory=dict)
    affinity_weight: float = 0.2


@dataclass
class Item:
    """One album in the queue, or a candidate for it."""

    release_group_id: int
    artist_id: int
    artist: str
    album: str
    year: int | None
    primary_type: str | None
    status: str
    tracks_heard: int | None = None
    track_count: int | None = None
    score: float | None = None
    why: list[dict[str, object]] = field(default_factory=list)
    slot: str = ""
    pool: str | None = None
    reason: str | None = None
    tags: list[str] = field(default_factory=list)


def tier(priority: str | None, default_priority: str | None) -> float:
    return config.TIER.get(priority or default_priority or config.DEFAULT_TIER, 2.0)


def position_factor(position: int | None, size: int, ranked: bool) -> float:
    """Ranked lists: #1 → 1.0, last → 0.5. Unranked lists: 1.0."""
    if not ranked or position is None or size <= 1:
        return 1.0
    spread = 1.0 - config.RANKED_LAST_FACTOR
    return 1.0 - spread * min(max(position - 1, 0), size - 1) / (size - 1)


def _words(text: object) -> set[str]:
    return {w.strip().lower() for w in str(text or "").replace(";", ",").split(",") if w.strip()}


def passes(e: Mapping[str, object], filters: Mapping[str, object], tags: Iterable[str]) -> bool:
    """An entry passes every filter the profile sets (QUEUE_SPEC section 10)."""
    checks = {
        "lists": e["slug"],
        "goals": e["goal"],
        "zones": e["zone"],
        "layers": e["layer"],
        "lanes": e["lane_id"],
        "primary_types": e["primary_type"],
    }
    for key, value in checks.items():
        allowed = filters.get(key)
        if allowed and value not in allowed:
            return False
    if decades := filters.get("decades"):
        year = e["first_release_year"] or e["year"]
        if year is None or (year // 10) * 10 not in {int(d) for d in decades}:
            return False
    if wanted := filters.get("descriptors_any"):
        if not _words(e["descriptors"]) & {w.lower() for w in wanted}:
            return False
    if wanted := filters.get("tags"):
        if not set(tags) & set(wanted):
            return False
    return True


def entry_weight(e: Mapping[str, object], p: Profile, list_size: int) -> float:
    return (
        float(e["weight"])
        * float(p.goal_weights.get(e["goal"], 1.0))
        * tier(e["priority"], e["default_priority"])
        * position_factor(e["position"], list_size, bool(e["ranked"]))
        * (config.START_HERE if e["start_here"] else 1.0)
        * float(p.zone_weights.get(e["zone"] or "none", 1.0))
    )


def affinity(artist_listens: int, weight: float) -> float:
    cap = math.log(1 + config.AFFINITY_LISTENS_CAP)
    return 1 + weight * min(math.log(1 + artist_listens) / cap, 1.0)


def lane_gap(lane_heard_share: float | None) -> float:
    """1 + 0.3 × (1 − heard share of the album's atlas lane); 1 off the atlas."""
    return 1.0 if lane_heard_share is None else 1 + config.LANE_GAP * (1 - lane_heard_share)


def why_of(e: Mapping[str, object]) -> dict[str, object]:
    return {
        "list": e["slug"],
        "label": config.LIST_LABELS.get(e["slug"], e["list_name"]),
        "position": e["position"] if e["ranked"] else None,
        "priority": e["priority"],
        "lane": e["lane_id"],
        "lane_name": e["lane_name"],
        "zone": e["zone"],
        "start_here": bool(e["start_here"]),
    }


def why_part(w: Mapping[str, object]) -> str:
    """One list's part of the why line: `V Atlas · C1 Indie twang · Essential · start here`,
    `Rolling Stone #2` or `1001 Albums`."""
    if w["lane"]:
        bits = [w["label"], f"{w['lane']} {w['lane_name'] or ''}".strip(), w["priority"]]
        bits.append("start here" if w["start_here"] else None)
        return " · ".join(str(b) for b in bits if b)
    return f"{w['label']} #{w['position']}" if w["position"] else str(w["label"])


def why_line(why: list[dict[str, object]]) -> str:
    """Every list the album is on (spec v15): `Rolling Stone #2 · 1001 Albums`."""
    return " · ".join(why_part(w) for w in why)


def score_candidates(
    entries: list[Mapping[str, object]],
    p: Profile,
    *,
    list_sizes: Mapping[int, int],
    lane_shares: Mapping[str, float],
    tags: Mapping[int, list[str]],
    now: datetime,
) -> list[Item]:
    """Unheard and started albums that pass the profile, best first (ties by id).
    Hidden, snoozed and pinned albums are left out (pins go first on their own)."""
    items: dict[int, Item] = {}
    base: dict[int, float] = {}
    meta: dict[int, Mapping[str, object]] = {}
    for e in entries:
        rg = int(e["release_group_id"])
        if e["status"] == "heard" or e["hidden"] or e["pinned"]:
            continue
        if e["snoozed_until"] is not None and e["snoozed_until"] > now:
            continue
        if not passes(e, p.filters, tags.get(rg, ())):
            continue
        base[rg] = base.get(rg, 0.0) + entry_weight(e, p, list_sizes[e["list_id"]])
        if rg not in items:
            items[rg] = item_of(e, tags.get(rg, []))
            meta[rg] = e
        item = items[rg]
        item.why.append(why_of(e))
        if e["slug"] == "v_atlas":
            meta[rg] = e
    for rg, item in items.items():
        e = meta[rg]
        item.why.sort(key=lambda w: (w["list"] != "v_atlas", w["position"] or 10**6))
        bumped = e["bumped_until"] is not None and e["bumped_until"] > now
        item.score = round(
            base[rg]
            * affinity(int(e["artist_listens"]), p.affinity_weight)
            * lane_gap(lane_shares.get(e["lane_id"]) if e["lane_id"] else None)
            * (config.BUMP if bumped else 1.0),
            3,
        )
    return sorted(items.values(), key=lambda i: (-(i.score or 0), i.release_group_id))


def item_of(e: Mapping[str, object], tags: list[str]) -> Item:
    """The album as the queue shows it: MusicBrainz's title when the group is mapped,
    the list's spelling otherwise (a streamed "… (2015 Remaster)" stays out of sight)."""
    return Item(
        release_group_id=int(e["release_group_id"]),
        artist_id=int(e["artist_id"]),
        artist=str(e["artist"] if e["mapped"] else e["raw_artist"]),
        album=str(e["title"] if e["mapped"] else e["raw_album"]),
        year=e["first_release_year"] or e["year"],
        primary_type=e["primary_type"],
        status=str(e["status"]),
        tracks_heard=e["tracks_heard"],
        track_count=e["track_count"],
        tags=list(tags),
    )
