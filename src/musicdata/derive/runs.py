"""Sittings go to the album they played; compilation listens outside one go to their home album
(ADR 0015 amendment, 2026-10-17).

A *run* is consecutive listens reported under one album (`reported_key`), each within 30 minutes
of the last; its artists may differ (a various-artists compilation). The per-listen rules
(derive/reported.py, derive/redirects.py) miss a run when the player's name for the album and
MusicBrainz's differ ("Cuttin' Grass - Vol. 2 (Cowboy Arms Sessions)" against "Cuttin' Grass
Vol. 2: The Cowboy Arms Sessions"), when two groups share the name (Twin Fantasy), and when the
album is a compilation. For each run of at least three titles:

- the candidates are mapped groups with a resolved tracklist that have the reported key, or that
  belong to one of the run's artists (or their current groups' artists) and whose key nearly
  matches it or extends it either way ("Tangle" and "Tangle EP");
- each candidate scores the run's titles its tracklist places (recording MBID, title key, then
  the loose keys sessions use); the one top scorer placing at least 80% of them wins, ties going
  to the group with the reported key, then to more recording matches, then to the group already
  holding more of the run;
- a compilation or box set wins only when the run also covers at least 80% of its own tracklist
  (Matt, 2026-10-17: that is listening to the compilation).

The winner's matched listens move to it and are marked `placed_by = 'run'`; the per-listen rules
skip them. Then a listen on a compilation or box set that no run placed goes to its track's home
album: the one album or EP (not a compilation, resolved tracklist) by the listen's artist or the
compilation's that holds the recording, by MBID, else by title among groups with no secondary
types; several go to the earliest; a tie or none leaves it (a rarity has no other home).
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import timedelta

import asyncpg

from musicdata.derive.reported import ELIGIBLE
from musicdata.derive.sessions import Play, TrackRef, _Matcher
from musicdata.resolve.unmapped import titles_match

GAP = timedelta(minutes=30)
MIN_TITLES = 3
COVER = 0.8

LISTENS = """
    SELECT l.listen_id, l.listened_at, l.artist_id, l.reported_key, l.recording_mbid::text AS mbid,
           l.norm_title, l.track_name, l.release_group_id, l.placed_by, cur.artist_id AS cur_artist
      FROM listen l
      LEFT JOIN release_group cur ON cur.release_group_id = l.release_group_id
     WHERE l.reported_key IS NOT NULL
     ORDER BY l.listened_at, l.listen_id
"""

GROUPS = """
    SELECT g.release_group_id, g.artist_id, g.norm_key, g.is_compilation OR g.is_box_set AS comp,
           t.track_count
      FROM release_group g
      JOIN release_group_tracklist t USING (release_group_id)
     WHERE g.mbid IS NOT NULL AND t.source <> 'unresolved' AND t.track_count > 0
"""

TRACKS = """
    SELECT release_group_id, position, recording_mbid::text AS mbid, norm_title, title
      FROM release_group_track WHERE release_group_id = ANY($1::int[])
"""


@dataclass
class Run:
    key: str
    listens: list[asyncpg.Record] = field(default_factory=list)

    @property
    def titles(self) -> set[str]:
        return {r["norm_title"] for r in self.listens}

    @property
    def artists(self) -> set[int]:
        return {r["artist_id"] for r in self.listens} | {
            r["cur_artist"] for r in self.listens if r["cur_artist"] is not None
        }


def split_runs(listens: list[asyncpg.Record]) -> list[Run]:
    runs: list[Run] = []
    for r in listens:
        last = runs[-1].listens[-1] if runs else None
        if (
            last is None
            or r["reported_key"] != last["reported_key"]
            or r["listened_at"] - last["listened_at"] > GAP
        ):
            runs.append(Run(r["reported_key"]))
        runs[-1].listens.append(r)
    return [run for run in runs if len(run.titles) >= MIN_TITLES]


def related(reported: str, key: str) -> bool:
    """A key the player's album name may stand for: nearly equal, or one extends the other."""
    return (
        titles_match(reported, key)
        or key.startswith(reported + " ")
        or reported.startswith(key + " ")
    )


def candidates(run: Run, by_key: dict, by_artist: dict) -> set[int]:
    found = {g["release_group_id"] for g in by_key.get(run.key, ())}
    for a in run.artists:
        found |= {
            g["release_group_id"] for g in by_artist.get(a, ()) if related(run.key, g["norm_key"])
        }
    return found


def _play(r: asyncpg.Record) -> Play:
    return Play(r["listened_at"], r["mbid"], r["norm_title"], r["track_name"])


def choose(
    run: Run, cands: set[int], groups: dict[int, asyncpg.Record], matchers: dict[int, _Matcher]
) -> tuple[int, list[int]] | None:
    """(winner, its listen ids) when one candidate places at least 80% of the run's titles."""
    titles = run.titles
    scored = []
    for rg in cands:
        m = matchers.get(rg)
        if m is None:
            continue
        placed = {r["listen_id"]: m.position(_play(r)) for r in run.listens}
        heard_titles = {r["norm_title"] for r in run.listens if placed[r["listen_id"]] is not None}
        if len(heard_titles) < COVER * len(titles):
            continue
        g = groups[rg]
        positions = {p for p in placed.values() if p is not None}
        if g["comp"] and len(positions) < COVER * g["track_count"]:
            continue
        exact = g["norm_key"] == run.key  # Sgt. Pepper over "Sgt: Peppers … Rockband Mixes 2009"
        by_mbid = sum(1 for r in run.listens if r["mbid"] and r["mbid"] in m.by_mbid)
        held = sum(1 for r in run.listens if r["release_group_id"] == rg)
        ids = [i for i, p in placed.items() if p is not None]
        scored.append(((len(heard_titles), exact, by_mbid, held), rg, ids))
    if not scored:
        return None
    scored.sort(key=lambda s: s[0], reverse=True)
    if len(scored) > 1 and scored[0][0] == scored[1][0]:
        return None  # two groups play it equally well: leave it
    return scored[0][1], scored[0][2]


