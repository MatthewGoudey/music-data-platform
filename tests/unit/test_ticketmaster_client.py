"""The Ticketmaster key rides in the URL; it must never reach an error message or log."""

from __future__ import annotations

import httpx
import pytest

from musicdata.clients.ticketmaster import BASE_URL, HORIZON_DAYS, WINDOW_DAYS, Ticketmaster
from musicdata.clients.web import PoliteFetcher


async def _no_wait(seconds: float) -> None:
    return None


async def test_errors_never_show_the_key() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"fault": "invalid key"})

    http = httpx.AsyncClient(base_url=BASE_URL, transport=httpx.MockTransport(handler))
    tm = Ticketmaster("SECRET-KEY-123", PoliteFetcher(BASE_URL, client=http, sleep=_no_wait))
    with pytest.raises(RuntimeError) as err:
        await tm.upcoming()
    assert "SECRET-KEY-123" not in str(err.value) and "***" in str(err.value)


async def test_windows_cover_the_horizon_and_pages_are_followed() -> None:
    calls: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.url.params["startDateTime"], request.url.params["page"]))
        page = int(request.url.params["page"])
        return httpx.Response(200, json={"page": {"totalPages": 2, "number": page}})

    http = httpx.AsyncClient(base_url=BASE_URL, transport=httpx.MockTransport(handler))
    tm = Ticketmaster("k", PoliteFetcher(BASE_URL, client=http, sleep=_no_wait))
    assert await tm.upcoming() == []
    windows = -(-HORIZON_DAYS // WINDOW_DAYS)  # a year in 30-day windows: 13
    assert len(calls) == windows * 2  # two pages each
