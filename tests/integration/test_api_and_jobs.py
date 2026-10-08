"""Integration tests: need DATABASE_URL pointing at a migrated Postgres (CI provides one)."""

from __future__ import annotations

import asyncio
import os

import asyncpg
import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from musicdata.api.app import app
from musicdata.jobs.cli import app as cli

pytestmark = pytest.mark.integration


async def _max_run_id() -> int:
    conn = await asyncpg.connect(os.environ["DATABASE_URL"])
    try:
        return await conn.fetchval("SELECT coalesce(max(id), 0) FROM pipeline_run")
    finally:
        await conn.close()


async def _delete_runs_after(run_id: int) -> None:
    conn = await asyncpg.connect(os.environ["DATABASE_URL"])
    try:
        # Only the rows this process wrote: cloud jobs run as dev or prod, tests as local/test.
        await conn.execute(
            "DELETE FROM pipeline_run WHERE id > $1 AND env IN ('local', 'test')", run_id
        )
    finally:
        await conn.close()


@pytest.fixture(autouse=True)
def _token(monkeypatch):
    """A test token, and no pipeline_run rows left behind (dev's /ops/status stays honest)."""
    monkeypatch.setenv("API_TOKEN", "test-token")
    from musicdata.config import get_settings

    get_settings.cache_clear()
    before = asyncio.run(_max_run_id())
    yield
    asyncio.run(_delete_runs_after(before))
    get_settings.cache_clear()


def test_health_needs_no_token() -> None:
    with TestClient(app) as client:
        r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"
    assert r.json()["database"] == "up"


def test_ops_status_requires_token() -> None:
    with TestClient(app) as client:
        assert client.get("/ops/status").status_code == 401
        r = client.get("/ops/status", headers={"Authorization": "Bearer test-token"})
    assert r.status_code == 200
    assert set(r.json()) >= {"healthy", "failing", "jobs", "dq"}


def test_noop_job_records_a_run() -> None:
    runner = CliRunner()
    result = runner.invoke(cli, ["shows"])
    assert result.exit_code == 0, result.output
    with TestClient(app) as client:
        r = client.get("/ops/status", headers={"Authorization": "Bearer test-token"})
    jobs = r.json()["jobs"]
    assert jobs["shows"]["status"] == "ok"


def test_forced_failure_is_recorded_and_exits_nonzero() -> None:
    runner = CliRunner()
    result = runner.invoke(cli, ["dq", "--fail"])
    assert result.exit_code == 1
    with TestClient(app) as client:
        r = client.get("/ops/status", headers={"Authorization": "Bearer test-token"})
    body = r.json()
    assert body["jobs"]["dq"]["status"] == "failed"
    assert "dq" in body["failing"]
    assert body["healthy"] is False


def test_database_url_is_set_for_these_tests() -> None:
    assert os.environ.get("DATABASE_URL")
