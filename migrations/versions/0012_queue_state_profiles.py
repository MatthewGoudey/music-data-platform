"""queue state and profiles: queue_state, queue_profile (seeded)

Revision ID: 0012
Revises: 0011
Create Date: 2026-10-09

docs/QUEUE_SPEC.md sections 4, 10 and 11. Queue state keys on release_group_id, so an
album keeps its pin, snooze or hide whichever list it appears on. The three starting
profiles are seeded here; `PUT /profiles/{name}` edits them.
"""

from __future__ import annotations

from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE queue_state (
            release_group_id  INTEGER PRIMARY KEY
                              REFERENCES release_group (release_group_id) ON DELETE CASCADE,
            pinned_at         TIMESTAMPTZ,
            bumped_until      TIMESTAMPTZ,
            snoozed_until     TIMESTAMPTZ,
            hidden_at         TIMESTAMPTZ,
            updated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
        );

        CREATE TABLE queue_profile (
            name             TEXT PRIMARY KEY,
            description      TEXT,
            filters          JSONB   NOT NULL DEFAULT '{}'::jsonb,
            goal_weights     JSONB   NOT NULL DEFAULT '{}'::jsonb,
            zone_weights     JSONB   NOT NULL DEFAULT '{}'::jsonb,
            composition      JSONB   NOT NULL DEFAULT '{}'::jsonb,
            affinity_weight  NUMERIC NOT NULL DEFAULT 0.2,
            updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
        );

        INSERT INTO queue_profile
               (name, description, filters, goal_weights, zone_weights, composition, affinity_weight)
        VALUES
          ('default', 'Every accepted list',
           '{}', '{"canon": 1, "depth": 1, "breadth": 1}', '{}',
           '{"n": 10, "revisit_share": 0.2, "wildcard": 1}', 0.2),
          ('home-genre', 'The V Atlas: indie twang, slacker rock and their ancestors',
           '{"lists": ["v_atlas"]}', '{"depth": 1}', '{"Core": 1.5, "V": 1.0, "Context": 0.6}',
           '{"n": 10, "revisit_share": 0.2, "wildcard": 1}', 0.2),
          ('canon', 'The canon lists only',
           '{"goals": ["canon"]}', '{"canon": 1}', '{}',
           '{"n": 10, "revisit_share": 0.1, "wildcard": 1}', 0.0);
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS queue_profile; DROP TABLE IF EXISTS queue_state;")
