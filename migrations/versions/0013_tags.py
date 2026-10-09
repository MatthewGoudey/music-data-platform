"""tags: tag (seeded), release_group_tag

Revision ID: 0013
Revises: 0012
Create Date: 2026-10-09

docs/QUEUE_SPEC.md section 4. Tags key on release_group_id; suggested tags stay
`suggested` until Matt confirms them.
"""

from __future__ import annotations

from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE tag (
            tag_id       INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            name         TEXT    NOT NULL UNIQUE,
            description  TEXT,
            active       BOOLEAN NOT NULL DEFAULT true
        );

        CREATE TABLE release_group_tag (
            release_group_id  INTEGER NOT NULL
                              REFERENCES release_group (release_group_id) ON DELETE CASCADE,
            tag_id            INTEGER NOT NULL REFERENCES tag (tag_id) ON DELETE CASCADE,
            source            TEXT    NOT NULL
                              CHECK (source IN ('manual', 'playlist', 'rule', 'suggested')),
            status            TEXT    NOT NULL DEFAULT 'applied'
                              CHECK (status IN ('applied', 'suggested', 'rejected')),
            created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (release_group_id, tag_id)
        );
        CREATE INDEX release_group_tag_tag_idx ON release_group_tag (tag_id);

        INSERT INTO tag (name) VALUES
          ('study'), ('workout'), ('cooking'), ('with-people'), ('rainy'), ('morning');
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS release_group_tag; DROP TABLE IF EXISTS tag;")
