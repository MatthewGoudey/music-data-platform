"""ListenBrainz API client: listens (paged backwards in time) and listen-count.

No token is needed for either endpoint. The client honours the X-RateLimit-* headers
ListenBrainz sends on every response and retries 429, 5xx and network errors with backoff.

Deep in a long history a 1,000-listen page can take ListenBrainz ~40 s, where its server
drops the connection; a 500-listen page from the same point takes a few seconds. So paging
starts at 1,000 and halves the page size on each timeout, down to 100.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import datetime

import httpx

from musicdata.log import get_logger

log = get_logger(__name__)

BASE_URL = "https://api.listenbrainz.org/1"
PAGE_SIZE = 1000  # the API maximum
MIN_PAGE_SIZE = 100


class ListenBrainzClient:
    def __init__(
        self, user: str, *, user_agent: str, client: httpx.AsyncClient | None = None
    ) -> None:
        if not user:
            raise ValueError("LISTENBRAINZ_USER is not set")
        self.user = user
        self._http = client or httpx.AsyncClient(
            base_url=BASE_URL, headers={"User-Agent": user_agent}, timeout=60
        )

    async def __aenter__(self) -> ListenBrainzClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self._http.aclose()

    async def _get(
        self, path: str, params: dict[str, object] | None = None, *, attempts: int = 5
    ) -> dict:
        delay = 2.0
        for attempt in range(1, attempts + 1):
            try:
                r = await self._http.get(path, params=params)
            except httpx.TransportError as exc:
                if attempt == attempts:
                    raise
                log.warning("listenbrainz network error; retrying", extra={"error": str(exc)})
                await asyncio.sleep(delay)
                delay *= 2
                continue
            if r.status_code == 429 or r.status_code >= 500:
                if attempt == attempts:
                    r.raise_for_status()
                wait = float(r.headers.get("X-RateLimit-Reset-In", delay))
                log.warning(
                    "listenbrainz throttled or failing; retrying",
                    extra={"status": r.status_code, "wait": wait},
                )
                await asyncio.sleep(wait + 0.5)
                delay *= 2
                continue
            r.raise_for_status()
            await self._respect_rate_limit(r.headers)
            return r.json()
        raise RuntimeError("unreachable")

    @staticmethod
    async def _respect_rate_limit(headers: httpx.Headers) -> None:
        remaining = headers.get("X-RateLimit-Remaining")
        if remaining is not None and int(remaining) <= 1:
            await asyncio.sleep(float(headers.get("X-RateLimit-Reset-In", 1)) + 0.5)

    async def listen_count(self) -> int:
        body = await self._get(f"/user/{self.user}/listen-count")
        return int(body["payload"]["count"])

    async def listens_page(
        self, max_ts: int | None = None, count: int = PAGE_SIZE, *, attempts: int = 5
    ) -> list[dict]:
        params: dict[str, object] = {"count": count}
        if max_ts is not None:
            params["max_ts"] = max_ts
        body = await self._get(f"/user/{self.user}/listens", params, attempts=attempts)
        return body["payload"]["listens"]

    async def pages_back_to(
        self, stop_before: datetime | None, *, start_before: datetime | None = None
    ) -> AsyncIterator[list[dict]]:
        """Yield pages newest-first from `start_before` (None: now) until a page reaches
        `stop_before` (None: all history).

        `max_ts` is exclusive, so the next page starts one second above the oldest listen
        seen; listens sharing that second come back twice and the natural key drops them.
        """
        stop_ts = int(stop_before.timestamp()) if stop_before else None
        max_ts: int | None = int(start_before.timestamp()) + 1 if start_before else None
        size = PAGE_SIZE
        while True:
            try:
                page = await self.listens_page(max_ts, size, attempts=2)
            except (httpx.TransportError, httpx.HTTPStatusError) as exc:
                # A slow page surfaces as our timeout, a dropped connection, or a 5xx.
                if size <= MIN_PAGE_SIZE or (
                    isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code < 500
                ):
                    raise
                size = max(size // 2, MIN_PAGE_SIZE)
                log.warning("listenbrainz page timed out; shrinking", extra={"page_size": size})
                continue
            if not page:
                return
            yield page
            oldest = min(int(listen["listened_at"]) for listen in page)
            if stop_ts is not None and oldest < stop_ts:
                return
            next_max = oldest + 1
            if max_ts is not None and next_max >= max_ts:
                next_max = oldest  # a full page within one second: step past it
            max_ts = next_max
