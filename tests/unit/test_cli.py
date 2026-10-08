from __future__ import annotations

import pytest

from musicdata.jobs.cli import _trigger


@pytest.mark.parametrize(
    ("event", "expected"),
    [(None, "manual"), ("schedule", "schedule"), ("workflow_dispatch", "dispatch"), ("push", "ci")],
)
def test_trigger_follows_the_github_event(monkeypatch, event: str | None, expected: str) -> None:
    if event is None:
        monkeypatch.delenv("GITHUB_EVENT_NAME", raising=False)
    else:
        monkeypatch.setenv("GITHUB_EVENT_NAME", event)
    assert _trigger() == expected


def test_ingest_factory_returns_a_job() -> None:
    from musicdata.ingest.job import ingest

    assert callable(ingest(full=True))
