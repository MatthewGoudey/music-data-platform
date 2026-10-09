"""popular profile: the Billboard 200 list on its own

Revision ID: 0017
Revises: 0016
Create Date: 2026-10-09

QUEUE_SPEC v12. Matt (26) added the Billboard 200 to hear what was popular, not only what
critics praised: albums the people he meets would know. The `popular` profile draws from
that list alone; the list (goal breadth) also counts in `default`.
"""

from __future__ import annotations

from alembic import op

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        INSERT INTO queue_profile
               (name, description, filters, goal_weights, zone_weights, composition, affinity_weight)
        VALUES ('popular', 'Billboard 200: albums people your age would know',
                '{"lists": ["billboard_200"]}', '{"breadth": 1}', '{}',
                '{"n": 10, "revisit_share": 0.2, "wildcard": 1}', 0.2)
        ON CONFLICT (name) DO NOTHING;
        """
    )


def downgrade() -> None:
    op.execute("DELETE FROM queue_profile WHERE name = 'popular';")
