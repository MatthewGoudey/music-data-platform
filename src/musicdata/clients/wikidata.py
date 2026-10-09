"""Wikidata: the English Wikipedia sitelink of an item (graph baseline, GRAPH_SPEC 7.1)."""

from __future__ import annotations

import asyncio

import httpx

API = "https://www.wikidata.org/w/api.php"


class WikidataClient:
    def __init__(self, *, user_agent: str, client: httpx.AsyncClient | None = None) -> None:
        self._http = client or httpx.AsyncClient(headers={"User-Agent": user_agent}, timeout=30)
        self.requests = 0

    async def __aenter__(self) -> WikidataClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self._http.aclose()

    async def enwiki_url(self, qid: str) -> str | None:
        """The English Wikipedia URL Wikidata holds for `qid`, or None. Backs off on 429."""
        params = {
            "action": "wbgetentities",
            "ids": qid,
            "props": "sitelinks/urls",
            "sitefilter": "enwiki",
            "format": "json",
        }
        for attempt in range(4):
            self.requests += 1
            r = await self._http.get(API, params=params)
            if r.status_code == 429 and attempt < 3:
                await asyncio.sleep(int(r.headers.get("Retry-After") or 0) or 2 * (attempt + 1))
                continue
            r.raise_for_status()
            entity = (r.json().get("entities") or {}).get(qid) or {}
            link = (entity.get("sitelinks") or {}).get("enwiki")
            return link.get("url") if link else None
        return None
