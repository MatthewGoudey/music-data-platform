"""Album documents as pages (docs/graph/COMPANION_SPEC.md 7.5): the latest ready version of an
album's liner notes or deep dive, behind the page token (`?t=`) or the API bearer token."""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse

from musicdata.api.deps import Pool
from musicdata.api.routers.queue import page_or_bearer
from musicdata.db import connection
from musicdata.documents import document_page

router = APIRouter(tags=["documents"])


@router.get(
    "/albums/{release_group_id}/documents/{kind}",
    response_class=HTMLResponse,
    dependencies=[Depends(page_or_bearer)],
)
async def album_document(
    release_group_id: int,
    kind: Annotated[Literal["deep_dive", "liner_notes"], "document kind"],
    pool: Pool,
) -> HTMLResponse:
    async with connection(pool) as conn:
        page = await document_page(conn, release_group_id, kind)
    if page is None:
        raise HTTPException(status_code=404, detail=f"no ready {kind} for this album")
    return HTMLResponse(page)
