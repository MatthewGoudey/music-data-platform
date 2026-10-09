"""MusicBrainz web service client, at the 1 request/second the service asks for.

One browse call per release group returns its metadata, artist credit and every release
with full tracklists (`inc=media+release-groups+artist-credits+recordings`), so a lookup
by MBID costs one request.
"""

from __future__ import annotations

import asyncio
import re
import time

import httpx

from musicdata.log import get_logger

log = get_logger(__name__)

BASE_URL = "https://musicbrainz.org/ws/2"
BROWSE_LIMIT = 100
MAX_RELEASES = 300  # Beatles-sized groups: the canonical release is among the first pages


_WORD = re.compile(r"\w+")


def _lucene(text: str) -> str:
    """Escape a phrase for a quoted Lucene term."""
    return text.replace("\\", "\\\\").replace('"', '\\"')


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
            follow_redirects=True,  # a merged MBID answers 301 with its new MBID
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

    async def search_release_groups(
        self,
        title: str,
        *,
        artist_name: str | None = None,
        artist_mbid: str | None = None,
        limit: int = 5,
        loose: bool = False,
    ) -> list[dict]:
        """Release groups matching a title, by an artist when one is given; best score first.
        `loose` searches the words instead of the exact phrase ("Lift Yr. Skinny Fists …")."""
        if loose:
            words = " ".join(w.lower() for w in _WORD.findall(title)) or title
            query = f"releasegroup:({_lucene(words)})"
        else:
            query = f'releasegroup:"{_lucene(title)}"'
        if artist_mbid:
            query += f" AND arid:{artist_mbid}"
        elif artist_name and loose:
            query += f" AND artist:({_lucene(' '.join(w.lower() for w in _WORD.findall(artist_name)) or artist_name)})"
        elif artist_name:
            query += f' AND artist:"{_lucene(artist_name)}"'
        body = await self._get("/release-group", {"query": query, "limit": limit})
        return body.get("release-groups", [])

    async def release_group(self, mbid: str) -> dict:
        """A release group with its URL relations and artist credit (graph baseline)."""
        return await self._get(f"/release-group/{mbid}", {"inc": "url-rels+artist-credits"})

    async def release(self, mbid: str) -> dict:
        """A release with labels, recordings, and the relations the graph turns into claims:
        credits, places and works at release and recording level."""
        inc = (
            "labels+recordings+artist-rels+place-rels+recording-level-rels"
            "+work-rels+work-level-rels+artist-credits"
        )
        return await self._get(f"/release/{mbid}", {"inc": inc})

    async def artist(self, mbid: str) -> dict:
        """An artist with type, areas, life span, artist and URL relations."""
        return await self._get(f"/artist/{mbid}", {"inc": "artist-rels+url-rels"})

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
