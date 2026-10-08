"""verdict: again / later / never per release group, after a session

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-08

Each verdict is a row; the latest per release group is the current one. It points at
its session by start time, not session_id, because derive replaces scrobbled session
rows (and their IDs) whenever it rebuilds a group.
"""

from __future__ import annotations

from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE verdict (
            verdict_id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            release_group_id    INTEGER     NOT NULL
                                REFERENCES release_group (release_group_id) ON DELETE CASCADE,
            verdict             TEXT        NOT NULL CHECK (verdict IN ('again', 'later', 'never')),
            rating              SMALLINT    CHECK (rating BETWEEN 1 AND 5),
            note                TEXT        CHECK (length(note) <= 500),
            session_started_at  TIMESTAMPTZ,
            created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        CREATE INDEX verdict_release_group_idx ON verdict (release_group_id, created_at DESC);
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS verdict;")
