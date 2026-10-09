"""graph fetch: source_fetch (cache, ledger, gaps log) and graph_batch

Revision ID: 0019
Revises: 0018
Create Date: 2026-10-09

docs/graph/GRAPH_SPEC.md section 4. Every Firecrawl fetch is one `source_fetch` row: the page
body, its facts JSON and its credits, so the cache, the monthly credit ledger and the gaps
log (`ok = false`) are one table. `graph_batch` is a set of up to 25 albums handed to a
Claude reading session.
"""

from __future__ import annotations

from alembic import op

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE source_fetch (
            fetch_id        BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            url             TEXT NOT NULL,
            mode            TEXT NOT NULL CHECK (mode IN ('facts','plain','bandcamp')),
            schema_version  TEXT NOT NULL,          -- facts-v2, plain, bandcamp-v1
            entity_id       BIGINT REFERENCES entity (entity_id) ON DELETE SET NULL,
            fetched_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            credits         INTEGER NOT NULL,
            ok              BOOLEAN NOT NULL,
            error           TEXT,                   -- redacted: fc-… keys replaced by fc-***
            title           TEXT,
            body            TEXT,                   -- page markdown; TOAST compresses it
            facts           JSONB,
            pipeline_run_id BIGINT
        );
        CREATE UNIQUE INDEX source_fetch_ok_uq ON source_fetch (url, schema_version) WHERE ok;
        CREATE INDEX source_fetch_month_idx ON source_fetch (fetched_at);

        CREATE TABLE graph_batch (
            batch_id      INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            label         TEXT NOT NULL UNIQUE,     -- P01, P02, …
            slice         TEXT NOT NULL,
            created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
            status        TEXT NOT NULL DEFAULT 'exported' CHECK (status IN
                              ('exported','claims_loaded','reader_exported','reader_loaded','verified')),
            counts        JSONB NOT NULL DEFAULT '{}'::jsonb
        );
        ALTER TABLE assertion ADD FOREIGN KEY (fetch_id)
            REFERENCES source_fetch (fetch_id) ON DELETE SET NULL;
        ALTER TABLE assertion ADD FOREIGN KEY (batch_id)
            REFERENCES graph_batch (batch_id) ON DELETE SET NULL;
        ALTER TABLE graph_album ADD FOREIGN KEY (batch_id)
            REFERENCES graph_batch (batch_id) ON DELETE SET NULL;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        ALTER TABLE graph_album DROP CONSTRAINT IF EXISTS graph_album_batch_id_fkey;
        ALTER TABLE assertion DROP CONSTRAINT IF EXISTS assertion_batch_id_fkey;
        ALTER TABLE assertion DROP CONSTRAINT IF EXISTS assertion_fetch_id_fkey;
        DROP TABLE IF EXISTS graph_batch, source_fetch;
        """
    )
