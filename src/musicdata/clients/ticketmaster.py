"""Ticketmaster Discovery API: upcoming music events in Chicago.

The API allows 5 requests a second and 5,000 a day, and pages no deeper than 1,000
results, so the next HORIZON_DAYS are read in WINDOW_DAYS windows (Chicago lists about
1,200 music events at a time). Needs TICKETMASTER_API_KEY (the consumer key).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx

from musicdata.clients.web import PoliteFetcher
from musicdata.shows.parse import Show, parse_tm_events

BASE_URL = "https://app.ticketmaster.com"
WINDOW_DAYS = 30
HORIZON_DAYS = 180
PAGE_SIZE = 200


class Ticketmaster:
    def __init__(self, api_key: str, fetcher: PoliteFetcher | None = None) -> None:
        if not api_key:
            raise ValueError("TICKETMASTER_API_KEY is not set")
        self._key = api_key
        self.web = fetcher or PoliteFetcher(BASE_URL, min_interval=0.25)

    async def __aenter__(self) -> Ticketmaster:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.web.__aexit__(*exc)

    async def _get(self, params: dict[str, object]) -> httpx.Response:
        """The key rides in the URL, so it is redacted from any error raised here."""
        try:
            return await self.web.get("/discovery/v2/events.json", params)
        except (httpx.HTTPError, RuntimeError) as exc:
            raise RuntimeError(str(exc).replace(self._key, "***")) from None

    async def upcoming(self, now: datetime | None = None) -> list[Show]:
        start = (now or datetime.now(UTC)).replace(minute=0, second=0, microsecond=0)
        shows: dict[str, Show] = {}
        for offset in range(0, HORIZON_DAYS, WINDOW_DAYS):
            lo = start + timedelta(days=offset)
            hi = lo + timedelta(days=WINDOW_DAYS)
            page, pages = 0, 1
            while page < pages:
                r = await self._get(
                    {
                        "apikey": self._key,
                        "city": "Chicago",
                        "stateCode": "IL",
                        "classificationName": "music",
                        "startDateTime": lo.strftime("%Y-%m-%dT%H:%M:%SZ"),
                        "endDateTime": hi.strftime("%Y-%m-%dT%H:%M:%SZ"),
                        "sort": "date,asc",
                        "size": PAGE_SIZE,
                        "page": page,
                    },
                )
                body = r.json()
                pages = int((body.get("page") or {}).get("totalPages") or 0)
                for show in parse_tm_events(body):
                    shows.setdefault(show.source_id, show)
                page += 1
        return list(shows.values())
