"""the generated lists `following` and `graph_walk`, and the profiles that read them

Revision ID: 0026
Revises: 0025
Create Date: 2026-10-10

docs/graph/COMPANION_SPEC.md 5.4–5.6 and graph spec section 11 (Block E2). Generated lists carry
`source = 'generated:graph'` and no `file_rows`, so check L1 and `lists load` leave them alone;
the queue reads one only under a profile whose filters name it, so `default` and the other list
profiles score exactly as before.
"""

from __future__ import annotations

from alembic import op

revision = "0026"
down_revision = "0025"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        INSERT INTO list (slug, name, goal, weight, ranked, source, file_rows)
        VALUES ('following', 'Following', 'depth', 1.0, true, 'generated:graph', NULL),
               ('graph_walk', 'Walk back', 'depth', 1.0, true, 'generated:graph', NULL)
        ON CONFLICT (slug) DO NOTHING;

        INSERT INTO queue_profile
               (name, description, filters, goal_weights, zone_weights, composition,
                affinity_weight, graph_weight)
        VALUES ('following', 'Records by the people, bands, labels and studios you follow',
                '{"lists": ["following"]}', '{"depth": 1}', '{}',
                '{"n": 10, "revisit_share": 0, "wildcard": 0, "thread": 0}', 0.1, 0),
               ('walk-back', 'Where an album comes from: its sources and its people''s earlier records',
                '{"lists": ["graph_walk"]}', '{"depth": 1}', '{}',
                '{"n": 10, "revisit_share": 0, "wildcard": 0, "thread": 0}', 0.1, 0)
        ON CONFLICT (name) DO NOTHING;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DELETE FROM queue_profile WHERE name IN ('following', 'walk-back');
        DELETE FROM list WHERE slug IN ('following', 'graph_walk');
        """
    )
