"""graph_connection and graph_thread: the computed connection tables

Revision ID: 0021
Revises: 0020
Create Date: 2026-10-10

docs/graph/COMPANION_SPEC.md sections 4 and 10 (Phase 6 Block D): for each candidate album, its
connection score C(g) and its three strongest connecting nodes; for each recently finished album,
its 20 strongest connected unheard albums. Both are rewritten by jobs (`graph connections`,
`graph threads`); nothing else writes them.
"""

from __future__ import annotations

from alembic import op

revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE graph_connection (
            release_group_id INTEGER PRIMARY KEY
                             REFERENCES release_group (release_group_id) ON DELETE CASCADE,
            score            NUMERIC NOT NULL,      -- C(g)
            top              JSONB   NOT NULL,      -- up to three: kind, node, text, heard album, weight, assertion ids
            computed_at      TIMESTAMPTZ NOT NULL DEFAULT now()
        );

        CREATE TABLE graph_thread (
            from_release_group_id INTEGER NOT NULL
                                  REFERENCES release_group (release_group_id) ON DELETE CASCADE,
            to_release_group_id   INTEGER NOT NULL
                                  REFERENCES release_group (release_group_id) ON DELETE CASCADE,
            rank                  INTEGER NOT NULL,
            connection            JSONB   NOT NULL,
            computed_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (from_release_group_id, to_release_group_id)
        );
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS graph_thread, graph_connection;")
