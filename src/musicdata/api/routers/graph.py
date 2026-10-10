"""The music graph's API (companion spec section 11, Block A; graph spec section 10): entities,
an album's graph and brief, claims and links posted by Claude sessions, reading questions and
coverage. Bearer token, like every router; the pages that use the page token arrive in Block E.
"""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Body, Depends, HTTPException, Query

from musicdata.api.deps import Format, FormatParam, Pool, render, render_object, require_token
from musicdata.db import connection
from musicdata.graph import queries
from musicdata.graph.answers import answer as record_answer
from musicdata.graph.report import coverage

router = APIRouter(tags=["graph"], dependencies=[Depends(require_token)])


@router.get("/entities")
async def entities(
    pool: Pool,
    q: Annotated[str, Query(min_length=2, description="Name, matched by trigram and substring.")],
    type: Annotated[str | None, Query(description="album, person, artist, label, place, …")] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 25,
    format: FormatParam = Format.compact,
):
    """Entities by name: people, bands, albums, labels, studios, songs."""
    async with connection(pool) as conn:
        rows = await queries.search_entities(conn, q, type, limit)
    return render(rows, format)


@router.get("/entities/{entity_id}")
async def entity(entity_id: int, pool: Pool):
    """The entity, its links and map coordinates, and its edges grouped by facet."""
    async with connection(pool) as conn:
        body = await queries.entity_detail(conn, entity_id)
    if body is None:
        raise HTTPException(status_code=404, detail="no such entity")
    return render_object(body)


@router.get("/entities/{entity_id}/neighbors")
async def entity_neighbors(
    entity_id: int,
    pool: Pool,
    predicates: Annotated[
        str | None, Query(description="Comma-separated, e.g. credited_on,member_of")
    ] = None,
    direction: Literal["in", "out", "both"] = "both",
    min_confidence: Annotated[float, Query(ge=0, le=1)] = 0.5,
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
    format: FormatParam = Format.compact,
):
    """Adjacent entities through accepted edges."""
    preds = [p.strip() for p in predicates.split(",") if p.strip()] if predicates else None
    async with connection(pool) as conn:
        rows = await queries.neighbors(conn, entity_id, preds, direction, min_confidence, limit)
    return render(rows, format)


@router.get("/albums/{release_group_id}/graph")
async def album_graph(release_group_id: int, pool: Pool):
    """The album's edges grouped by facet, each with sources and confidence."""
    async with connection(pool) as conn:
        album_id = await queries.album_entity(conn, release_group_id)
        body = await queries.entity_detail(conn, album_id) if album_id else None
    if body is None:
        raise HTTPException(status_code=404, detail="the graph holds no such album")
    return render_object(body)


@router.get("/albums/{release_group_id}/brief")
async def album_brief(release_group_id: int, pool: Pool):
    """One packet to work from: identity and tracks with their credits, where it sits, edges by
    facet, cached pages, links, gaps and research questions, Matt's context, a budget hint."""
    async with connection(pool) as conn:
        body = await queries.album_brief(conn, release_group_id)
    if body is None:
        raise HTTPException(status_code=404, detail="no such album")
    return render_object(body)


@router.post("/assertions")
async def post_assertions(pool: Pool, claims: Annotated[list[dict], Body()]):
    """Proposed claims in the research skill's JSON shape (each with its claim_id); names are
    stored as written and resolved later. Returns claim label → assertion id."""
    async with connection(pool) as conn:
        try:
            ids = await queries.post_assertions(conn, claims)
        except queries.GraphInputError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    return render_object({"assertions": ids})


@router.post("/links")
async def post_link(
    pool: Pool,
    entity_id: Annotated[int, Body()],
    kind: Annotated[str, Body()],
    url: Annotated[str, Body()],
    source: Annotated[str, Body()],
):
    """A registered link for an entity (English pages only)."""
    async with connection(pool) as conn:
        try:
            body = await queries.post_link(conn, entity_id, kind, url, source)
        except queries.GraphInputError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    return render_object(body)


@router.get("/assertions")
async def assertions(
    pool: Pool,
    ids: Annotated[str, Query(description="Comma-separated assertion ids.")],
    format: FormatParam = Format.json,
):
    """Claims by id, with evidence and source (for a fact-check)."""
    try:
        wanted = [int(x) for x in ids.split(",") if x.strip()]
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="ids are integers") from exc
    async with connection(pool) as conn:
        rows = await queries.assertions_by_id(conn, wanted[:500])
    return render(rows, format)


@router.get("/graph/questions")
async def questions(pool: Pool, format: FormatParam = Format.json):
    """Open reading questions: the claim in plain words, the quote, the source, the reader's
    reason. Answer from the quote alone."""
    async with connection(pool) as conn:
        rows = await queries.open_questions(conn)
    return render(rows, format)


@router.post("/graph/questions/{assertion_id}/answer")
async def answer_question(
    assertion_id: int,
    pool: Pool,
    answer: Annotated[Literal["yes", "no", "skip"], Body(embed=True)],
    qualifiers: Annotated[dict | None, Body(embed=True)] = None,
):
    """yes or no writes a claim under Matt's name and closes the question; skip leaves it open."""
    async with connection(pool) as conn:
        label = await conn.fetchval(
            "SELECT claim_label FROM assertion WHERE assertion_id = $1 AND status = 'ask_matt'",
            assertion_id,
        )
        if label is None:
            raise HTTPException(status_code=404, detail="no open question with that id")
        new = await record_answer(conn, label, answer, qualifiers)
    return render_object({"assertion_id": assertion_id, "answer": answer, "matt_claim": new})


@router.get("/graph/coverage")
async def graph_coverage(
    pool: Pool,
    slice: Annotated[str, Query(description="crazy_horse, v_atlas or album:<id>")] = "crazy_horse",
):
    """Coverage per facet, claims by status, predicate and source, gaps and no-match albums."""
    async with connection(pool) as conn:
        try:
            body = await coverage(conn, slice)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
    return render_object(body)
