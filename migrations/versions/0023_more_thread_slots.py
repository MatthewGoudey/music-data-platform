"""three thread slots in the default and home-genre profiles

Revision ID: 0023
Revises: 0022
Create Date: 2026-10-10

docs/QUEUE_SPEC.md v17 (Matt: "Can you add more threads and make the shuffle work on them?"):
composition.thread goes from 1 to 3, one per finished album in turn.
"""

from __future__ import annotations

from alembic import op

revision = "0023"
down_revision = "0022"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """UPDATE queue_profile SET composition = composition || '{"thread": 3}'::jsonb
            WHERE name IN ('default', 'home-genre')"""
    )


def downgrade() -> None:
    op.execute(
        """UPDATE queue_profile SET composition = composition || '{"thread": 1}'::jsonb
            WHERE name IN ('default', 'home-genre')"""
    )
