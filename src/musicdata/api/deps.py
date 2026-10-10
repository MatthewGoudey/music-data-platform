"""Shared request dependencies: bearer auth, the pool, the date window, and output format.

Conventions from the plan: bearer auth everywhere except /health; `format=compact` by
default (tab-separated text, cheap for Claude to read) with `format=json` for
structured output; ISO dates; empty results rather than errors.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from enum import StrEnum
from typing import Annotated

import asyncpg
from fastapi import Depends, HTTPException, Query, Request, status
from fastapi.responses import JSONResponse, PlainTextResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from musicdata.config import Settings

bearer = HTTPBearer(auto_error=False)


def require_token(
    request: Request,
    creds: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
) -> None:
    settings: Settings = request.app.state.settings
    expected = settings.api_token.get_secret_value()
    if creds is None or creds.credentials != expected:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="bad or missing token")


def check_page_token(request: Request, t: str) -> None:
    settings: Settings = request.app.state.settings
    if not secrets.compare_digest(t, settings.queue_page_token.get_secret_value()):
        raise HTTPException(status_code=401, detail="bad or missing page token")


def page_or_bearer(
    request: Request,
    creds: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
    t: Annotated[str, Query()] = "",
) -> None:
    """The page token in the URL (?t=, so a phone bookmark opens it), or the API bearer token."""
    if t:
        check_page_token(request, t)
    else:
        require_token(request, creds)


def get_pool(request: Request) -> asyncpg.Pool:
    pool = request.app.state.pool
    if pool is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="database unavailable"
        )
    return pool


Pool = Annotated[asyncpg.Pool, Depends(get_pool)]


class Format(StrEnum):
    compact = "compact"
    json = "json"


FormatParam = Annotated[Format, Query(description="compact: tab-separated text; json: rows")]


@dataclass(frozen=True)
class Window:
    """A listened_at window: [start, end). Both None means all time."""

    start: datetime | None
    end: datetime | None
    limit: int


def window(
    start_date: Annotated[date | None, Query(description="First day included (UTC).")] = None,
    end_date: Annotated[date | None, Query(description="Last day included (UTC).")] = None,
    days: Annotated[
        int | None, Query(ge=1, description="The last N days; overrides start_date.")
    ] = None,
    limit: Annotated[int, Query(ge=1, le=5000)] = 50,
) -> Window:
    start = datetime.combine(start_date, time(), UTC) if start_date else None
    end = datetime.combine(end_date + timedelta(days=1), time(), UTC) if end_date else None
    if days:
        start = (end or datetime.now(UTC)) - timedelta(days=days)
    return Window(start=start, end=end, limit=limit)


WindowParam = Annotated[Window, Depends(window)]


def _cell(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.isoformat(timespec="seconds")
    if isinstance(value, float):
        return f"{value:g}"
    return str(value).replace("\t", " ").replace("\n", " ")


def render(rows: list[dict[str, object]] | list[asyncpg.Record], fmt: Format):
    """compact: a header line and one tab-separated line per row; json: a list of objects."""
    data = [dict(r) for r in rows]
    if fmt is Format.json:
        return JSONResponse(content=_jsonable(data))
    if not data:
        return PlainTextResponse("(no rows)\n")
    header = list(data[0])
    lines = ["\t".join(header)] + ["\t".join(_cell(r[k]) for k in header) for r in data]
    return PlainTextResponse("\n".join(lines) + "\n")


def render_object(obj: dict[str, object]) -> JSONResponse:
    """One object (an artist or album page) as JSON."""
    return JSONResponse(content=_jsonable(obj))


def _jsonable(obj: object) -> object:
    from decimal import Decimal
    from uuid import UUID

    if isinstance(obj, dict):
        return {k: _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, list | tuple):
        return [_jsonable(v) for v in obj]
    if isinstance(obj, datetime | date):
        return obj.isoformat()
    if isinstance(obj, Decimal):
        return float(obj)
    if isinstance(obj, UUID):
        return str(obj)
    return obj
