"""Pages (docs/graph/COMPANION_SPEC.md section 6): the album page, hub pages, search, and what
is playing now. Page auth: the page token (`?t=`) or the API bearer token; every link a page
makes carries the token."""

from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from musicdata.api.deps import Pool, page_or_bearer
from musicdata.clients.listenbrainz import ListenBrainzClient
from musicdata.config import get_settings
from musicdata.db import connection
from musicdata.graph.generated import rebuild_following, walk
from musicdata.ingest.parse import parse_listen
from musicdata.pages.album import album_page
from musicdata.pages.common import SUGGEST_JS, link
from musicdata.pages.hub import entity_page, search_page, suggest

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


@router.get("/pages/suggest")
async def suggestions(pool: Pool, q: str = "", t: Annotated[str, Query()] = "") -> list[dict]:
    """Autocomplete for every search box: up to 8 names with a link each."""
    async with connection(pool) as conn:
        return await suggest(conn, q, t)


@router.get("/pages/suggest.js")
async def suggest_js() -> Response:
    return Response(SUGGEST_JS, media_type="text/javascript",
                    headers={"Cache-Control": "max-age=300"})  # fmt: skip


FOLLOWABLE = ("person", "artist", "label", "place")


@router.post("/entities/{entity_id}/follow")
async def follow(entity_id: int, pool: Pool) -> dict[str, object]:
    """Follow a person, band, label or studio (companion spec 5.5); rewrites `following`."""
    async with connection(pool) as conn:
        kind = await conn.fetchval("SELECT type FROM entity WHERE entity_id = $1", entity_id)
        if kind not in FOLLOWABLE:
            raise HTTPException(status_code=404, detail="only people, bands, labels and studios")
        await conn.execute(
            "INSERT INTO graph_follow (entity_id) VALUES ($1) ON CONFLICT DO NOTHING", entity_id
        )
        n = await rebuild_following(conn)
    return {"entity_id": entity_id, "following": True, "entries": n}


@router.post("/entities/{entity_id}/unfollow")
async def unfollow(entity_id: int, pool: Pool) -> dict[str, object]:
    async with connection(pool) as conn:
        await conn.execute("DELETE FROM graph_follow WHERE entity_id = $1", entity_id)
        n = await rebuild_following(conn)
    return {"entity_id": entity_id, "following": False, "entries": n}


@router.post("/graph/walk")
async def graph_walk(pool: Pool, from_: Annotated[int, Query(alias="from")]) -> dict[str, object]:
    """Walk back from an album (companion spec 5.6): rewrites `graph_walk` for the walk-back
    profile."""
    async with connection(pool) as conn:
        try:
            return await walk(conn, from_)
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc


async def _playing_album(pool, p) -> int | None:
    """The album the player reports, by its MBID or by artist and album keys."""
    from musicdata.identity import album_key, norm_key

    async with connection(pool) as conn:
        if p.release_group_mbid:
            rg = await conn.fetchval(
                "SELECT release_group_id FROM release_group WHERE mbid = $1::uuid",
                p.release_group_mbid,
            )
            if rg:
                return rg
        artists = [k for k in (norm_key(p.album_artist.name), norm_key(p.artist_name)) if k]
        key = album_key(p.release_name) if p.release_name else ""
        if key:
            rg = await conn.fetchval(
                """SELECT rg.release_group_id FROM release_group rg JOIN artist a USING (artist_id)
                    WHERE rg.norm_key = $1 AND a.norm_key = ANY($2::text[])
                    ORDER BY rg.mbid IS NULL, rg.release_group_id LIMIT 1""",
                key,
                artists,
            )
            if rg:
                return rg
        # A player's short album name ("II" for Meat Puppets II): the artist's album holding the
        # track, one whose title ends with the reported name first.
        return await conn.fetchval(
            """SELECT rg.release_group_id FROM release_group rg JOIN artist a USING (artist_id)
                WHERE a.norm_key = ANY($1::text[])
                  AND EXISTS (SELECT 1 FROM release_group_track t
                               WHERE t.release_group_id = rg.release_group_id AND t.norm_title = $2)
                ORDER BY ($3 <> '' AND rg.norm_key LIKE '%' || $3) DESC, rg.mbid IS NULL,
                         rg.release_group_id
                LIMIT 1""",
            artists,
            p.norm_title,
            key,
        )


@router.get("/queue/now-playing")
async def now_playing(pool: Pool) -> dict | None:
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
                         "release": p.release_name, "title_key": p.norm_title,
                         "release_group_id": await _playing_album(pool, p)}  # fmt: skip
    _now.update(at=time.monotonic(), value=value)
    return value
