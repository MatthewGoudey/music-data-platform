"""One asyncpg pool factory with reconnect, shared by the API and the jobs.

The old pipeline lost ~30 nightly runs to Neon dropping idle connections. Two
habits fix that: acquire a connection per unit of work (never hold one across a
long loop), and retry pool creation and transient errors with backoff.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import asyncpg

from musicdata.config import get_settings
from musicdata.log import get_logger

log = get_logger(__name__)

_TRANSIENT = (
    asyncpg.PostgresConnectionError,
    asyncpg.InterfaceError,
    ConnectionError,
    OSError,
    asyncio.TimeoutError,
)


async def create_pool(
    url: str | None = None, *, min_size: int = 1, max_size: int = 5
) -> asyncpg.Pool:
    """Create a pool, retrying with backoff (1s, 2s, 4s, 8s, 16s)."""
    dsn = url or get_settings().database_url.get_secret_value()
    delay = 1.0
    for attempt in range(1, 6):
        try:
            return await asyncpg.create_pool(
                dsn, min_size=min_size, max_size=max_size, command_timeout=60, timeout=20
            )
        except _TRANSIENT as exc:  # pragma: no cover - needs a flaky network
            if attempt == 5:
                raise
            log.warning(
                "db pool create failed; retrying", extra={"attempt": attempt, "error": str(exc)}
            )
            await asyncio.sleep(delay)
            delay *= 2
    raise RuntimeError("unreachable")


@asynccontextmanager
async def connection(pool: asyncpg.Pool) -> AsyncIterator[asyncpg.Connection]:
    """Acquire a live connection for one unit of work. A pooled connection the server has
    dropped is found by a probe and replaced once; a drop during the work itself is
    raised to the caller (a context manager cannot run its body twice)."""
    for attempt in (1, 2):
        held = pool.acquire()
        conn = await held.__aenter__()
        try:
            await conn.execute("SELECT 1")
            break
        except _TRANSIENT as exc:
            await held.__aexit__(type(exc), exc, exc.__traceback__)
            if attempt == 2:
                raise
            log.warning("db connection dropped; retrying once", extra={"error": str(exc)})
            await asyncio.sleep(1)
    try:
        yield conn
    except BaseException as exc:
        await held.__aexit__(type(exc), exc, exc.__traceback__)
        raise
    else:
        await held.__aexit__(None, None, None)


@asynccontextmanager
async def pool_scope(**kwargs) -> AsyncIterator[asyncpg.Pool]:
    """Create a pool for the lifetime of a job and close it afterwards."""
    pool = await create_pool(**kwargs)
    try:
        yield pool
    finally:
        await pool.close()
