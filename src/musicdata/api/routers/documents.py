"""Album documents (docs/graph/COMPANION_SPEC.md sections 7–8): the page of the latest ready
version, requests from the album page (page auth), and the worker's endpoints (bearer token)."""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from musicdata import worker
from musicdata.api.deps import Pool, page_or_bearer, require_token
from musicdata.db import connection
from musicdata.documents import document_page
from musicdata.graph.batch import store_reader_verdicts
from musicdata.log import get_logger

router = APIRouter(tags=["documents"])
log = get_logger(__name__)
Kind = Literal["deep_dive"]  # liner notes dropped (companion spec change 2026-10-10)


def _refuse(exc: Exception) -> HTTPException:
    return HTTPException(status_code=404 if isinstance(exc, LookupError) else 422, detail=str(exc))


@router.get(
    "/albums/{release_group_id}/documents/{kind}",
    response_class=HTMLResponse,
    dependencies=[Depends(page_or_bearer)],
)
async def album_document(release_group_id: int, kind: Kind, pool: Pool) -> HTMLResponse:
    async with connection(pool) as conn:
        page = await document_page(conn, release_group_id, kind)
    if page is None:
        raise HTTPException(status_code=404, detail=f"no ready {kind} for this album")
    return HTMLResponse(page)


class RequestIn(BaseModel):
    kind: Kind


@router.post("/albums/{release_group_id}/documents", dependencies=[Depends(page_or_bearer)])
async def request_document(release_group_id: int, body: RequestIn, pool: Pool) -> dict:
    """8.1: ask for a deep dive; one open request per album."""
    async with connection(pool) as conn:
        try:
            out = await worker.request(conn, release_group_id, body.kind)
        except (LookupError, worker.DocumentError) as exc:
            raise _refuse(exc) from exc
    if out["created"]:  # start the worker now; the daily run is the safety net
        out["worker"] = await worker.fire_routine(
            f"Document {out['document_id']} was requested: a {body.kind} for release group "
            f"{release_group_id}."
        )
        log.info("document requested", extra={"document_id": out["document_id"], **out["worker"]})
    return out


@router.get("/albums/{release_group_id}/documents", dependencies=[Depends(page_or_bearer)])
async def album_documents(release_group_id: int, pool: Pool) -> dict:
    async with connection(pool) as conn:
        return await worker.document_states(conn, release_group_id)


# --- the worker (section 8.3), bearer token ---------------------------------------------


@router.get("/documents", dependencies=[Depends(require_token)])
async def documents(pool: Pool, status: Annotated[str, Query()] = "requested") -> list[dict]:
    if status != "requested":
        raise HTTPException(status_code=422, detail="the worker lists requested documents")
    async with connection(pool) as conn:
        return await worker.requested(conn)


@router.post("/documents/{document_id}/start", dependencies=[Depends(require_token)])
async def start(document_id: int, pool: Pool) -> dict:
    async with connection(pool) as conn:
        try:
            return await worker.start(conn, document_id)
        except worker.DocumentError as exc:
            raise _refuse(exc) from exc


class LeaseIn(BaseModel):
    lease_token: str


@router.post("/documents/{document_id}/renew", dependencies=[Depends(require_token)])
async def renew(document_id: int, body: LeaseIn, pool: Pool) -> dict:
    async with connection(pool) as conn:
        try:
            return await worker.renew(conn, document_id, body.lease_token)
        except worker.DocumentError as exc:
            raise _refuse(exc) from exc


@router.put("/documents/{document_id}", dependencies=[Depends(require_token)])
async def finish(document_id: int, pool: Pool, body: Annotated[dict, Body()]) -> dict:
    async with connection(pool) as conn:
        try:
            return await worker.finish(conn, document_id, body)
        except worker.DocumentError as exc:
            raise _refuse(exc) from exc


@router.get("/fetches/{fetch_id}", dependencies=[Depends(require_token)])
async def get_fetch(fetch_id: int, pool: Pool) -> dict:
    async with connection(pool) as conn:
        page = await worker.get_fetch(conn, fetch_id)
    if page is None:
        raise HTTPException(status_code=404, detail="no such cached page")
    return page


class FetchIn(BaseModel):
    url: str
    mode: Literal["plain", "facts", "bandcamp"] = "plain"
    document_id: int


@router.post("/fetches", dependencies=[Depends(require_token)])
async def post_fetch(body: FetchIn, pool: Pool) -> dict:
    """The API fetches the page through Firecrawl and caches it for the document."""
    async with connection(pool) as conn:
        try:
            return await worker.fetch_page(conn, body.url, body.mode, body.document_id)
        except worker.DocumentError as exc:
            raise _refuse(exc) from exc


@router.post("/graph/batches/{label}/reader-verdicts", dependencies=[Depends(require_token)])
async def reader_verdicts(label: str, pool: Pool, verdicts: Annotated[list[dict], Body()]) -> dict:
    if not label.startswith("D"):
        raise HTTPException(status_code=422, detail="the worker posts verdicts for its D<id> batch")
    async with connection(pool) as conn:
        try:
            n = await store_reader_verdicts(conn, label, verdicts)
        except (ValueError, LookupError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"batch": label, "verdicts": n}
