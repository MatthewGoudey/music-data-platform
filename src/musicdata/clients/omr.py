"""Oh My Rockness (Chicago): the venue index, each venue's upcoming shows, show records.

Only public pages: /venues/all, venue pages and show pages. The site's `/api/` needs a
token and is not used.
"""

from __future__ import annotations

from musicdata.clients.web import PageNotFoundError, PoliteFetcher
from musicdata.shows.parse import (
    Show,
    parse_omr_show_page,
    parse_omr_venue_index,
    parse_omr_venue_page,
)

BASE_URL = "https://chicago.ohmyrockness.com"


class OhMyRockness:
    def __init__(self, fetcher: PoliteFetcher | None = None) -> None:
        self.web = fetcher or PoliteFetcher(BASE_URL)

    async def __aenter__(self) -> OhMyRockness:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.web.__aexit__(*exc)

    async def venues(self) -> list[tuple[str, str]]:
        """(slug, name) for every venue the site has ever listed."""
        r = await self.web.get("/venues/all")
        return parse_omr_venue_index(r.text)

    async def venue_show_ids(self, slug: str) -> list[str]:
        """Upcoming show ids on a venue's page; empty when the venue is gone."""
        try:
            r = await self.web.get(f"/venues/{slug}")
        except PageNotFoundError:
            return []
        return parse_omr_venue_page(r.text)

    async def show(self, show_id: str) -> Show | None:
        try:
            r = await self.web.get(f"/shows/{show_id}")
        except PageNotFoundError:
            return None
        return parse_omr_show_page(r.text, show_id)
