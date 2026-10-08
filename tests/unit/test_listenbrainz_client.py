"""Paging logic against a fake ListenBrainz (httpx.MockTransport)."""

from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest

from musicdata.clients.listenbrainz import BASE_URL, ListenBrainzClient


def _fake(timestamps: list[int], page_size: int, slow_above: int | None = None):
    """Serve `timestamps` newest-first, honouring max_ts (exclusive) like the real API.

    With `slow_above`, any request for more than that many listens times out.
    """
    calls: list[int | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/listen-count"):
            return httpx.Response(200, json={"payload": {"count": len(timestamps)}})
        if slow_above is not None and int(request.url.params["count"]) > slow_above:
            raise httpx.ReadTimeout("slow page", request=request)
        max_ts = request.url.params.get("max_ts")
        calls.append(int(max_ts) if max_ts else None)
        eligible = sorted(
            (t for t in timestamps if max_ts is None or t < int(max_ts)), reverse=True
        )
        count = int(request.url.params["count"])
        page = [{"listened_at": t} for t in eligible[: min(page_size, count)]]
        headers = {"X-RateLimit-Remaining": "25", "X-RateLimit-Reset-In": "1"}
        return httpx.Response(200, json={"payload": {"listens": page}}, headers=headers)

    return handler, calls


async def _no_wait(seconds: float) -> None:
    return None


def _client(handler) -> ListenBrainzClient:
    http = httpx.AsyncClient(base_url=BASE_URL, transport=httpx.MockTransport(handler))
    return ListenBrainzClient("someone", user_agent="test", client=http, sleep=_no_wait)


async def _collect(
    client: ListenBrainzClient, stop_before: datetime | None, start_before: datetime | None = None
) -> list[int]:
    seen: list[int] = []
    async with client:
        async for page in client.pages_back_to(stop_before, start_before=start_before):
            seen += [listen["listened_at"] for listen in page]
    return seen


async def test_full_history_reaches_the_oldest_listen() -> None:
    timestamps = list(range(1000, 1050))
    handler, _ = _fake(timestamps, page_size=7)
    seen = await _collect(_client(handler), None)
    assert set(seen) == set(timestamps)


async def test_listens_sharing_a_second_across_a_page_break_are_kept() -> None:
    timestamps = [100, 100, 100, 99, 98, 98, 97]
    handler, _ = _fake(timestamps, page_size=2)
    seen = await _collect(_client(handler), None)
    assert seen.count(100) >= 3 and set(seen) == {97, 98, 99, 100}


async def test_incremental_stops_below_the_watermark() -> None:
    timestamps = list(range(1000, 1100))
    handler, calls = _fake(timestamps, page_size=10)
    seen = await _collect(_client(handler), datetime.fromtimestamp(1085, tz=UTC))
    assert min(seen) < 1085
    assert len(calls) == 2


async def test_listen_count() -> None:
    handler, _ = _fake([1, 2, 3], page_size=10)
    async with _client(handler) as client:
        assert await client.listen_count() == 3


async def test_a_timeout_shrinks_the_page_and_carries_on() -> None:
    timestamps = list(range(1000, 1600))
    handler, _ = _fake(timestamps, page_size=1000, slow_above=250)
    seen = await _collect(_client(handler), None)
    assert set(seen) == set(timestamps)


async def test_a_full_load_resumes_below_the_oldest_stored_listen() -> None:
    timestamps = list(range(1000, 1100))
    handler, calls = _fake(timestamps, page_size=10)
    seen = await _collect(_client(handler), None, datetime.fromtimestamp(1050, tz=UTC))
    assert calls[0] == 1051
    assert max(seen) == 1050 and min(seen) == 1000


async def test_an_outage_is_waited_out(monkeypatch) -> None:
    import musicdata.clients.listenbrainz as lb

    monkeypatch.setattr(lb.asyncio, "sleep", _no_wait)  # the client's own retry backoff
    timestamps = list(range(1000, 1020))
    serve, _ = _fake(timestamps, page_size=1000)
    failures = {"left": 12}  # more than the shrinking alone absorbs

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/listens") and failures["left"] > 0:
            failures["left"] -= 1
            return httpx.Response(502)
        return serve(request)

    seen = await _collect(_client(handler), None)
    assert set(seen) == set(timestamps)


async def test_a_long_outage_still_fails(monkeypatch) -> None:
    import musicdata.clients.listenbrainz as lb

    monkeypatch.setattr(lb.asyncio, "sleep", _no_wait)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(502)

    with pytest.raises(httpx.HTTPStatusError):
        await _collect(_client(handler), None)