async def place_runs(conn: asyncpg.Connection) -> dict[str, int]:
    """Decide every run, then apply: moves, marks, and unmarks of listens no run places now."""
    runs = split_runs(await conn.fetch(LISTENS))
    groups = {g["release_group_id"]: g for g in await conn.fetch(GROUPS)}
    by_key: dict[str, list] = defaultdict(list)
    by_artist: dict[int, list] = defaultdict(list)
    for g in groups.values():
        by_key[g["norm_key"]].append(g)
        by_artist[g["artist_id"]].append(g)
    cands = {id(run): candidates(run, by_key, by_artist) for run in runs}
    wanted = sorted(set().union(*cands.values())) if cands else []
    tracks: dict[int, list[TrackRef]] = defaultdict(list)
    for t in await conn.fetch(TRACKS, wanted):
        tracks[t["release_group_id"]].append(
            TrackRef(t["position"], t["mbid"], t["norm_title"], t["title"])
        )
    matchers = {rg: _Matcher(ts) for rg, ts in tracks.items()}

    placement: dict[int, int] = {}
    runs_placed = 0
    for run in runs:
        picked = choose(run, cands[id(run)], groups, matchers)
        if picked:
            runs_placed += 1
            placement.update(dict.fromkeys(picked[1], picked[0]))
    async with conn.transaction():
        await conn.execute(
            """CREATE TEMP TABLE run_place (listen_id BIGINT PRIMARY KEY, rg INT NOT NULL)
               ON COMMIT DROP"""
        )
        await conn.copy_records_to_table("run_place", records=list(placement.items()))
        moved = await conn.fetch(
            """WITH old AS (
                   SELECT l.listen_id, l.release_group_id AS was, p.rg
                     FROM listen l JOIN run_place p USING (listen_id)
                    WHERE l.release_group_id IS DISTINCT FROM p.rg OR l.placed_by IS NULL
               )
               UPDATE listen l SET release_group_id = o.rg, placed_by = 'run'
                 FROM old o WHERE l.listen_id = o.listen_id
            RETURNING o.was, o.rg"""
        )
        released = await conn.execute(
            """UPDATE listen l SET placed_by = NULL
                WHERE l.placed_by = 'run'
                  AND NOT EXISTS (SELECT 1 FROM run_place p WHERE p.listen_id = l.listen_id)"""
        )
        touched = {r["rg"] for r in moved if r["was"] != r["rg"]} | {
            r["was"] for r in moved if r["was"] is not None and r["was"] != r["rg"]
        }
        await conn.execute(
            "UPDATE release_group SET updated_at = now() WHERE release_group_id = ANY($1::int[])",
            list(touched),
        )
    return {
        "runs": len(runs),
        "runs_placed": runs_placed,
        "listens_moved": sum(1 for r in moved if r["was"] != r["rg"]),
        "listens_released": int(released.split()[-1]),
    }


REAL = ELIGIBLE  # an album or EP with a resolved tracklist, never a compilation or box set

LEAVE_COMPILATIONS = f"""
    WITH away AS (
        SELECT l.listen_id, l.release_group_id AS from_rg, l.recording_mbid, l.norm_title,
               l.artist_id, c.artist_id AS comp_artist
          FROM listen l
          JOIN release_group c ON c.release_group_id = l.release_group_id
         WHERE (c.is_compilation OR c.is_box_set) AND l.placed_by IS NULL
    ), cand AS (
        SELECT a.listen_id, a.from_rg, g.release_group_id AS to_rg, g.first_release_year, 1 AS how
          FROM away a
          JOIN release_group_track t ON t.recording_mbid = a.recording_mbid
          JOIN release_group g ON g.release_group_id = t.release_group_id
         WHERE a.recording_mbid IS NOT NULL AND g.artist_id IN (a.artist_id, a.comp_artist)
           AND {REAL}
        UNION
        SELECT a.listen_id, a.from_rg, g.release_group_id, g.first_release_year, 2
          FROM away a
          JOIN release_group g ON g.artist_id IN (a.artist_id, a.comp_artist)
          JOIN release_group_track t ON t.release_group_id = g.release_group_id
                                    AND t.norm_title = a.norm_title
         WHERE coalesce(cardinality(g.secondary_types), 0) = 0 AND {REAL}
    ), best AS (  -- by MBID before by title, then the earliest year; ties name no home
        SELECT DISTINCT ON (listen_id) listen_id, from_rg, to_rg,
               count(*) OVER (PARTITION BY listen_id, how, first_release_year) AS tied,
               how, first_release_year
          FROM cand
         ORDER BY listen_id, how, first_release_year NULLS LAST
    ), moved AS (
        UPDATE listen l SET release_group_id = b.to_rg
          FROM best b
         WHERE l.listen_id = b.listen_id AND b.tied = 1
        RETURNING b.from_rg AS a, b.to_rg AS b
    ), touched AS (
        SELECT a AS rg FROM moved UNION SELECT b FROM moved
    ), bumped AS (
        UPDATE release_group SET updated_at = now()
         WHERE release_group_id IN (SELECT rg FROM touched)
        RETURNING 1
    )
    SELECT (SELECT count(*) FROM moved) AS listens
"""


async def leave_compilations(conn: asyncpg.Connection) -> dict[str, int]:
    """Compilation listens no run placed go to their home album. One transaction."""
    async with conn.transaction():
        row = await conn.fetchrow(LEAVE_COMPILATIONS)
    return {"listens_left_compilations": row["listens"]}
