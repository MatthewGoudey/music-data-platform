from __future__ import annotations

import os

import pytest


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Skip integration tests unless DATABASE_URL is set (CI always sets it)."""
    if os.environ.get("DATABASE_URL"):
        return
    skip = pytest.mark.skip(reason="DATABASE_URL not set; integration tests need a real Postgres")
    for item in items:
        if "integration" in item.keywords:
            item.add_marker(skip)
