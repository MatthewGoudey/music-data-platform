"""album_document: liner notes and deep dives, versioned per album

Revision ID: 0024
Revises: 0023
Create Date: 2026-10-10

docs/graph/COMPANION_SPEC.md section 10 (Block F's documents table, taken early so Matt can read
the first deep dive on dev behind the page token; Block F adds the request and worker endpoints).
`citations` maps each marker (`c:<assertion_id>`, `p:<fetch_id or page id>`) to what the page
links: the claim or page id, its URL, a short source label and a title.
"""

from __future__ import annotations

from alembic import op

revision = "0024"
down_revision = "0023"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE album_document (
            document_id       INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            release_group_id  INTEGER NOT NULL
                              REFERENCES release_group (release_group_id) ON DELETE CASCADE,
            kind              TEXT NOT NULL CHECK (kind IN ('liner_notes','deep_dive')),
            version           INTEGER NOT NULL,
            status            TEXT NOT NULL DEFAULT 'requested'
                              CHECK (status IN ('requested','writing','ready','failed','superseded')),
            requested_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            attempts          INTEGER NOT NULL DEFAULT 0,
            lease_token       TEXT,
            lease_until       TIMESTAMPTZ,
            finished_at       TIMESTAMPTZ,
            model             TEXT,
            body_md           TEXT,
            citations         JSONB NOT NULL DEFAULT '{}'::jsonb,
            checks            JSONB NOT NULL DEFAULT '{}'::jsonb,
            claims_through    BIGINT,
            firecrawl_credits INTEGER NOT NULL DEFAULT 0,
            error             TEXT,
            UNIQUE (release_group_id, kind, version)
        );
        CREATE UNIQUE INDEX album_document_open_uq ON album_document (release_group_id, kind)
            WHERE status IN ('requested','writing');
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS album_document;")
