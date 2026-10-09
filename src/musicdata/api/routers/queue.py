"""The /queue page: Up next (docs/QUEUE_SPEC.md sections 11 and 13), and the dormant
verdict endpoint.

The page is a phone-width shell (api/static/queue.html) that reads `GET /queue/data` and
acts through `POST /queue/{release_group_id}/{action}`. It carries QUEUE_PAGE_TOKEN in its
URL (?t=...) so it opens from a phone bookmark without a header; the data and action
endpoints accept that token or the API bearer token. Opening the page also starts a
rate-limited catch-up ingest, so an album finished an hour ago has already left.

Verdicts are dormant (QUEUE_SPEC.md section 11): the table, POST /verdicts and the album
page's verdict list stay, and nothing on the page or in the queue uses them.
"""

from __future__ import annotations

import secrets
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.security import HTTPAuthorizationCredentials
from pydantic import BaseModel, Field

from musicdata.api.deps import Pool, bearer, render_object, require_token
from musicdata.api.routers.next_queue import parse_ids
from musicdata.config import Settings
from musicdata.db import connection
from musicdata.lists.progress import progress
from musicdata.queue import config
from musicdata.queue.engine import UnknownProfileError, next_queue

router = APIRouter(tags=["queue"])

Verdict = Literal["again", "later", "never"]
Action = Literal["pin", "unpin", "bump", "snooze", "unsnooze", "hide", "unhide", "played"]
PAGE = (Path(__file__).parents[1] / "static" / "queue.html").read_text(encoding="utf-8")
UNDO_MINUTES = 60  # a manual session can be taken back this long after it was recorded


def _check_page_token(request: Request, t: str) -> None:
    settings: Settings = request.app.state.settings
    if not secrets.compare_digest(t, settings.queue_page_token.get_secret_value()):
        raise HTTPException(status_code=401, detail="bad or missing page token")


def page_or_bearer(
    request: Request,
    creds: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
    t: Annotated[str, Query()] = "",
) -> None:
    """The page token in the URL, or the API bearer token."""
    if t:
        _check_page_token(request, t)
    else:
        require_token(request, creds)


async def _save(
    pool,
    release_group_id: int,
    verdict: str,
    note: str | None,
    rating: int | None,
    session_started_at: datetime | None,
) -> int:
    async with connection(pool) as conn:
        if not await conn.fetchval(
            "SELECT 1 FROM release_group WHERE release_group_id = $1", release_group_id
        ):
            raise HTTPException(status_code=404, detail="no such album")
        return await conn.fetchval(
            """INSERT INTO verdict (release_group_id, verdict, rating, note, session_started_at)
               VALUES ($1, $2, $3, $4, $5) RETURNING verdict_id""",
            release_group_id,
            verdict,
            rating,
            note or None,
            session_started_at,
        )


class VerdictIn(BaseModel):
    release_group_id: int
    verdict: Verdict
    rating: int | None = Field(None, ge=1, le=5)
    note: str | None = Field(None, max_length=500)
    session_started_at: datetime | None = None


@router.post("/verdicts", status_code=201, dependencies=[Depends(require_token)])
async def add_verdict(body: VerdictIn, pool: Pool):
    """Record a verdict on an album; the latest per album is the current one."""
    verdict_id = await _save(
        pool, body.release_group_id, body.verdict, body.note, body.rating, body.session_started_at
    )
    return {"verdict_id": verdict_id}


def history_line(
    full_sessions: int | None,
    best_completion: float | None,
    tracks_heard: int | None,
    track_count: int | None,
) -> str:
    """The album's whole history in one line: finished or not, and how much of the
    standard tracklist has ever been heard across every play."""
    heard = ""
    if track_count:
        heard = (
            "every track heard"
            if (tracks_heard or 0) >= track_count
            else f"{tracks_heard or 0} of {track_count} tracks ever heard"
        )
    if full_sessions:
        times = "once" if full_sessions == 1 else f"{full_sessions}×"
        return " · ".join(x for x in (f"Finished {times}", heard) if x)
    best = f"best {round(float(best_completion or 0) * 100)}%"
    return " · ".join(x for x in ("Not finished yet", best, heard) if x)


@router.get("/queue", response_class=HTMLResponse)
async def queue_page(request: Request, pool: Pool, t: Annotated[str, Query()] = ""):
    """Up next: the queue for a profile, its actions, and progress through the lists."""
    _check_page_token(request, t)
    from musicdata.api.routers.ingest import start_catch_up

    await start_catch_up(pool)
    return HTMLResponse(PAGE)


async def _check_status(pool) -> dict[str, object]:
    from musicdata.api.routers.ingest import _running

    async with connection(pool) as conn:
        last = await conn.fetchrow(
            """SELECT finished_at, notes->>'listens_inserted' AS new_listens
                 FROM pipeline_run
                WHERE job = 'ingest' AND status = 'ok'
                ORDER BY started_at DESC LIMIT 1"""
        )
        running = bool(_running) or bool(
            await conn.fetchval(
                """SELECT 1 FROM pipeline_run
                    WHERE job IN ('ingest', 'derive') AND status = 'running'
                      AND started_at > now() - interval '15 minutes' LIMIT 1"""
            )
        )
    return {
        "running": running,
        "last_checked": last["finished_at"] if last else None,
        "new_listens": int(last["new_listens"] or 0) if last else 0,
    }


@router.post("/queue/check-listens", dependencies=[Depends(page_or_bearer)])
async def check_listens(pool: Pool):
    """Pull the newest listens from ListenBrainz and rebuild their sessions (about 20
    seconds). At most once every five minutes; a press inside that window starts nothing."""
    from musicdata.api.routers.ingest import start_catch_up

    started = await start_catch_up(pool)
    return render_object({"started": started} | await _check_status(pool))


