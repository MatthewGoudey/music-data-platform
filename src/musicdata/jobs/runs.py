"""pipeline_run bookkeeping: one row per job execution, written before and after."""

from __future__ import annotations

import asyncio
import traceback
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime

import asyncpg

from musicdata.config import get_settings
from musicdata.db import connection, pool_scope
from musicdata.log import get_logger

log = get_logger(__name__)


@dataclass
class RunContext:
    """Handed to every job. Jobs report progress by mutating `rows` and `notes`."""

    pool: asyncpg.Pool
    run_id: int
    job: str
    env: str
    started_at: datetime
    rows: int = 0
    notes: dict[str, object] = field(default_factory=dict)


JobFn = Callable[[RunContext], Awaitable[None]]


async def _start(pool: asyncpg.Pool, job: str, env: str, trigger: str) -> tuple[int, datetime]:
    started = datetime.now(UTC)
    async with connection(pool) as conn:
        run_id = await conn.fetchval(
            """
            INSERT INTO pipeline_run (job, env, trigger, started_at, status)
            VALUES ($1, $2, $3, $4, 'running')
            RETURNING id
            """,
            job,
            env,
            trigger,
            started,
        )
    return int(run_id), started


async def _finish(ctx: RunContext, status: str, error: str | None) -> None:
    async with connection(ctx.pool) as conn:
        await conn.execute(
            """
            UPDATE pipeline_run
               SET finished_at = $2, status = $3, rows_affected = $4, error = $5, notes = $6::jsonb
             WHERE id = $1
            """,
            ctx.run_id,
            datetime.now(UTC),
            status,
            ctx.rows,
            error,
            _json(ctx.notes),
        )


def _json(obj: dict[str, object]) -> str:
    import json

    return json.dumps(obj, default=str)


async def record(job: str, fn: JobFn, *, trigger: str = "manual") -> int:
    """Run `fn` as job `job`, recording start, finish, rows and any error.

    Returns the process exit code (0 ok, 1 failed) so the CLI can propagate it.
    """
    settings = get_settings()
    env = settings.musicdata_env
    async with pool_scope() as pool:
        run_id, started = await _start(pool, job, env, trigger)
        ctx = RunContext(pool=pool, run_id=run_id, job=job, env=env, started_at=started)
        log.info("job start", extra={"job": job, "env": env, "run_id": run_id})
        try:
            await fn(ctx)
        except Exception as exc:
            err = f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}"
            await _finish(ctx, "failed", err)
            log.error(
                "job failed", extra={"job": job, "env": env, "run_id": run_id, "error": str(exc)}
            )
            return 1
        await _finish(ctx, "ok", None)
        elapsed = (datetime.now(UTC) - started).total_seconds()
        log.info(
            "job ok",
            extra={
                "job": job,
                "env": env,
                "run_id": run_id,
                "rows": ctx.rows,
                "seconds": round(elapsed, 1),
            },
        )
        return 0


def run(job: str, fn: JobFn, *, trigger: str = "manual") -> int:
    return asyncio.run(record(job, fn, trigger=trigger))
