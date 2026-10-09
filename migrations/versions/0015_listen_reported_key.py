"""listen.reported_key: the album key of the release name the player reported

Revision ID: 0015
Revises: 0014
Create Date: 2026-10-09

ADR 0015 amendment (2026-10-09): the album a listen belongs to is the one the player
reported, when that album has a home group. ListenBrainz maps each track on its own and
can file one album's tracks under a dozen compilations; derive moves them home by this
key. Ingest fills it for new listens; derive fills the rows this migration leaves NULL.
"""

from __future__ import annotations

from alembic import op

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE listen ADD COLUMN reported_key TEXT;
        CREATE INDEX listen_reported_key_idx ON listen (reported_key)
            WHERE reported_key IS NOT NULL;
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP INDEX IF EXISTS listen_reported_key_idx; ALTER TABLE listen DROP COLUMN reported_key;"
    )
