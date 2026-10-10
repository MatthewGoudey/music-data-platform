"""the walk-back profile includes heard albums

Revision ID: 0027
Revises: 0026
Create Date: 2026-10-10

Matt: "The walk back should not be limited just to new albums, it should be a mix of both heard
and unheard". composition.include_heard lets a profile's new slots take heard albums; only
walk-back sets it (queue spec v18).
"""

from __future__ import annotations

from alembic import op

revision = "0027"
down_revision = "0026"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """UPDATE queue_profile SET composition = composition || '{"include_heard": true}'::jsonb
            WHERE name = 'walk-back'"""
    )


def downgrade() -> None:
    op.execute(
        "UPDATE queue_profile SET composition = composition - 'include_heard' WHERE name = 'walk-back'"
    )
