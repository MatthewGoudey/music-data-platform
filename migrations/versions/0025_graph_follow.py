"""graph_follow: the people, bands, labels and studios Matt follows

Revision ID: 0025
Revises: 0024
Create Date: 2026-10-10

docs/graph/COMPANION_SPEC.md 5.5 (Block E2; numbered in build order, spec change 2026-10-10).
"""

from __future__ import annotations

from alembic import op

revision = "0025"
down_revision = "0024"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE graph_follow (
            entity_id   BIGINT PRIMARY KEY REFERENCES entity (entity_id) ON DELETE CASCADE,
            followed_at TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS graph_follow;")
