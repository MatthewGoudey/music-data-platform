"""`musicdata venues travel`: minutes by CTA and on foot from home to each venue (ADR 0008).

Venues without times go first, those with upcoming shows before the rest. A venue is
reached by its coordinates when Oh My Rockness or Ticketmaster gave them, else its
address, else "<name>, Chicago, IL". Transit is timed for the coming Friday at 6:30 PM
in Chicago, a usual time to head to a show. A venue the API cannot route keeps a note
and is not asked again unless `--refresh`.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from musicdata.clients.routes import Place, RoutesClient
from musicdata.config import get_settings
from musicdata.db import connection
from musicdata.jobs.runs import JobFn, RunContext

CHICAGO = ZoneInfo("America/Chicago")
DEPART_WEEKDAY = 4  # Friday
DEPART_AT = (18, 30)


def show_night(now: datetime) -> datetime:
    """The coming Friday at 6:30 PM in Chicago (a week ahead if that moment has passed)."""
    local = now.astimezone(CHICAGO)
    day = local + timedelta(days=(DEPART_WEEKDAY - local.weekday()) % 7)
    at = day.replace(hour=DEPART_AT[0], minute=DEPART_AT[1], second=0, microsecond=0)
    return at if at > local else at + timedelta(days=7)


WALK_ONLY_MAX = 20  # minutes: a walk this short needs no train
WALK_TOO_MAX = 40  # minutes: up to this, show the walk beside the CTA time


def travel_line(transit_min: int | None, walk_min: int | None) -> str | None:
    """The sensible way to get there: a short walk alone, a middling walk beside the CTA
    time, otherwise the CTA (whose time includes the walks to and from the stops)."""
    walk = f"{walk_min} min walk" if walk_min is not None else None
    cta = f"{transit_min} min CTA" if transit_min is not None else None
    if walk and (walk_min <= WALK_ONLY_MAX or not cta or transit_min >= walk_min):
        return walk
    if walk and cta and walk_min <= WALK_TOO_MAX:
        return f"{walk} · {cta}"
    return cta


def place_of(name: str, address: str | None, lat: float | None, lon: float | None) -> Place:
    if lat is not None and lon is not None:
        return Place(latitude=lat, longitude=lon)
    return Place(address=address or f"{name}, Chicago, IL")


def venues_travel(*, limit: int = 1000, refresh: bool = False) -> JobFn:
    async def _run(ctx: RunContext) -> None:
        settings = get_settings()
        if not settings.google_routes_api_key or not settings.home_address:
            raise RuntimeError("GOOGLE_ROUTES_API_KEY and HOME_ADDRESS must both be set")
        home = settings.home_address.get_secret_value()
        async with connection(ctx.pool) as conn:
            venues = await conn.fetch(
                """SELECT v.venue_id, v.name, v.address, v.latitude, v.longitude
                     FROM venue v
                    WHERE $1::bool OR v.travel_checked_at IS NULL
                    ORDER BY EXISTS (SELECT 1 FROM show s
                                      WHERE s.venue_id = v.venue_id AND s.starts_at > now()) DESC,
                             v.venue_id
                    LIMIT $2""",
                refresh,
                limit,
            )
        places = [place_of(v["name"], v["address"], v["latitude"], v["longitude"]) for v in venues]
        departure = show_night(datetime.now(UTC)).astimezone(UTC).isoformat().replace("+00:00", "Z")
        async with RoutesClient(settings.google_routes_api_key.get_secret_value()) as routes:
            transit = await routes.matrix(home, places, "TRANSIT", departure) if places else []
            walk = await routes.matrix(home, places, "WALK") if places else []
            elements = routes.elements
        found = 0
        async with connection(ctx.pool) as conn:
            await conn.executemany(
                """UPDATE venue SET transit_min = $2, walk_min = $3, walk_km = $4,
                                    travel_checked_at = now(), travel_note = $5
                    WHERE venue_id = $1""",
                [
                    (
                        v["venue_id"],
                        t.minutes,
                        w.minutes,
                        round(w.meters / 1000, 2) if w.meters else None,
                        None if (t.found or w.found) else "no route found",
                    )
                    for v, t, w in zip(venues, transit, walk, strict=True)
                ],
            )
            found = sum(1 for t, w in zip(transit, walk, strict=True) if t.found or w.found)
        ctx.rows = found
        ctx.notes.update(
            venues=len(venues),
            routed=found,
            route_elements=elements,
            transit_departure=departure,
        )

    return _run
