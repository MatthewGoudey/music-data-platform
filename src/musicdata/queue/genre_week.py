"""Genre of the week (queue spec section 10a, v23): each Monday (Chicago) the system picks a genre
from the list albums' genres, rotating near / adjacent / stretch by ISO week, and rewrites the
`genre_week` generated list that the `genre-week` profile reads. Matt can reroll it.

- near: a genre among the albums Matt finished in the last year (its share of them);
- adjacent: one he hardly finished that turns up on the same albums as his top genres;
- stretch: one he never finished that seldom shares an album with his top genres.

A genre qualifies with at least GENRE_WEEK_MIN_UNHEARD unheard list albums, at most
GENRE_WEEK_MAX_ALBUMS list albums in all (umbrellas such as "rock" stay out), and no week of its
own in the last GENRE_WEEK_NO_REPEAT weeks. The week seeds the draw, so a rerun picks the same.
"""

from __future__ import annotations

import json
import random
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import asyncpg

from musicdata.db import connection
from musicdata.graph.generated import write_list
from musicdata.jobs.runs import JobFn, RunContext
from musicdata.queue import config
from musicdata.queue.genres import index_rows, raw_rows, title

KINDS = ("near", "adjacent", "stretch")
CHICAGO = ZoneInfo("America/Chicago")

LIST_ALBUMS = """
WITH sizes AS (SELECT list_id, count(*) AS size FROM list_entry GROUP BY list_id)
SELECT e.release_group_id AS rg, l.name AS list, min(e.position) AS position, s.size
  FROM list_entry e JOIN list l USING (list_id) JOIN sizes s USING (list_id)
 WHERE e.release_group_id IS NOT NULL AND l.source NOT LIKE 'generated:%'
 GROUP BY e.release_group_id, l.name, s.size
"""
HEARD = "SELECT release_group_id FROM release_group_stat WHERE full_sessions > 0"
FINISHED = """
SELECT DISTINCT release_group_id FROM album_session
 WHERE session_type = 'full' AND started_at > now() - make_interval(days => $1)
"""


def week_start(now: datetime | None = None) -> date:
    d = (now or datetime.now(CHICAGO)).astimezone(CHICAGO).date()
    return d - timedelta(days=d.weekday())


def kind_for(week: date) -> str:
    return KINDS[week.isocalendar().week % len(KINDS)]


@dataclass
class Index:
    """The list albums, their genres, and what Matt has heard and finished."""

    genres: dict[int, set[str]]  # list album -> genre keys
    names: dict[str, str]
    lists: dict[int, list[tuple[str, int | None, int]]]  # album -> (list, position, size)
    heard: set[int]
    finished: dict[str, int] = field(default_factory=dict)  # key -> albums finished this year
    finished_total: int = 0

    def albums(self, key: str) -> list[int]:
        return [rg for rg, ks in self.genres.items() if key in ks]

    def totals(self) -> tuple[Counter[str], Counter[str]]:
        total, unheard = Counter(), Counter()
        for rg, ks in self.genres.items():
            for k in ks:
                total[k] += 1
                if rg not in self.heard:
                    unheard[k] += 1
        return total, unheard

    def related(self, key: str, n: int = 5) -> list[tuple[str, int]]:
        """Genres that share the most list albums with this one."""
        shared = Counter(k for rg in self.albums(key) for k in self.genres[rg] if k != key)
        return shared.most_common(n)


async def load_index(conn: asyncpg.Connection) -> Index:
    lists: dict[int, list[tuple[str, int | None, int]]] = defaultdict(list)
    for r in await conn.fetch(LIST_ALBUMS):
        lists[r["rg"]].append((r["list"], r["position"], int(r["size"])))
    by_rg, names = index_rows(await raw_rows(conn, list(lists)))
    heard = {r[0] for r in await conn.fetch(HEARD)}
    finished = [r[0] for r in await conn.fetch(FINISHED, config.GENRE_WEEK_AFFINITY_DAYS)]
    f_genres, f_names = index_rows(await raw_rows(conn, finished))
    idx = Index({rg: ks for rg, ks in by_rg.items() if rg in lists}, names | f_names, lists, heard)
    idx.finished = Counter(k for ks in f_genres.values() for k in ks)
    idx.finished_total = len(finished)
    return idx


def candidates(idx: Index, kind: str, exclude: set[str]) -> list[tuple[str, dict]]:
    """Qualifying genres of one kind, best first, each with the numbers behind it."""
    total, unheard = idx.totals()
    ok = [k for k in total if unheard[k] >= config.GENRE_WEEK_MIN_UNHEARD
          and total[k] <= config.GENRE_WEEK_MAX_ALBUMS and k not in exclude]  # fmt: skip
    umbrella = {k for k in total if total[k] > config.GENRE_WEEK_MAX_ALBUMS}
    top = [k for k, n in idx.finished.most_common() if n >= 2 and k not in umbrella]
    top = top[: config.GENRE_WEEK_TOP]

    def with_top(k: str) -> tuple[float, str | None, int]:
        """Share of k's list albums that carry a top genre, and the top genre it meets most."""
        albums = idx.albums(k)
        meets = Counter(t for rg in albums for t in idx.genres[rg] if t in top and t != k)
        hit = sum(1 for rg in albums if idx.genres[rg] & set(top) - {k})
        best = meets.most_common(1)
        return hit / len(albums), (best[0][0] if best else None), (best[0][1] if best else 0)

    out = []
    for k in ok:
        base = {"unheard": unheard[k], "albums": total[k], "finished": idx.finished.get(k, 0)}
        if kind == "near" and idx.finished.get(k, 0) >= 2:
            out.append((k, base, idx.finished[k]))
        elif kind in ("adjacent", "stretch") and idx.finished.get(k, 0) <= (
            1 if kind == "adjacent" else 0
        ):
            share, meet, n = with_top(k)
            if kind == "adjacent" and share >= config.GENRE_WEEK_ADJACENT_SHARE and meet:
                out.append((k, base | {"with": meet, "shared": n}, share))
            elif kind == "stretch" and share < config.GENRE_WEEK_STRETCH_SHARE:
                out.append((k, base, unheard[k]))
    out.sort(key=lambda x: (-x[2], x[0]))
    # a stretch draws from every qualifying genre: ranked by size, the biggest are all metal
    keep = len(out) if kind == "stretch" else config.GENRE_WEEK_POOL
    return [(k, why) for k, why, _ in out[:keep]]


