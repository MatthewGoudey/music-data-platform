"""The reconcile job: the whole ListenBrainz history against the listen table, second by second.

ListenBrainz's `listen-count` endpoint can drift from the history it serves (on 2026-10-12 it
fell ~300 below it), and ingest only ever adds, so a listen deleted upstream stays here. A
weekly run pages every listen, records the paged total and the stored listens ListenBrainz no
longer has; `listens_beyond_listenbrainz` in dq reads that. `delete` removes those listens
and bumps their release groups so the next derive rebuilds their sessions.

Listens are compared per second: a second where the database holds more listens than
ListenBrainz has extras. Within that second the rows whose title ListenBrainz lacks are the
extras; when titles cannot single them out (a repeat in the same second) they are reported
as ambiguous and never deleted.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime

from musicdata.clients.listenbrainz import ListenBrainzClient
from musicdata.config import get_settings
from musicdata.db import connection
from musicdata.identity import title_key
from musicdata.jobs.runs import JobFn, RunContext
from musicdata.log import get_logger

log = get_logger(__name__)

LISTED_MAX = 200  # extras written into the run's notes


@dataclass(frozen=True)
class Stored:
    listen_id: int
    ts: int
    norm_title: str


@dataclass
class Diff:
    extra: list[int] = field(default_factory=list)  # listen_ids ListenBrainz no longer has
    ambiguous: list[int] = field(default_factory=list)  # rows at a second with an extra
    missing: int = 0  # listens ListenBrainz has that the database lacks


def compare(upstream: dict[int, Counter[str]], stored: list[Stored]) -> Diff:
    """`upstream` maps each second to the title keys ListenBrainz holds there."""
    by_second: dict[int, list[Stored]] = defaultdict(list)
    for s in stored:
        by_second[s.ts].append(s)
    diff = Diff()
    for ts, rows in by_second.items():
        titles = upstream.get(ts, Counter())
        surplus = len(rows) - sum(titles.values())
        if surplus <= 0:
            continue
        left = Counter(titles)
        unmatched = []
        for r in rows:
            if left[r.norm_title] > 0:
                left[r.norm_title] -= 1
            else:
                unmatched.append(r.listen_id)
        # A surplus row sharing a title with a kept one could be either: never guess.
        clashes = any(titles[r.norm_title] for r in rows if r.listen_id in unmatched)
        if len(unmatched) == surplus and not clashes:
            diff.extra.extend(unmatched)
        else:
            diff.ambiguous.extend(r.listen_id for r in rows)
    for ts, titles in upstream.items():
        diff.missing += max(sum(titles.values()) - len(by_second.get(ts, [])), 0)
    return diff


def reconcile(*, delete: bool = False) -> JobFn:
    async def _run(ctx: RunContext) -> None:
        settings = get_settings()
        upstream: dict[int, Counter[str]] = defaultdict(Counter)
        seen: set[tuple[int, str, str]] = set()
        newest: int | None = None
        pages = 0
        async with ListenBrainzClient(
            settings.listenbrainz_user, user_agent=settings.musicbrainz_user_agent
        ) as lb:
            async for page in lb.pages_back_to(None):
                pages += 1
                for raw in page:
                    meta = raw.get("track_metadata") or {}
                    ts, track = raw.get("listened_at"), meta.get("track_name") or ""
                    if ts is None:
                        continue
                    # max_ts paging returns listens sharing the boundary second twice
                    key = (int(ts), meta.get("artist_name") or "", track)
                    if key in seen:
                        continue
                    seen.add(key)
                    upstream[int(ts)][title_key(track)] += 1
                    newest = max(newest or 0, int(ts))
                if pages % 25 == 0:
                    log.info("reconcile progress", extra={"pages": pages, "listens": len(seen)})
        if newest is None:
            raise RuntimeError("ListenBrainz returned no listens")

        # Listens ingested after paging began are above `newest`; leave them out of both sides.
        async with connection(ctx.pool) as conn:
            rows = await conn.fetch(
                """SELECT listen_id, extract(epoch FROM listened_at)::bigint AS ts, norm_title
                     FROM listen WHERE listened_at <= to_timestamp($1)""",
                newest,
            )
        stored = [Stored(r["listen_id"], r["ts"], r["norm_title"]) for r in rows]
        diff = compare(upstream, stored)

        listed = []
        async with connection(ctx.pool) as conn:
            if diff.extra:
                listed = [
                    {**dict(r), "listened_at": r["listened_at"].isoformat()}
                    for r in await conn.fetch(
                        """SELECT listen_id, listened_at, artist_name, track_name, release_name
                             FROM listen WHERE listen_id = ANY($1::bigint[])
                            ORDER BY listened_at LIMIT $2""",
                        diff.extra,
                        LISTED_MAX,
                    )
                ]
            deleted = 0
            if delete and diff.extra:
                async with conn.transaction():
                    await conn.execute(
                        """UPDATE release_group SET updated_at = now()
                            WHERE release_group_id IN (SELECT release_group_id FROM listen
                                                        WHERE listen_id = ANY($1::bigint[]))""",
                        diff.extra,
                    )
                    deleted = int(
                        (
                            await conn.execute(
                                "DELETE FROM listen WHERE listen_id = ANY($1::bigint[])",
                                diff.extra,
                            )
                        ).split()[-1]
                    )
                log.info("reconcile deleted listens", extra={"deleted": deleted})
            db_count = await conn.fetchval(
                "SELECT count(*) FROM listen WHERE listened_at <= to_timestamp($1)", newest
            )

        ctx.rows = deleted
        ctx.notes.update(
            pages=pages,
            newest=datetime.fromtimestamp(newest, UTC).isoformat(),
            listenbrainz_paged_count=len(seen),
            database_count=db_count,
            extra=len(diff.extra) - deleted,
            extra_found=len(diff.extra),
            deleted=deleted,
            ambiguous=len(diff.ambiguous),
            missing=diff.missing,
            extra_listens=listed,
        )

    return _run