@router.get("/queue/check-listens", dependencies=[Depends(page_or_bearer)])
async def check_listens_status(pool: Pool):
    """Whether a catch-up is running, when listens were last pulled, and how many were new."""
    return render_object(await _check_status(pool))


@router.get("/queue/data", dependencies=[Depends(page_or_bearer)])
async def queue_data(
    pool: Pool,
    profile: str = "default",
    shuffle: bool = False,
    seed: Annotated[int | None, Query(ge=0)] = None,
    exclude: str | None = None,
):
    """Everything the page shows: the queue with a history line per album, the profiles,
    and heard / total per list and for the atlas's Core zone."""
    async with connection(pool) as conn:
        try:
            q = await next_queue(
                conn, profile, shuffle=shuffle, seed=seed, exclude=parse_ids(exclude)
            )
        except UnknownProfileError as exc:
            raise HTTPException(status_code=404, detail=f"no profile {exc}") from exc
        ids = [i["release_group_id"] for i in q["items"]]
        stats = {
            r["release_group_id"]: r
            for r in await conn.fetch(
                """SELECT release_group_id, full_sessions, best_completion, tracks_heard,
                          track_count
                     FROM release_group_stat WHERE release_group_id = ANY($1::int[])""",
                ids,
            )
        }
        pinned = {
            r[0]
            for r in await conn.fetch(
                """SELECT release_group_id FROM queue_state
                    WHERE pinned_at IS NOT NULL AND release_group_id = ANY($1::int[])""",
                ids,
            )
        }
        profiles = [
            dict(r)
            for r in await conn.fetch(
                "SELECT name, description FROM queue_profile ORDER BY name <> 'default', name"
            )
        ]
        tag_names = [
            r[0] for r in await conn.fetch("SELECT name FROM tag WHERE active ORDER BY tag_id")
        ]
        lists = await progress(conn, "list")
        core = await progress(conn, "zone", slug="v_atlas", zone="Core")
    for item in q["items"]:
        s = stats.get(item["release_group_id"])
        item["history"] = (
            history_line(
                s["full_sessions"], s["best_completion"], s["tracks_heard"], s["track_count"]
            )
            if s
            else "Never played"
        )
        item["pinned"] = item["release_group_id"] in pinned
    labels = config.LIST_LABELS
    strip = [
        {"label": labels.get(r["list"], r["name"]), "heard": r["heard"], "total": r["total"]}
        for r in lists
    ]
    strip += [{"label": "V Atlas · Core", "heard": r["heard"], "total": r["total"]} for r in core]
    return render_object(
        q
        | {
            "profiles": profiles,
            "progress": strip,
            "tags": tag_names,
            "shuffle_pool": config.SHUFFLE_POOL,
        }
    )


STATE = {
    "pin": "pinned_at = now()",
    "unpin": "pinned_at = NULL",
    "bump": f"bumped_until = now() + interval '{config.BUMP_DAYS} days'",
    "snooze": "snoozed_until = now() + make_interval(days => $2)",
    "unsnooze": "snoozed_until = NULL",
    "hide": "hidden_at = now(), pinned_at = NULL",
    "unhide": "hidden_at = NULL",
}


@router.post("/queue/{release_group_id}/{action}", dependencies=[Depends(page_or_bearer)])
async def queue_action(
    release_group_id: int,
    action: Action,
    pool: Pool,
    days: Annotated[int, Query(ge=1, le=365)] = 30,
):
    """Pin, unpin, bump (score × 2 for 14 days), snooze (`days`), unsnooze, hide ("not for
    me"), unhide, or played: a manual full session now, for vinyl or a show."""
    async with connection(pool) as conn:
        if not await conn.fetchval(
            "SELECT 1 FROM release_group WHERE release_group_id = $1", release_group_id
        ):
            raise HTTPException(status_code=404, detail="no such album")
        if action == "played":
            count = (
                await conn.fetchval(
                    """SELECT track_count FROM release_group_tracklist
                        WHERE release_group_id = $1 AND source <> 'unresolved'""",
                    release_group_id,
                )
                or 1
            )
            session_id = await conn.fetchval(
                """INSERT INTO album_session (release_group_id, started_at, ended_at,
                                              tracks_played, track_count, completion,
                                              session_type, source, note)
                   VALUES ($1, $2, $2, $3, $3, 1.0, 'full', 'manual', 'marked played on the page')
                   RETURNING session_id""",
                release_group_id,
                datetime.now(UTC),
                count,
            )
            return {"action": action, "session_id": session_id}
        sql = STATE[action]
        args: list[object] = [release_group_id]
        if "$2" in sql:
            args.append(days)
        await conn.execute(
            "INSERT INTO queue_state (release_group_id) VALUES ($1) ON CONFLICT DO NOTHING",
            release_group_id,
        )
        await conn.execute(
            f"UPDATE queue_state SET {sql}, updated_at = now() WHERE release_group_id = $1",
            *args,
        )
    return {"action": action, "release_group_id": release_group_id}


@router.post("/queue/sessions/{session_id}/undo", dependencies=[Depends(page_or_bearer)])
async def undo_played(session_id: int, pool: Pool):
    """Take back a "Mark played" made in the last hour (a mis-tap)."""
    async with connection(pool) as conn:
        gone = await conn.fetchval(
            f"""DELETE FROM album_session
                 WHERE session_id = $1 AND source = 'manual'
                   AND created_at > now() - interval '{UNDO_MINUTES} minutes'
                RETURNING session_id""",
            session_id,
        )
    if gone is None:
        raise HTTPException(status_code=404, detail="no recent manual session with that id")
    return {"undone": session_id}
