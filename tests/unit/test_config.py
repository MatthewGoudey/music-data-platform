from __future__ import annotations

from musicdata.config import Settings


def test_sqlalchemy_url_adds_driver(monkeypatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@host/db?sslmode=require")
    s = Settings(_env_file=None)
    assert s.sqlalchemy_url == "postgresql+psycopg://u:p@host/db?sslmode=require"


def test_secrets_do_not_leak_in_repr(monkeypatch) -> None:
    monkeypatch.setenv("API_TOKEN", "super-secret")
    s = Settings(_env_file=None)
    assert "super-secret" not in repr(s)
    assert s.api_token.get_secret_value() == "super-secret"


def test_env_defaults_to_local(monkeypatch) -> None:
    monkeypatch.delenv("MUSICDATA_ENV", raising=False)
    assert Settings(_env_file=None).musicdata_env == "local"


def test_graph_keys_are_optional_and_never_in_repr(monkeypatch) -> None:
    for name in ("FIRECRAWL_API_KEY", "DISCOGS_TOKEN", "GRAPH_MONTHLY_CREDITS"):
        monkeypatch.delenv(name, raising=False)
    s = Settings(_env_file=None)
    assert s.firecrawl_api_key is None and s.discogs_token is None
    assert s.graph_monthly_credits == 15000
    monkeypatch.setenv("FIRECRAWL_API_KEY", "fc-" + "0" * 32)
    s = Settings(_env_file=None)
    assert s.firecrawl_api_key.get_secret_value().startswith("fc-")
    assert "fc-000" not in repr(s)
