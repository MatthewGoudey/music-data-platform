"""Tags on albums (docs/QUEUE_SPEC.md sections 4, 11 and 12; Block F).

A tag is a mood or a use (study, workout, rainy …) on a release group, so an album keeps
its tags whichever list it comes from. `apply` and `remove` take release group ids;
`tag-now-playing` tags whatever ListenBrainz reports as playing right now. The page uses
the page token; Claude and scripts use the API token.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from musicdata.api.deps import Format, FormatParam, Pool, page_or_bearer, render
from musicdata.clients.listenbrainz import ListenBrainzClient
from musicdata.config import get_settings
from musicdata.db import connection
from musicdata.identity import norm_key
from musicdata.ingest.parse import parse_listen
from musicdata.lists.match import group_key
from musicdata.lists.resolve import Album, Known

router = APIRouter(tags=["tags"], dependencies=[Depends(page_or_bearer)])


class Groups(BaseModel):
    release_group_ids: list[int] = Field(min_length=1, max_length=500)


async def _tag_id(conn, name: str) -> int:
    tag_id = await conn.fetchval("SELECT tag_id FROM tag WHERE name = $1 AND active", name)
    if tag_id is None:
        raise HTTPException(status_code=404, detail=f"no active tag {name}")
    return tag_id


@router.get("/tags")
async def tags(pool: Pool, format: FormatParam = Format.compact):
    """Every active tag with the number of albums carrying it."""
    async with connection(pool) as conn:
        rows = await conn.fetch(
            """SELECT t.name, t.description,
                      count(rt.release_group_id) FILTER (WHERE rt.status = 'applied') AS albums
                 FROM tag t LEFT JOIN release_group_tag rt USING (tag_id)
                WHERE t.active GROUP BY t.tag_id ORDER BY t.tag_id"""
        )
    return render(rows, format)


@router.post("/tags/{name}/apply")
async def apply_tag(name: str, body: Groups, pool: Pool):
    """Tag albums by hand (source `manual`, status `applied`); confirms a suggestion."""
    async with connection(pool) as conn:
        tag_id = await _tag_id(conn, name)
        status = await conn.execute(
            """INSERT INTO release_group_tag (release_group_id, tag_id, source, status)
               SELECT g, $2, 'manual', 'applied' FROM unnest($1::int[]) AS g
                WHERE EXISTS (SELECT 1 FROM release_group r WHERE r.release_group_id = g)
               ON CONFLICT (release_group_id, tag_id)
                  DO UPDATE SET status = 'applied', source = 'manual'""",
            body.release_group_ids,
            tag_id,
        )
    return {"tag": name, "applied": int(status.split()[-1])}


@router.post("/tags/{name}/remove")
async def remove_tag(name: str, body: Groups, pool: Pool):
    """Take a tag off albums."""
    async with connection(pool) as conn:
        tag_id = await _tag_id(conn, name)
        status = await conn.execute(
            "DELETE FROM release_group_tag WHERE tag_id = $2 AND release_group_id = ANY($1::int[])",
            body.release_group_ids,
            tag_id,
        )
    return {"tag": name, "removed": int(status.split()[-1])}


@router.post("/tag-now-playing")
async def tag_now_playing(pool: Pool, tag: Annotated[str, Query()]):
    """Tag the album ListenBrainz reports as playing right now."""
    settings = get_settings()
    if not settings.listenbrainz_user:
        raise HTTPException(status_code=503, detail="LISTENBRAINZ_USER is not set")
    async with ListenBrainzClient(
        settings.listenbrainz_user, user_agent=settings.musicbrainz_user_agent
    ) as lb:
        now = await lb.playing_now()
    if now is None:
        raise HTTPException(status_code=404, detail="nothing is playing on ListenBrainz")
    p = parse_listen({**now, "listened_at": int(datetime.now(UTC).timestamp())})
    if p is None or not p.release_name:
        raise HTTPException(status_code=404, detail="what is playing names no album")
    async with connection(pool) as conn:
        tag_id = await _tag_id(conn, tag)
        rg = None
        if p.release_group_mbid:
            rg = await conn.fetchval(
                "SELECT release_group_id FROM release_group WHERE mbid = $1",
                p.release_group_mbid,
            )
        if rg is None:
            known = await Known.load(conn)
            for artist in (p.album_artist.name, p.artist.name):
                rg = known.find(
                    Album(artist, p.release_name, norm_key(artist), group_key(p.release_name), None)
                )
                if rg:
                    break
        if rg is None:
            raise HTTPException(
                status_code=404, detail=f"no album in the library for {p.release_name}"
            )
        await conn.execute(
            """INSERT INTO release_group_tag (release_group_id, tag_id, source, status)
               VALUES ($1, $2, 'manual', 'applied')
               ON CONFLICT (release_group_id, tag_id) DO UPDATE SET status = 'applied'""",
            rg,
            tag_id,
        )
        title = await conn.fetchval(
            "SELECT title FROM release_group WHERE release_group_id = $1", rg
        )
    return {"tag": tag, "release_group_id": rg, "album": title, "artist": p.artist_name}