def choose(
    idx: Index, week: date, exclude: set[str], kind: str | None = None
) -> tuple[str, str, dict] | None:
    """(key, kind, why) for the week: its own kind first, then the others."""
    first = kind or kind_for(week)
    for k in (first, *[x for x in KINDS if x != first]):
        pool = candidates(idx, k, exclude)
        if pool:
            key, why = random.Random(f"{week.isoformat()}:{len(exclude)}").choice(pool)
            return key, k, why
    return None


def why_line(kind: str, why: dict, names: dict[str, str]) -> str:
    if kind == "near":
        n = why.get("finished", 0)
        return f"You finished {n} album{'s' if n != 1 else ''} in this genre in the last year."
    if kind == "adjacent":
        other = names.get(why.get("with") or "", why.get("with") or "")
        return (
            f"Turns up on {why.get('shared', 0)} list albums alongside {other}, a genre you play."
        )
    return "A stretch: you haven't finished an album in this genre in the last year."


def entries(idx: Index, key: str) -> list[tuple[int, str]]:
    """The genre's list albums for the generated list, the most listed and highest placed first."""

    def place(x: tuple[str, int | None, int]) -> float:
        return 0.5 if x[1] is None else x[1] / max(x[2], 1)  # an unranked list: mid-list

    def rank(rg: int) -> tuple[int, float]:
        on = idx.lists[rg]
        return (-len(on), min(place(x) for x in on))

    out = []
    for rg in sorted(idx.albums(key), key=rank):
        on = sorted(idx.lists[rg], key=place)
        short = [(name.split(":")[0], p) for name, p, _ in on[:3]]  # "Rate Your Music #610"
        note = " · ".join(name if p is None else f"{name} #{p}" for name, p in short)
        out.append((rg, note))
    return out


async def recent_genres(conn: asyncpg.Connection, week: date) -> set[str]:
    rows = await conn.fetch(
        """SELECT genre FROM genre_week
            WHERE week_start >= $1::date - make_interval(weeks => $2) AND week_start < $1""",
        week,
        config.GENRE_WEEK_NO_REPEAT,
    )
    return {r["genre"] for r in rows}


async def write_week(conn: asyncpg.Connection, idx: Index, week: date, key: str) -> int:
    name = title(idx.names.get(key, key))
    return await write_list(conn, "genre_week", entries(idx, key), f"genre:{key}",
                            name=f"Genre of the week: {name}")  # fmt: skip


async def pick(
    conn: asyncpg.Connection, *, reroll: bool = False, now: datetime | None = None
) -> dict:
    """Make sure this week has a genre (or replace it on a reroll) and its list is current."""
    week = week_start(now)
    idx = await load_index(conn)
    row = await conn.fetchrow("SELECT * FROM genre_week WHERE week_start = $1", week)
    if row is not None and not reroll:
        n = await write_week(conn, idx, week, row["genre"])
        return {
            "week": week,
            "genre": row["genre"],
            "kind": row["kind"],
            "entries": n,
            "new": False,
        }
    exclude = await recent_genres(conn, week)
    rerolled = list(row["rerolled"]) + [row["genre"]] if row is not None else []
    choice = choose(idx, week, exclude | set(rerolled), row["kind"] if row is not None else None)
    if choice is None:
        raise LookupError("no genre qualifies this week")
    key, kind, why = choice
    async with conn.transaction():
        await conn.execute(
            """INSERT INTO genre_week (week_start, genre, name, kind, picked_by, why, rerolled)
               VALUES ($1, $2, $3, $4, $5, $6::jsonb, $7)
               ON CONFLICT (week_start) DO UPDATE
                  SET genre = $2, name = $3, kind = $4, picked_by = $5, picked_at = now(),
                      why = $6::jsonb, rerolled = $7""",
            week, key, idx.names.get(key, key), kind, "reroll" if reroll else "auto",
            json.dumps(why), rerolled,
        )  # fmt: skip
        n = await write_week(conn, idx, week, key)
    return {"week": week, "genre": key, "kind": kind, "entries": n, "new": True}


def genre_week_job() -> JobFn:
    async def _run(ctx: RunContext) -> None:
        async with connection(ctx.pool) as conn:
            out = await pick(conn)
        ctx.rows = int(out["entries"])
        ctx.notes.update(out)

    return _run
