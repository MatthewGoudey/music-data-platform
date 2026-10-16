"""genre of the week: the week's pick, its generated list and the profile that reads it

Revision ID: 0029
Revises: 0028
Create Date: 2026-10-16

docs/QUEUE_SPEC.md section 10a (v23). One row per week (Monday, Chicago time): the genre key and
name, the kind of pick (near, adjacent, stretch), who picked it, the numbers behind it, and the
genres rerolled away that week. The `genre_week` list is generated, so check L1 and `lists load`
leave it alone, and only the `genre-week` profile reads it.
"""

from __future__ import annotations

from alembic import op

revision = "0029"
down_revision = "0028"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE genre_week (
            week_start  DATE PRIMARY KEY,
            genre       TEXT NOT NULL,            -- the key (queue/genres.py genre_key)
            name        TEXT NOT NULL,            -- the spelling shown
            kind        TEXT NOT NULL CHECK (kind IN ('near', 'adjacent', 'stretch')),
            picked_by   TEXT NOT NULL CHECK (picked_by IN ('auto', 'reroll')),
            picked_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
            why         JSONB NOT NULL DEFAULT '{}'::jsonb,
            rerolled    TEXT[] NOT NULL DEFAULT '{}'
        );

        INSERT INTO list (slug, name, goal, weight, ranked, source, file_rows)
        VALUES ('genre_week', 'Genre of the week', 'breadth', 1.0, true, 'generated:queue', NULL)
        ON CONFLICT (slug) DO NOTHING;

        INSERT INTO queue_profile
               (name, description, filters, goal_weights, zone_weights, composition,
                affinity_weight, graph_weight)
        VALUES ('genre-week', 'This week''s genre: list albums that carry it',
                '{"lists": ["genre_week"]}', '{"breadth": 1}', '{}',
                '{"n": 10, "revisit_share": 0, "wildcard": 0, "thread": 0}', 0.1, 0)
        ON CONFLICT (name) DO NOTHING;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DELETE FROM queue_profile WHERE name = 'genre-week';
        DELETE FROM list WHERE slug = 'genre_week';
        DROP TABLE genre_week;
        """
    )
