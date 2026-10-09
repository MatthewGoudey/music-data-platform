"""Hand-checked track counts (`seeds/manual_tracklists.csv`) override MusicBrainz.

The rows came from the old pipeline, where Matt verified each count by hand (exported
2026-10-09). Each finds its release group the way `lists resolve` finds a known album and
stores `source = 'manual'`, which the resolve job never overwrites. When two rows land on
one group with different counts, the row without edition words wins ("Magnolia Electric
Co." over "… (Deluxe Edition)"); a tie that still disagrees is skipped and reported.
Idempotent: a group already holding the count is left alone.
"""

from __future__ import annotations

import csv
from collections import defaultdict
from pathlib import Path

import asyncpg

from musicdata.identity import has_edition_marker, norm_key
from musicdata.lists.match import group_key
from musicdata.lists.resolve import Album, Known

SEED = Path("seeds/manual_tracklists.csv")


def read_manual(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as f:
        return [r for r in csv.DictReader(f) if r.get("track_count", "").strip().isdigit()]


def pick(rows: list[dict[str, str]]) -> dict[str, str] | None:
    """The row to apply among rows that found the same group, or None on a real conflict."""
    if len({r["track_count"] for r in rows}) == 1:
        return rows[0]
    plain = [r for r in rows if not has_edition_marker(r["album"])]
    if plain and len({r["track_count"] for r in plain}) == 1:
        return plain[0]
    return None


async def apply_manual_tracklists(conn: asyncpg.Connection, path: Path = SEED) -> dict[str, int]:
    if not path.exists():
        return {"seed_missing": 1}
    known = await Known.load(conn)
    found: dict[int, list[dict[str, str]]] = defaultdict(list)
    not_found = 0
    for r in read_manual(path):
        album = Album(r["artist"], r["album"], norm_key(r["artist"]), group_key(r["album"]), None)
        rg_id = known.find(album)
        if rg_id is None:
            not_found += 1
        else:
            found[rg_id].append(r)
    applied = unchanged = conflicting = 0
    for rg_id, rows in found.items():
        row = pick(rows)
        if row is None:
            conflicting += 1
            continue
        count = int(row["track_count"])
        current = await conn.fetchrow(
            "SELECT source, track_count FROM release_group_tracklist WHERE release_group_id = $1",
            rg_id,
        )
        if current and current["source"] == "manual" and current["track_count"] == count:
            unchanged += 1
            continue
        async with conn.transaction():
            await conn.execute(
                """INSERT INTO release_group_tracklist
                          (release_group_id, track_count, source, resolved_at, note)
                   VALUES ($1, $2, 'manual', now(), $3)
                   ON CONFLICT (release_group_id) DO UPDATE
                      SET track_count = EXCLUDED.track_count, source = 'manual',
                          resolved_at = now(), note = EXCLUDED.note""",
                rg_id,
                count,
                f"hand-checked: {row['note']}".strip(),
            )
            await conn.execute(  # derive rebuilds the sessions of a group that changed
                "UPDATE release_group SET updated_at = now() WHERE release_group_id = $1", rg_id
            )
        applied += 1
    return {
        "applied": applied,
        "unchanged": unchanged,
        "not_found": not_found,
        "conflicting": conflicting,
    }
