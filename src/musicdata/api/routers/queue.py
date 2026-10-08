"""The /queue page (Phase 2 version) and verdicts.

Phase 2's page lists recent album sessions that have no verdict yet, each with the
two-tap form: again / later / never and an optional one-line note. The page carries
QUEUE_PAGE_TOKEN in its URL (?t=...) so it opens from a phone bookmark without a header.
The full queue (next ten for a profile, pin, snooze) arrives in Phase 4.
"""

from __future__ import annotations

import secrets
from datetime import datetime
from html import escape
from typing import Annotated, Literal
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel, Field

from musicdata.api.deps import Pool, require_token
from musicdata.config import Settings
from musicdata.db import connection

router = APIRouter(tags=["queue"])

Verdict = Literal["again", "later", "never"]
RECENT_DAYS = 30


def _check_page_token(request: Request, t: str) -> None:
    settings: Settings = request.app.state.settings
    if not secrets.compare_digest(t, settings.queue_page_token.get_secret_value()):
        raise HTTPException(status_code=401, detail="bad or missing page token")


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


PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>musicdata queue</title>
<style>
  :root {{ --bg: #fafaf9; --fg: #1c1917; --muted: #78716c; --card: #fff; --line: #e7e5e4; }}
  @media (prefers-color-scheme: dark) {{
    :root {{ --bg: #1c1917; --fg: #f5f5f4; --muted: #a8a29e; --card: #292524; --line: #44403c; }}
  }}
  body {{ margin: 0; padding: 16px; background: var(--bg); color: var(--fg);
         font: 16px/1.4 system-ui, sans-serif; }}
  h1 {{ font-size: 1.2rem; margin: 0 0 12px; }}
  .card {{ background: var(--card); border: 1px solid var(--line); border-radius: 12px;
          padding: 12px; margin-bottom: 12px; }}
  .meta {{ color: var(--muted); font-size: .85rem; }}
  .row {{ display: flex; gap: 8px; margin-top: 8px; }}
  button {{ flex: 1; padding: 10px; border-radius: 8px; border: 1px solid var(--line);
           background: var(--bg); color: var(--fg); font-size: 1rem; }}
  input[type=text] {{ width: 100%; box-sizing: border-box; margin-top: 8px; padding: 8px;
                     border-radius: 8px; border: 1px solid var(--line);
                     background: var(--bg); color: var(--fg); }}
</style></head><body>
<h1>Recent albums: what next?</h1>
{cards}
</body></html>"""

CARD = """<form class="card" method="post" action="/queue/verdict?t={token}">
  <div><strong>{album}</strong></div>
  <div>{artist}</div>
  <div class="meta">{when} · {kind} · {played}/{count} tracks</div>
  <input type="hidden" name="release_group_id" value="{rg}">
  <input type="hidden" name="session_started_at" value="{started}">
  <input type="text" name="note" maxlength="500" placeholder="One line (optional)">
  <div class="row">
    <button name="verdict" value="again">Again</button>
    <button name="verdict" value="later">Later</button>
    <button name="verdict" value="never">Never</button>
  </div>
</form>"""


@router.get("/queue", response_class=HTMLResponse)
async def queue_page(request: Request, pool: Pool, t: Annotated[str, Query()] = ""):
    """Recent sessions without a verdict, newest first, with the verdict form."""
    _check_page_token(request, t)
    async with connection(pool) as conn:
        rows = await conn.fetch(
            f"""SELECT DISTINCT ON (s.release_group_id)
                       s.release_group_id, s.started_at, s.session_type, s.tracks_played,
                       s.track_count, rg.title, a.name AS artist
                  FROM album_session s
                  JOIN release_group rg USING (release_group_id)
                  JOIN artist a ON a.artist_id = rg.artist_id
                 WHERE s.started_at > now() - interval '{RECENT_DAYS} days'
                   AND NOT EXISTS (SELECT 1 FROM verdict v
                                    WHERE v.release_group_id = s.release_group_id
                                      AND v.created_at > s.started_at)
                 ORDER BY s.release_group_id, s.started_at DESC"""
        )
    rows = sorted(rows, key=lambda r: r["started_at"], reverse=True)
    cards = "\n".join(
        CARD.format(
            token=escape(t, quote=True),
            album=escape(r["title"]),
            artist=escape(r["artist"]),
            when=r["started_at"].strftime("%b %d"),
            kind=r["session_type"],
            played=r["tracks_played"],
            count=r["track_count"],
            rg=r["release_group_id"],
            started=r["started_at"].isoformat(),
        )
        for r in rows
    )
    return HTMLResponse(PAGE.format(cards=cards or "<p>Nothing waiting for a verdict.</p>"))


@router.post("/queue/verdict")
async def queue_verdict(
    request: Request,
    pool: Pool,
    release_group_id: Annotated[int, Form()],
    verdict: Annotated[Verdict, Form()],
    note: Annotated[str | None, Form(max_length=500)] = None,
    session_started_at: Annotated[datetime | None, Form()] = None,
    t: Annotated[str, Query()] = "",
):
    """The page's form target: save the verdict and go back to the page."""
    _check_page_token(request, t)
    await _save(pool, release_group_id, verdict, note, None, session_started_at)
    return RedirectResponse(url=f"/queue?t={quote(t)}", status_code=303)
