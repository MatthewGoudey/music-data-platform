"""Starting the worker routine on request (companion spec 8.2): the documented trigger request,
and a failed start that never fails the request."""

from __future__ import annotations

import json

import httpx
import pytest

from musicdata.worker import fire_routine

URL = "https://api.anthropic.com/v1/claude_code/routines/trig_01TEST/fire"


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setenv("ROUTINE_FIRE_URL", URL)
    monkeypatch.setenv("ROUTINE_FIRE_TOKEN", "sk-ant-oat01-test")
    from musicdata.config import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


async def test_fires_the_documented_request(configured) -> None:
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(url=str(request.url), auth=request.headers["Authorization"],
                    version=request.headers["anthropic-version"], body=json.loads(request.content))  # fmt: skip
        return httpx.Response(200, json={"type": "routine_fire",
                                         "claude_code_session_url": "https://claude.ai/code/session_1"})  # fmt: skip

    out = await fire_routine(
        "Document 9 was requested", httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )
    assert out == {"fired": True, "session_url": "https://claude.ai/code/session_1"}
    assert seen == {"url": URL, "auth": "Bearer sk-ant-oat01-test", "version": "2023-06-01",
                    "body": {"text": "Document 9 was requested"}}  # fmt: skip


async def test_a_refused_start_is_reported_not_raised(configured) -> None:
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(429)))
    assert await fire_routine("x", client) == {
        "fired": False,
        "reason": "routine trigger answered 429",
    }


async def test_without_a_trigger_nothing_is_sent(monkeypatch) -> None:
    monkeypatch.delenv("ROUTINE_FIRE_URL", raising=False)
    from musicdata.config import get_settings

    get_settings.cache_clear()
    assert (await fire_routine("x"))["fired"] is False
    get_settings.cache_clear()
