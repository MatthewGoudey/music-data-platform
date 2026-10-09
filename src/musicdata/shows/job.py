"""The shows job: Oh My Rockness, then Ticketmaster → venue, show, show_source, show_artist.

Each run reads the venue index, then the pages of venues that had upcoming shows last
time plus a rotating slice of the rest (`--sweep` reads all), and fetches a show page
only for show ids not stored yet. A show missing from three crawls of its venue is
cancelled. Performers resolve to artists with the identity rules: an alias seen at
ingest, then the key (the one artist you listen to when several share a name); "A & B"
splits only when the whole name resolves to nobody and each part does.
"""

from __future__ import annotations

from datetime import UTC, datetime

import asyncpg

from musicdata.clients.omr import OhMyRockness
from musicdata.clients.ticketmaster import Ticketmaster
from musicdata.config import get_settings
from musicdata.db import connection
from musicdata.identity import clean_performer, non_artist_event, norm_key, split_lineup
from musicdata.jobs.runs import JobFn, RunContext
from musicdata.log import get_logger
from musicdata.shows.parse import Performer, Show, Venue

log = get_logger(__name__)

ROTATE = 85  # inactive venues re-checked per run: all ~600 within a week
CANCEL_AFTER = 3


async def resolve_performer(conn: asyncpg.Connection, name: str) -> int | None:
    rows = await conn.fetch(
        """SELECT a.artist_id, coalesce(s.listens, 0) AS listens
             FROM artist_alias x JOIN artist a USING (artist_id)
             LEFT JOIN artist_stat s USING (artist_id)
            WHERE x.raw_name = $1
            ORDER BY listens DESC""",
        name,
    )
    if not rows:
        key = norm_key(name)
        if not key:
            return None
        rows = await conn.fetch(
            """SELECT a.artist_id, coalesce(s.listens, 0) AS listens
                 FROM artist a LEFT JOIN artist_stat s USING (artist_id)
                WHERE a.norm_key = $1
                ORDER BY a.mbid IS NULL DESC, listens DESC""",
            key,
        )
    if len(rows) == 1:
        return rows[0]["artist_id"]
    heard = [r for r in rows if r["listens"] > 0]
    return heard[0]["artist_id"] if len(heard) == 1 else None


async def _lineup(conn: asyncpg.Connection, show: Show) -> list[dict]:
    """Show artists in order: cleaned, resolved, "A & B" split only when that resolves.
    A listing without structured performers is split from its title, unless the whole
    title is an artist ("Earth, Wind & Fire")."""
    performers = show.performers
    if not performers:
        whole = clean_performer(show.title)[0]
        if not non_artist_event(show.title) and await resolve_performer(conn, whole):
            performers = [Performer(name=whole)]
        else:
            performers = [Performer(name=n) for n in split_lineup(show.title)]
    out: list[dict] = []
    for p in performers:
        clean, note = clean_performer(p.name)
        artist_id = await resolve_performer(conn, clean)
        parts = [clean]
        if artist_id is None and (" & " in clean or " and " in clean):
            pieces = [x.strip() for x in clean.replace(" and ", " & ").split(" & ") if x.strip()]
            ids = [await resolve_performer(conn, x) for x in pieces]
            if len(pieces) > 1 and all(ids):
                parts = pieces
        for part in parts:
            out.append(
                {
                    "raw_name": p.name,
                    "clean_name": part,
                    "note": note,
                    "artist_id": artist_id
                    if part == clean
                    else await resolve_performer(conn, part),
                    "slug": p.slug,
                }
            )
    return out


VENUE_ID_COLUMN = {"omr": "omr_slug", "ticketmaster": "ticketmaster_id"}
SAME_PLACE_METERS = 150


async def upsert_venue(conn: asyncpg.Connection, venue: Venue) -> int:
    """The venue by the source's own id, else by name key, else an existing venue within
    SAME_PLACE_METERS ("The Salt Shed" and "The Salt Shed Indoors (Shed)" are one place);
    otherwise a new venue. The source id is recorded on whichever row matched."""
    column = VENUE_ID_COLUMN[venue.source]
    venue_id = await conn.fetchval(f"SELECT venue_id FROM venue WHERE {column} = $1", venue.slug)
    key = norm_key(venue.name) or venue.slug
    if venue_id is None:
        venue_id = await conn.fetchval("SELECT venue_id FROM venue WHERE norm_key = $1", key)
    if venue_id is None and venue.latitude is not None and venue.longitude is not None:
        venue_id = await conn.fetchval(
            """SELECT venue_id FROM venue
                WHERE latitude IS NOT NULL
                  AND sqrt(power((latitude - $1) * 111320, 2)
                           + power((longitude - $2) * 111320 * cos(radians($1)), 2)) < $3
                ORDER BY power(latitude - $1, 2) + power(longitude - $2, 2) LIMIT 1""",
            venue.latitude,
            venue.longitude,
            SAME_PLACE_METERS,
        )
    if venue_id is None:
        return await conn.fetchval(
            f"""INSERT INTO venue (name, norm_key, address, latitude, longitude, website, {column})
                VALUES ($1, $2, $3, $4, $5, $6, $7) RETURNING venue_id""",
            venue.name,
            key,
            venue.address,
            venue.latitude,
            venue.longitude,
            venue.website,
            venue.slug,
        )
    await conn.execute(
        f"""UPDATE venue SET {column} = coalesce({column}, $2),
                             address = coalesce(address, $3),
                             latitude = coalesce(latitude, $4),
                             longitude = coalesce(longitude, $5),
                             website = coalesce(website, $6)
             WHERE venue_id = $1""",
        venue_id,
        venue.slug,
        venue.address,
        venue.latitude,
        venue.longitude,
        venue.website,
    )
    return venue_id


