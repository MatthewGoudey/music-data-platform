"""MusicBrainz web service client, at the 1 request/second the service asks for.

One browse call per release group returns its metadata, artist credit and every release
with full tracklists (`inc=media+release-groups+artist-credits+recordings`), so a lookup
by MBID costs one request.
"""

from __future__ import annotations

import asyncio
import time

import httpx

from musicdata.log import get_logger

log = get_logger(__name__)

BASE_URL = "https://musicbrainz.org/ws/2"
BROWSE_LIMIT = 100
MAX_RELEASES = 300  # Beatles-sized groups: the canonical release is among the first pages


class NotFoundError(Exception):
    """The MBID is gone from MusicBrainz (merged or deleted)."""


class MusicBrainzClient:
    def __init__(
        self,
        *,
        user_agent: str,
        client: httpx.AsyncClient | None = None,
        min_interval: float = 1.0,
    ) -> None:
        self._http = client or httpx.AsyncClient(
            base_url=BASE_URL,
            headers={"User-Agent": user_agent, "Accept": "application/json"},
            timeout=60,
        )
        self._min_interval = min_interval
        self._last = 0.0
        self.requests = 0

    async def __aenter__(self) -> MusicBrainzClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self._http.aclose()

    async def _throttle(self) -> None:
        wait = self._last + self._min_interval - time.monotonic()
        if wait > 0:
            await asyncio.sleep(wait)
        self._last = time.monotonic()

    async def _get(self, path: str, params: dict[str, object]) -> dict:
        delay = 2.0
        for attempt in range(1, 6):
            await self._throttle()
            self.requests += 1
            try:
                r = await self._http.get(path, params={**params, "fmt": "json"})
            except httpx.TransportError as exc:
                if attempt == 5:
                    raise
                log.warning("musicbrainz network error; retrying", extra={"error": str(exc)})
                await asyncio.sleep(delay)
                delay *= 2
                continue
            if r.status_code == 404:
                raise NotFoundError(path)
            if r.status_code in (429, 503) or r.status_code >= 500:
                if attempt == 5:
                    r.raise_for_status()
                log.warning("musicbrainz throttled; backing off", extra={"status": r.status_code})
                await asyncio.sleep(delay)
                delay *= 2
                continue
            r.raise_for_status()
            return r.json()
        raise RuntimeError("unreachable")

    async def releases_of_group(self, release_group_mbid: str) -> list[dict]:
        """Every release in the group with media, tracks, artist credit and group metadata."""
        releases: list[dict] = []
        offset = 0
        while True:
            body = await self._get(
                "/release",
                {
                    "release-group": release_group_mbid,
                    "inc": "media+release-groups+artist-credits+recordings",
                    "limit": BROWSE_LIMIT,
                    "offset": offset,
                },
            )
            releases += body.get("releases", [])
            offset += BROWSE_LIMIT
            if offset >= int(body.get("release-count", 0)) or offset >= MAX_RELEASES:
                break
        if not releases:
            # A browse of an unknown group returns an empty list, not 404.
            raise NotFoundError(f"release-group {release_group_mbid}")
        return releases
