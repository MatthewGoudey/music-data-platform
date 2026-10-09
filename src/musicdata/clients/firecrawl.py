"""Firecrawl v2 scrape (GRAPH_SPEC 7.2): one page as markdown, plus facts JSON in `facts` mode.

    POST https://api.firecrawl.dev/v2/scrape   Authorization: Bearer <FIRECRAWL_API_KEY>

Modes: `facts` (markdown + JSON with the facts_v2 schema, main content; 5 credits), `plain`
(markdown, main content; 1), `bandcamp` (markdown of the album blocks only; 1). The key never
appears in an error or a log line: any `fc-…` token is replaced by `fc-***`.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

import httpx

URL = "https://api.firecrawl.dev/v2/scrape"
COST = {"facts": 5, "plain": 1, "bandcamp": 1}
SCHEMA_VERSION = {"facts": "facts-v2", "plain": "plain", "bandcamp": "bandcamp-v1"}
BANDCAMP_TAGS = [
    "#name-section",
    ".tralbum-about",
    ".tralbum-credits",
    ".tralbum-tags",
    "#band-name-location",
]
FACTS_SCHEMA = json.loads(
    (Path(__file__).parents[1] / "graph" / "schemas" / "facts_v2.json").read_text("utf-8")
)
_KEY = re.compile(r"fc-[A-Za-z0-9]+")


def redact(text: str) -> str:
    return _KEY.sub("fc-***", text or "")


@dataclass
class Page:
    url: str
    mode: str
    ok: bool
    credits: int
    title: str | None = None
    body: str | None = None
    facts: dict | None = None
    error: str | None = None


def request_body(url: str, mode: str) -> dict:
    if mode == "facts":
        return {
            "url": url,
            "formats": ["markdown", {"type": "json", "schema": FACTS_SCHEMA}],
            "onlyMainContent": True,
        }
    if mode == "bandcamp":
        return {"url": url, "formats": ["markdown"], "includeTags": BANDCAMP_TAGS}
    if mode == "plain":
        return {"url": url, "formats": ["markdown"], "onlyMainContent": True}
    raise ValueError(f"unknown mode {mode}")


def parse(url: str, mode: str, status: int, payload: dict | None, text: str) -> Page:
    """A Page from an HTTP answer: ok with body and facts, or a failure costing 1 credit."""
    data = (payload or {}).get("data") or {}
    if status == 200 and (payload or {}).get("success") and data.get("markdown"):
        meta = data.get("metadata") or {}
        title = meta.get("title")
        credits = meta.get("creditsUsed") or data.get("creditsUsed") or COST[mode]
        return Page(
            url,
            mode,
            True,
            int(credits),
            title=title[0] if isinstance(title, list) and title else title,
            body=data["markdown"],
            facts=data.get("json") if mode == "facts" else None,
        )
    message = (payload or {}).get("error") or text or "empty page"
    return Page(url, mode, False, 1, error=redact(f"HTTP {status}: {message}")[:300])


class FirecrawlClient:
    def __init__(self, api_key: str, *, client: httpx.AsyncClient | None = None) -> None:
        self._key = api_key
        self._http = client or httpx.AsyncClient(timeout=120)

    async def __aenter__(self) -> FirecrawlClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self._http.aclose()

    async def scrape(self, url: str, mode: str) -> Page:
        """One call, never retried: a 403, 429, 5xx or timeout comes back as a failed Page."""
        try:
            r = await self._http.post(
                URL,
                json=request_body(url, mode),
                headers={"Authorization": f"Bearer {self._key}"},
            )
        except httpx.HTTPError as exc:
            return Page(url, mode, False, 1, error=redact(f"{type(exc).__name__}: {exc}")[:300])
        try:
            payload = r.json()
        except ValueError:
            payload = None
        return parse(url, mode, r.status_code, payload, r.text[:300])