async def upsert_show(conn: asyncpg.Connection, show: Show) -> int:
    """Store one show from one source; returns show_id. One transaction."""
    async with conn.transaction():
        venue_id = await upsert_venue(conn, show.venue)
        lineup = await _lineup(conn, show)
        headliner = lineup[0]["clean_name"] if lineup else show.title
        show_id = await conn.fetchval(
            """INSERT INTO show (venue_id, starts_at, show_date, headliner_key, title,
                                 non_artist, cancelled)
               VALUES ($1, $2, $3, $4, $5, $6, $7)
               ON CONFLICT (venue_id, show_date, headliner_key) DO UPDATE
                  SET starts_at = EXCLUDED.starts_at, title = EXCLUDED.title,
                      non_artist = EXCLUDED.non_artist, last_seen_at = now(),
                      missed_runs = 0, cancelled = EXCLUDED.cancelled
               RETURNING show_id""",
            venue_id,
            show.starts_at,
            show.show_date,
            norm_key(headliner) or headliner.casefold(),
            show.title,
            non_artist_event(show.title),
            show.cancelled,
        )
        await conn.execute(
            """INSERT INTO show_source (source, source_id, show_id, url, tickets_url,
                                        on_sale_at, price, seen_at)
               VALUES ($1, $2, $3, $4, $5, $6, $7, now())
               ON CONFLICT (source, source_id) DO UPDATE
                  SET show_id = EXCLUDED.show_id, url = EXCLUDED.url,
                      tickets_url = EXCLUDED.tickets_url, on_sale_at = EXCLUDED.on_sale_at,
                      price = EXCLUDED.price, seen_at = now()""",
            show.source,
            show.source_id,
            show_id,
            show.url,
            show.tickets_url,
            show.on_sale_at,
            show.price,
        )
        await conn.execute("DELETE FROM show_artist WHERE show_id = $1", show_id)
        await conn.executemany(
            """INSERT INTO show_artist (show_id, position, role, raw_name, clean_name, note,
                                        artist_id, source_slug)
               VALUES ($1, $2, $3, $4, $5, $6, $7, $8)""",
            [
                (
                    show_id,
                    i,
                    "headliner" if i == 1 else "support",
                    a["raw_name"],
                    a["clean_name"],
                    a["note"],
                    a["artist_id"],
                    a["slug"],
                )
                for i, a in enumerate(lineup, 1)
            ],
        )
    return show_id


async def _crawl_list(conn: asyncpg.Connection, sweep: bool) -> list[tuple[int, str]]:
    if sweep:
        rows = await conn.fetch("SELECT venue_id, omr_slug FROM venue WHERE omr_slug IS NOT NULL")
    else:
        rows = await conn.fetch(
            """SELECT venue_id, omr_slug FROM (
                   (SELECT venue_id, omr_slug, 0 AS pri, omr_checked_at FROM venue
                     WHERE omr_slug IS NOT NULL AND omr_upcoming > 0)
                   UNION ALL
                   (SELECT venue_id, omr_slug, 1 AS pri, omr_checked_at FROM venue
                     WHERE omr_slug IS NOT NULL AND coalesce(omr_upcoming, 0) = 0
                     ORDER BY omr_checked_at NULLS FIRST LIMIT $1)
               ) v
               ORDER BY pri, omr_checked_at NULLS FIRST""",
            ROTATE,
        )
    return [(r["venue_id"], r["omr_slug"]) for r in rows]


async def reresolve(conn: asyncpg.Connection) -> int:
    """Upcoming performers without an artist get another lookup (listening grows)."""
    rows = await conn.fetch(
        """SELECT sa.show_id, sa.position, sa.clean_name
             FROM show_artist sa JOIN show s USING (show_id)
            WHERE sa.artist_id IS NULL AND s.starts_at > now() AND NOT s.non_artist"""
    )
    found = 0
    for r in rows:
        artist_id = await resolve_performer(conn, r["clean_name"])
        if artist_id is not None:
            await conn.execute(
                "UPDATE show_artist SET artist_id = $3 WHERE show_id = $1 AND position = $2",
                r["show_id"],
                r["position"],
                artist_id,
            )
            found += 1
    return found


