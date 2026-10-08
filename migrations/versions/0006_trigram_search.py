"""trigram search: pg_trgm and GIN indexes on artist and release-group keys

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-08

POST /artists/batch falls back to trigram similarity when a name matches no alias and
no key, and returns candidates instead of guessing.
"""

from __future__ import annotations

from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE EXTENSION IF NOT EXISTS pg_trgm;
        CREATE INDEX artist_norm_key_trgm_idx ON artist USING gin (norm_key gin_trgm_ops);
        CREATE INDEX release_group_norm_key_trgm_idx
            ON release_group USING gin (norm_key gin_trgm_ops);
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DROP INDEX IF EXISTS release_group_norm_key_trgm_idx;
        DROP INDEX IF EXISTS artist_norm_key_trgm_idx;
        """
    )
