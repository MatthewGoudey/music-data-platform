"""FastAPI application: /health, /ops/status, and the routers added per phase.

Auth is a bearer token for everything except /health and /openapi.json. The
/queue page (Phase 2+) carries its own token in the URL instead.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Annotated

import asyncpg
from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from musicdata import __version__
from musicdata.config import Settings, get_settings
from musicdata.db import connection, create_pool
from musicdata.log import configure_logging, get_logger

log = get_logger(__name__)
bearer = HTTPBearer(auto_error=False)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    configure_logging(json_lines=settings.log_json)
    app.state.settings = settings
    app.state.pool = None
    try:
        app.state.pool = await create_pool(min_size=1, max_size=5)
    except Exception as exc:  # the API still answers /health without a database
        log.error("database unavailable at startup", extra={"error": str(exc)})
    yield
    if app.state.pool is not None:
        await app.state.pool.close()


app = FastAPI(
    title="musicdata",
    version=__version__,
    description="Personal listening tracker: ListenBrainz in, a queue out.",
    lifespan=lifespan,
)


def require_token(
    request: Request,
    creds: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
) -> None:
    settings: Settings = request.app.state.settings
    expected = settings.api_token.get_secret_value()
    if creds is None or creds.credentials != expected:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="bad or missing token")


def get_pool(request: Request) -> asyncpg.Pool:
    pool = request.app.state.pool
    if pool is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="database unavailable"
        )
    return pool


@app.get("/health", tags=["ops"])
async def health(request: Request) -> dict[str, object]:
    settings: Settings = request.app.state.settings
    return {
        "status": "ok",
        "version": __version__,
        "env": settings.musicdata_env,
        "database": "up" if request.app.state.pool is not None else "down",
        "time": datetime.now(UTC).isoformat(timespec="seconds"),
    }


@app.get("/ops/status", tags=["ops"], dependencies=[Depends(require_token)])
async def ops_status(pool: Annotated[asyncpg.Pool, Depends(get_pool)]) -> dict[str, object]:
    """Last run per job in this environment, plus the latest data-quality results."""
    async with connection(pool) as conn:
        runs = await conn.fetch(
            """
            SELECT DISTINCT ON (job) job, status, started_at, finished_at, rows_affected, error, trigger
              FROM pipeline_run
             ORDER BY job, started_at DESC
            """
        )
        checks = await conn.fetch(
            """
            SELECT DISTINCT ON (check_name) check_name, passed, observed, threshold, checked_at
              FROM dq_result
             ORDER BY check_name, checked_at DESC
            """
        )
    jobs = {
        r["job"]: {
            "status": r["status"],
            "started_at": r["started_at"],
            "finished_at": r["finished_at"],
            "rows": r["rows_affected"],
            "trigger": r["trigger"],
            "error": (r["error"] or "")[:300] or None,
        }
        for r in runs
    }
    dq = {
        c["check_name"]: {
            "passed": c["passed"],
            "observed": c["observed"],
            "threshold": c["threshold"],
            "checked_at": c["checked_at"],
        }
        for c in checks
    }
    failing = [j for j, v in jobs.items() if v["status"] == "failed"] + [
        c for c, v in dq.items() if v["passed"] is False
    ]
    return {"healthy": not failing, "failing": failing, "jobs": jobs, "dq": dq}
