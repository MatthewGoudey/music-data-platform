"""Paging logic against a fake ListenBrainz (httpx.MockTransport)."""

from __future__ import annotations

from datetime import UTC, datetime

import httpx

from musicdata.clients.listenbrainz import BASE_URL, ListenBrainzClient


def _fake(timestamps: list[int], page_size: int):
    """Serve `timestamps` newest-first, honouring max_ts (exclusive) like the real API."""
    calls: list[int | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/listen-count"):
            return httpx.Response(200, json={"payload": {"count": len(timestamps)}})
        max_ts = request.url.params.get("max_ts")
        calls.append(int(max_ts) if max_ts else None)
        eligible = sorted(
            (t for t in timestamps if max_ts is None or t < int(max_ts)), reverse=True
        )
        page = [{"listened_at": t} for t in eligible[:page_size]]
        headers = {"X-RateLimit-Remaining": "25", "X-RateLimit-Reset-In": "1"}
        return httpx.Response(200, json={"payload": {"listens": page}}, headers=headers)

    return handler, calls


def _client(handler) -> ListenBrainzClient:
    http = httpx.AsyncClient(base_url=BASE_URL, transport=httpx.MockTransport(handler))
    return ListenBrainzClient("someone", user_agent="test", client=http)


async def _collect(client: ListenBrainzClient, stop_before: datetime | None) -> list[int]:
    seen: list[int] = []
    async with client:
        async for page in client.pages_back_to(stop_before):
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
