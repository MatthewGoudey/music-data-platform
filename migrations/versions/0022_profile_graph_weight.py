"""queue_profile.graph_weight and composition.thread: the graph's two queue settings

Revision ID: 0022
Revises: 0021
Create Date: 2026-10-10

docs/graph/COMPANION_SPEC.md 5.2–5.3 (Phase 6 Block H; numbered 0022 because Block H now comes
before Block F, spec change 2026-10-10): graph_affinity scales a candidate by up to
×(1 + graph_weight) — default 0.3, home-genre 0.5, canon and popular 0 — and the default and
home-genre profiles gain one thread slot.
"""

from __future__ import annotations

from alembic import op

revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE queue_profile ADD COLUMN graph_weight NUMERIC NOT NULL DEFAULT 0;
        UPDATE queue_profile SET graph_weight = 0.3 WHERE name = 'default';
        UPDATE queue_profile SET graph_weight = 0.5 WHERE name = 'home-genre';
        UPDATE queue_profile SET composition = composition || '{"thread": 1}'::jsonb
         WHERE name IN ('default', 'home-genre');
        """
    )


def downgrade() -> None:
    op.execute(
        """
        UPDATE queue_profile SET composition = composition - 'thread';
        ALTER TABLE queue_profile DROP COLUMN graph_weight;
        """
    )