def shows(*, sweep: bool = False, venue_limit: int | None = None) -> JobFn:
    async def _run(ctx: RunContext) -> None:
        started = datetime.now(UTC)
        stored = venues_read = 0
        async with OhMyRockness() as omr:
            index = await omr.venues()
            async with connection(ctx.pool) as conn:
                await conn.executemany(
                    """INSERT INTO venue (name, norm_key, omr_slug) VALUES ($1, $2, $3)
                       ON CONFLICT DO NOTHING""",
                    [(name, norm_key(name) or slug, slug) for slug, name in index],
                )
                crawl = (await _crawl_list(conn, sweep))[:venue_limit]
            for venue_id, slug in crawl:
                ids = await omr.venue_show_ids(slug)
                venues_read += 1
                async with connection(ctx.pool) as conn:
                    await conn.execute(
                        """UPDATE venue SET omr_upcoming = $2, omr_checked_at = now()
                            WHERE venue_id = $1""",
                        venue_id,
                        len(ids),
                    )
                    known = {
                        r["source_id"]
                        for r in await conn.fetch(
                            """UPDATE show_source SET seen_at = now()
                                WHERE source = 'omr' AND source_id = ANY($1::text[])
                                RETURNING source_id""",
                            ids,
                        )
                    }
                for show_id in [i for i in ids if i not in known]:
                    show = await omr.show(show_id)
                    if show is None:
                        continue
                    async with connection(ctx.pool) as conn:
                        await upsert_show(conn, show)
                    stored += 1
                if venues_read % 25 == 0:
                    log.info("shows progress", extra={"venues": venues_read, "new": stored})
            requests = omr.web.requests
        tm_new = tm_seen = 0
        key = get_settings().ticketmaster_api_key
        if key is not None and key.get_secret_value():
            async with Ticketmaster(key.get_secret_value()) as tm:
                listed = await tm.upcoming()
                requests += tm.web.requests
            async with connection(ctx.pool) as conn:
                known = {
                    r["source_id"]
                    for r in await conn.fetch(
                        """UPDATE show_source SET seen_at = now()
                            WHERE source = 'ticketmaster' AND source_id = ANY($1::text[])
                            RETURNING source_id""",
                        [s.source_id for s in listed],
                    )
                }
                cancelled = [s.source_id for s in listed if s.cancelled and s.source_id in known]
                await conn.execute(
                    """UPDATE show SET cancelled = true
                         FROM show_source ss
                        WHERE ss.show_id = show.show_id AND ss.source = 'ticketmaster'
                          AND ss.source_id = ANY($1::text[])""",
                    cancelled,
                )
            tm_seen = len(known)
            for show in listed:
                if show.source_id in known:
                    continue
                async with connection(ctx.pool) as conn:
                    await upsert_show(conn, show)
                tm_new += 1
        async with connection(ctx.pool) as conn:
            await conn.execute(
                """UPDATE show s SET last_seen_at = now(), missed_runs = 0
                     FROM show_source ss
                    WHERE ss.show_id = s.show_id AND ss.seen_at >= $1""",
                started,
            )
            missed = await conn.execute(
                f"""UPDATE show s SET missed_runs = s.missed_runs + 1,
                                     cancelled = s.missed_runs + 1 >= {CANCEL_AFTER}
                      FROM show_source ss
                     WHERE ss.show_id = s.show_id AND ss.source = 'omr'
                       AND ss.seen_at < $1 AND s.starts_at > now()
                       AND s.venue_id = ANY($2::int[])""",
                started,
                [v for v, _ in crawl],
            )
            resolved_later = await reresolve(conn)
            upcoming = await conn.fetchrow(
                """SELECT count(*) AS shows,
                          count(*) FILTER (WHERE EXISTS (
                              SELECT 1 FROM show_artist sa
                               WHERE sa.show_id = s.show_id AND sa.artist_id IS NOT NULL
                                 AND sa.role = 'headliner')) AS with_known_headliner
                     FROM show s WHERE s.starts_at > now() AND NOT s.cancelled"""
            )
        ctx.rows = stored + tm_new
        ctx.notes.update(
            mode="sweep" if sweep else "nightly",
            venues_indexed=len(index),
            venues_read=venues_read,
            new_shows=stored,
            ticketmaster_new=tm_new,
            ticketmaster_seen=tm_seen,
            requests=requests,
            missed=int(missed.split()[-1]),
            resolved_later=resolved_later,
            upcoming_shows=upcoming["shows"],
            upcoming_with_known_headliner=upcoming["with_known_headliner"],
        )

    return _run
