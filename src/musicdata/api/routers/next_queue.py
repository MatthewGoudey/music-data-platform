"""`GET /next`: the queue as JSON, or compact text (docs/QUEUE_SPEC.md sections 8 and 12)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query

from musicdata.api.deps import Format, FormatParam, Pool, render, render_object, require_token
from musicdata.db import connection
from musicdata.queue import config
from musicdata.queue.engine import UnknownProfileError, next_queue

router = APIRouter(tags=["queue"], dependencies=[Depends(require_token)])


def parse_ids(text: str | None) -> frozenset[int]:
    if not text:
        return frozenset()
    try:
        return frozenset(int(x) for x in text.split(",") if x.strip())
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="exclude takes comma-separated ids") from exc


@router.get("/next")
async def next_albums(
    pool: Pool,
    profile: str = "default",
    n: Annotated[int | None, Query(ge=1, le=config.MAX_N)] = None,
    shuffle: bool = False,
    seed: Annotated[int | None, Query(ge=0)] = None,
    exclude: Annotated[
        str | None, Query(description="Release group ids kept out of shuffled new slots.")
    ] = None,
    format: FormatParam = Format.json,
):
    """What to hear next: pins, new albums from the lists, revisits and a wildcard, each
    with the reason it is here. `shuffle=true` draws the new slots from the top 200."""
    async with connection(pool) as conn:
        try:
            q = await next_queue(
                conn, profile, n=n, shuffle=shuffle, seed=seed, exclude=parse_ids(exclude)
            )
        except UnknownProfileError as exc:
            raise HTTPException(status_code=404, detail=f"no profile {exc}") from exc
    if format is Format.json:
        return render_object(q)
    return render(
        [
            {
                "slot": i["slot"],
                "artist": i["artist"],
                "album": i["album"],
                "year": i["year"],
                "status": i["status"],
                "score": i["score"],
                "why": i["why_line"],
                "release_group_id": i["release_group_id"],
            }
            for i in q["items"]
        ],
        format,
    )
