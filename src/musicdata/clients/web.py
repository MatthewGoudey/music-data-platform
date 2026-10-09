"""A polite fetcher for public web pages (show listings).

Etiquette (ADR 0016): public pages only, never token-gated APIs; a User-Agent that says
who we are and links the repository; at least two seconds between requests to a host;
back off on 429 and 5xx. A site that refuses an honest User-Agent is not scraped.
"""

from __future__ import annotations

import asyncio
import time

import httpx

from musicdata.log import get_logger

log = get_logger(__name__)

USER_AGENT = (
    "Mozilla/5.0 (compatible; musicdata/0.1; +https://github.com/MatthewGoudey/music-data-platform)"
)


class PageNotFoundError(Exception):
    """The page is gone (404)."""


class PoliteFetcher:
    def __init__(
        self,
        base_url: str,
        *,
        min_interval: float = 2.0,
        client: httpx.AsyncClient | None = None,
        sleep=asyncio.sleep,
    ) -> None:
        self._http = client or httpx.AsyncClient(
            base_url=base_url,
            headers={"User-Agent": USER_AGENT},
            timeout=30,
            follow_redirects=True,
        )
        self._min_interval = min_interval
        self._sleep = sleep
        self._last = 0.0
        self.requests = 0

    async def __aenter__(self) -> PoliteFetcher:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self._http.aclose()

    async def get(self, path: str, params: dict[str, object] | None = None) -> httpx.Response:
        delay = 5.0
        for attempt in range(1, 5):
            wait = self._last + self._min_interval - time.monotonic()
            if wait > 0:
                await self._sleep(wait)
            self._last = time.monotonic()
            self.requests += 1
            try:
                r = await self._http.get(path, params=params)
            except httpx.TransportError as exc:
                if attempt == 4:
                    raise
                log.warning("fetch failed; retrying", extra={"path": path, "error": str(exc)})
                await self._sleep(delay)
                delay *= 2
                continue
            if r.status_code == 404:
                raise PageNotFoundError(path)
            if r.status_code == 429 or r.status_code >= 500:
                if attempt == 4:
                    r.raise_for_status()
                log.warning(
                    "fetch throttled; backing off", extra={"path": path, "status": r.status_code}
                )
                await self._sleep(delay)
                delay *= 2
                continue
            r.raise_for_status()
            return r
        raise RuntimeError("unreachable")
