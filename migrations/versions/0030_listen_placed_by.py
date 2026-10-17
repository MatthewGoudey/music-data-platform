"""listen.placed_by: 'run' when derive placed the listen by the whole sitting it belongs to

Revision ID: 0030
Revises: 0029
Create Date: 2026-10-17

ADR 0015 amendment 2026-10-17 (derive/runs.py). A sitting played under one reported album goes
to the group whose tracklist covers it; the per-listen rules (reported album, stray-track
redirect, leaving a compilation) skip a listen so placed, so no two rules trade it back and forth.
"""

from __future__ import annotations

from alembic import op

revision = "0030"
down_revision = "0029"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE listen ADD COLUMN placed_by TEXT CHECK (placed_by IN ('run'));
        CREATE INDEX listen_placed_by_idx ON listen (placed_by) WHERE placed_by IS NOT NULL;
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP INDEX IF EXISTS listen_placed_by_idx; ALTER TABLE listen DROP COLUMN placed_by;"
    )
