"""Settings, read once from the environment.

Every job and the API read the same Settings. Locally the values come from a
`.env` file (gitignored); in GitHub Actions and on Fly they come from the
environment's secrets. There is no per-environment branching in code: the
environment is just which DATABASE_URL and MUSICDATA_ENV you were handed.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

Env = Literal["local", "dev", "prod", "test"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        env_prefix="",
    )

    musicdata_env: Env = Field(default="local", description="Which environment this process is.")
    database_url: SecretStr = Field(
        default=SecretStr("postgresql://musicdata:musicdata@localhost:5432/musicdata"),
        description="asyncpg-style URL. Neon: use the pooled endpoint and sslmode=require.",
    )
    database_url_readonly: SecretStr | None = Field(
        default=None, description="Read-only role URL for POST /query. Falls back to database_url."
    )
    api_token: SecretStr = Field(
        default=SecretStr("dev-token-change-me"), description="Bearer token for the API."
    )
    queue_page_token: SecretStr = Field(
        default=SecretStr("dev-queue-token-change-me"),
        description="Token carried in the /queue page URL.",
    )

    listenbrainz_user: str = Field(default="", description="ListenBrainz username to ingest.")
    listenbrainz_token: SecretStr | None = Field(default=None)
    musicbrainz_user_agent: str = Field(
        default="musicdata/0.1 (https://github.com/your-user/music-data-platform)",
        description="MusicBrainz requires a real contact in the User-Agent.",
    )
    lastfm_api_key: SecretStr | None = Field(default=None)
    ticketmaster_api_key: SecretStr | None = Field(default=None)
    google_routes_api_key: SecretStr | None = Field(
        default=None, description="Google Routes API key for venue travel times (ADR 0008)."
    )
    home_address: SecretStr | None = Field(
        default=None, description="Where travel times start from; kept out of the public repo."
    )
    firecrawl_api_key: SecretStr | None = Field(
        default=None, description="Firecrawl key for graph page fetches (GRAPH_SPEC 7.2)."
    )
    discogs_token: SecretStr | None = Field(
        default=None, description="Optional Discogs token: 60 requests/minute instead of 25."
    )
    graph_monthly_credits: int = Field(
        default=100000, description="Firecrawl credits the graph may spend in a calendar month."
    )
    document_credits: int = Field(
        default=100, description="Firecrawl credits one deep dive's page fetches may spend."
    )
    routine_fire_url: str | None = Field(
        default=None,
        description="The document worker routine's API trigger URL (claude.ai/code/routines).",
    )
    routine_fire_token: SecretStr | None = Field(
        default=None, description="The routine trigger's bearer token (shown once when generated)."
    )

    ntfy_topic: str | None = Field(default=None, description="ntfy.sh topic for failure pushes.")
    log_json: bool = Field(default=True)

    @property
    def sqlalchemy_url(self) -> str:
        """The same database as a SQLAlchemy/psycopg URL, for Alembic only."""
        url = self.database_url.get_secret_value()
        if url.startswith("postgresql://"):
            return "postgresql+psycopg://" + url[len("postgresql://") :]
        if url.startswith("postgres://"):
            return "postgresql+psycopg://" + url[len("postgres://") :]
        return url


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
