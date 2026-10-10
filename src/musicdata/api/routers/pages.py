"""Pages (docs/graph/COMPANION_SPEC.md section 6): the album page, hub pages, search, and what
is playing now. Page auth: the page token (`?t=`) or the API bearer token; every link a page
makes carries the token."""

from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import HTMLResponse, RedirectResponse

from musicdata.api.deps import Pool, page_or_bearer
from musicdata.clients.listenbrainz import ListenBrainzClient
from musicdata.config import get_settings
from musicdata.db import connection
from musicdata.ingest.parse import parse_listen
from musicdata.pages.album import album_page
from musicdata.pages.common import link
from musicdata.pages.hub import entity_page, search_page

router = APIRouter(tags=["pages"], dependencies=[Depends(page_or_bearer)])
NOW_CACHE_SECONDS = 20
_now: dict[str, object] = {"at": 0.0, "value": None}


@router.get("/albums/{release_group_id}/page", response_class=HTMLResponse)
async def album(release_group_id: int, pool: Pool, t: Annotated[str, Query()] = "") -> HTMLResponse:
    async with connection(pool) as conn:
        page = await album_page(conn, release_group_id, t)
    if page is None:
        raise HTTPException(status_code=404, detail="no such album")
    return HTMLResponse(page)


@router.get("/entities/{entity_id}/page", response_class=HTMLResponse)
async def entity(entity_id: int, pool: Pool, t: Annotated[str, Query()] = ""):
    async with connection(pool) as conn:
        page = await entity_page(conn, entity_id, t)
        if page is None:
            rg = await conn.fetchval(
                "SELECT release_group_id FROM entity WHERE entity_id = $1 AND type = 'album'",
                entity_id,
            )
    if page is not None:
        return HTMLResponse(page)
    if rg:
        return RedirectResponse(link(f"/albums/{rg}/page", t), status_code=302)
    raise HTTPException(status_code=404, detail="no such page")


@router.get("/pages/search", response_class=HTMLResponse)
async def search(pool: Pool, q: str = "", t: Annotated[str, Query()] = "") -> HTMLResponse:
    async with connection(pool) as conn:
        return HTMLResponse(await search_page(conn, q, t))


@router.get("/queue/now-playing")
async def now_playing() -> dict | None:
    """What ListenBrainz reports as playing (artist, track, release, title_key), cached 20 s."""
    if time.monotonic() - float(_now["at"]) < NOW_CACHE_SECONDS:
        return _now["value"]  # type: ignore[return-value]
    settings = get_settings()
    value = None
    if settings.listenbrainz_user:
        try:
            async with ListenBrainzClient(
                settings.listenbrainz_user, user_agent=settings.musicbrainz_user_agent
            ) as lb:
                listen = await lb.playing_now()
        except Exception:  # a ListenBrainz outage leaves the page without a highlight
            listen = None
        if listen:
            p = parse_listen({**listen, "listened_at": int(datetime.now(UTC).timestamp())})
            if p is not None:
                value = {"artist": p.artist_name, "track": p.track_name,
                         "release": p.release_name, "title_key": p.norm_title}  # fmt: skip
    _now.update(at=time.monotonic(), value=value)
    return value
