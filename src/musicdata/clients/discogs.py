"""Discogs: credits, labels and styles for an album from the Discogs link MusicBrainz holds.

Free API: 25 requests a minute without a token, 60 with `DISCOGS_TOKEN` (sent as
"Discogs token=…", never logged). A master link reads its main release.
"""

from __future__ import annotations

import asyncio
import re
import time

import httpx

API = "https://api.discogs.com"
_NUMBER = re.compile(r"\s\(\d+\)$")  # "Jim Keltner (2)" → "Jim Keltner"
_BRACKETS = re.compile(r"\s*\[.*?\]")  # "Guitar [Pedal Steel]" → "Guitar"


def clean_name(name: str) -> str:
    return _NUMBER.sub("", name or "").strip()


def clean_role(role: str) -> str:
    return _BRACKETS.sub("", role or "").strip()


def release_id_from(links: list[str]) -> tuple[str | None, str | None]:
    """(kind, id) for the first master link, else the first release link."""
    for kind in ("master", "release"):
        for url in links:
            if f"/{kind}/" in url:
                return kind, url.rstrip("/").rsplit("/", 1)[-1]
    return None, None


class DiscogsClient:
    def __init__(
        self,
        *,
        user_agent: str,
        token: str | None = None,
        client: httpx.AsyncClient | None = None,
        sleep=asyncio.sleep,
    ) -> None:
        headers = {"User-Agent": user_agent}
        if token:
            headers["Authorization"] = f"Discogs token={token}"
        self._http = client or httpx.AsyncClient(base_url=API, headers=headers, timeout=30)
        self._interval = 1.05 if token else 2.5  # 60 or 25 a minute
        self._sleep = sleep
        self._last = 0.0
        self.requests = 0

    async def __aenter__(self) -> DiscogsClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self._http.aclose()

    async def _get(self, path: str) -> dict:
        for attempt in range(4):
            wait = self._last + self._interval - time.monotonic()
            if wait > 0:
                await self._sleep(wait)
            self._last = time.monotonic()
            self.requests += 1
            r = await self._http.get(path)
            if r.status_code == 429 and attempt < 3:
                await self._sleep(10 * (attempt + 1))
                continue
            r.raise_for_status()
            return r.json()
        raise RuntimeError("unreachable")

    async def album(self, links: list[str]) -> dict | None:
        """Credits, labels, styles and genres of the release a Discogs link points at."""
        kind, ident = release_id_from(links)
        if ident is None:
            return None
        if kind == "master":
            ident = (await self._get(f"/masters/{ident}")).get("main_release")
            if ident is None:
                return None
        r = await self._get(f"/releases/{ident}")
        credits = [
            {"name": a["name"], "role": a.get("role"), "tracks": a.get("tracks") or "album"}
            for a in r.get("extraartists") or []
        ]
        for t in r.get("tracklist") or []:
            for a in t.get("extraartists") or []:
                credits.append({"name": a["name"], "role": a.get("role"), "tracks": t.get("title")})
        return {
            "release_id": ident,
            "url": f"https://www.discogs.com/release/{ident}",
            "styles": r.get("styles") or [],
            "genres": r.get("genres") or [],
            "labels": [{"name": x["name"], "catno": x.get("catno")} for x in r.get("labels") or []],
            "credits": credits,
        }
